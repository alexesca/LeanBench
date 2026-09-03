"""Query engine: FTS5 retrieval plus deterministic re-ranking.

Ranking weights are all config (§20 tunables). Ties break on `stable_key` ascending and
never on rowid, which is insertion-dependent and would make results depend on indexing
order — a silent violation of the determinism invariant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .store import Store

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def split_identifier(term: str) -> list[str]:
    """camelCase / PascalCase / snake_case / kebab-case -> constituent terms."""
    parts: list[str] = []
    for chunk in re.split(r"[_\-.]+", term):
        if not chunk:
            continue
        parts.extend(
            p for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk) if p
        )
    return parts


def query_terms(query: str) -> list[str]:
    """Terms to match on: the literal words plus their sub-identifier expansions.

    Expanding `follow_redirects` into `follow` and `redirects` is what lets an
    intent-phrased probe reach a symbol whose name it does not literally contain.
    """
    seen: dict[str, None] = {}
    for raw in _WORD.findall(query):
        low = raw.lower()
        seen.setdefault(low, None)
        for part in split_identifier(raw):
            seen.setdefault(part.lower(), None)
    return list(seen)


def fts_match_expression(query: str) -> str:
    """An OR-of-prefix FTS5 expression. Quoted so punctuation can never be read as
    FTS syntax — an unquoted user query is both a correctness and an injection bug."""
    terms = query_terms(query)
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"*' for t in terms)


@dataclass
class Hit:
    path: str
    symbol: str | None
    kind: str
    line_start: int
    line_end: int
    score: float
    doc_kind: str
    doc_id: int
    stable_key: str = ""
    snippet: str = ""
    matched: list[str] = field(default_factory=list)

    def sort_key(self) -> tuple:
        # Descending score, then a total order that cannot depend on insertion.
        return (-self.score, self.path, self.stable_key, self.doc_kind, self.doc_id)


class SearchEngine:
    def __init__(self, store: Store, cfg: Any) -> None:
        self.store = store
        self.cfg = cfg
        w = cfg.section("search.weights") or {}
        self.w = {k: float(v) for k, v in w.items()}
        self.candidates = int(cfg.get("search.fts_candidates", 400))
        self.limit_max = int(cfg.get("search.limit_max", 100))

    def _weight(self, key: str, default: float = 1.0) -> float:
        return self.w.get(key, default)

    def search(self, query: str, limit: int = 10) -> list[Hit]:
        limit = max(1, min(int(limit), self.limit_max))
        terms = query_terms(query)
        termset = set(terms)
        rows = self.store.fts_search(fts_match_expression(query), self.candidates)

        hits: list[Hit] = []
        for row in rows:
            # bm25() returns a negative number where more-negative is better.
            base = -float(row["rank"]) * self._weight("bm25", 1.0)
            score = base
            matched: list[str] = []

            symbol = row["symbol"] or ""
            qualified = row["qualified"] or ""
            path = row["path"] or ""

            if symbol:
                sym_low = symbol.lower()
                if sym_low in termset:
                    score += self._weight("exact_symbol", 4.0)
                    matched.append("exact_symbol")
                else:
                    parts = {p.lower() for p in split_identifier(symbol)}
                    overlap = len(parts & termset)
                    if overlap:
                        score += self._weight("exact_symbol", 4.0) * (
                            overlap / max(len(parts), 1)
                        )
                        matched.append("symbol_parts")
            if qualified and any(t in qualified.lower() for t in termset):
                score += self._weight("qualified_symbol", 3.0) * 0.5
                matched.append("qualified")

            path_parts = {p.lower() for p in split_identifier(path.replace("/", "_"))}
            path_overlap = len(path_parts & termset)
            if path_overlap:
                score += self._weight("path", 1.2) * (path_overlap / max(len(path_parts), 1))
                matched.append("path")

            keywords = (row["keywords"] or "").split()
            kw_overlap = len(set(keywords) & termset)
            if kw_overlap:
                score += self._weight("keyword", 1.0) * kw_overlap
                matched.append("keyword")

            klass = row["klass"] or ""
            if klass.startswith("test"):
                score *= self._weight("class_tests", 0.8)
            elif klass in ("documentation", "architecture"):
                score *= self._weight("class_docs", 0.7)
            else:
                score *= self._weight("class_source", 1.0)

            info = self._locate(row)
            hits.append(
                Hit(
                    path=path,
                    symbol=info.get("symbol"),
                    kind=info.get("kind", "file"),
                    line_start=int(info.get("line_start", 0)),
                    line_end=int(info.get("line_end", 0)),
                    score=round(score, 6),
                    doc_kind=row["doc_kind"],
                    doc_id=int(row["doc_id"]),
                    stable_key=info.get("stable_key", ""),
                    matched=sorted(set(matched)),
                )
            )

        hits.sort(key=Hit.sort_key)
        return hits[:limit]

    def _locate(self, row: Any) -> dict[str, Any]:
        if row["doc_kind"] == "symbol":
            sym = self.store.symbol_by_id(int(row["doc_id"]))
            if sym is not None:
                return {
                    "symbol": sym["qualified_name"],
                    "kind": sym["kind"],
                    "line_start": sym["line_start"],
                    "line_end": sym["line_end"],
                    "stable_key": sym["stable_key"],
                }
        return {"symbol": None, "kind": "file", "line_start": 0, "line_end": 0,
                "stable_key": ""}
