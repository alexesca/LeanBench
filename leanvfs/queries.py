"""Protocol-level query operations, assembled from the canonical model.

Everything here returns plain dicts shaped by PROTOCOL.md §4. Budget admission happens
in `get_context`, which is the money operation and the one most responsible for the
benchmark's headline metric.
"""

from __future__ import annotations

from typing import Any

from .budget import BudgetItem, TokenCounter, admit, context_order, per_kind_caps
from .search import SearchEngine, split_identifier
from .store import Store
from .views import build_file_view, build_symbol_view


def _match_symbol(row: Any, name: str) -> bool:
    """Accept bare (`send`) and qualified (`Client.send`) forms, per PROTOCOL.md §4.3."""
    qualified = (row["qualified_name"] or "").lower()
    bare = (row["name"] or "").lower()
    want = name.lower()
    if want in (qualified, bare):
        return True
    return qualified.endswith("." + want)


class QueryEngine:
    def __init__(self, store: Store, cfg: Any) -> None:
        self.store = store
        self.cfg = cfg
        self.search_engine = SearchEngine(store, cfg)
        self.counter = TokenCounter(cfg)

    # -- search ------------------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        hits = self.search_engine.search(query, limit)
        return {
            "hits": [
                {
                    "path": h.path,
                    "symbol": h.symbol,
                    "kind": h.kind,
                    "line_start": h.line_start,
                    "line_end": h.line_end,
                    "score": h.score,
                }
                for h in hits
            ]
        }

    # -- symbols -----------------------------------------------------------------
    def _find_symbols(self, name: str, limit: int) -> list[Any]:
        rows = [r for r in self.store.symbols_by_name(name.split(".")[-1])
                if _match_symbol(r, name)]
        if not rows:
            rows = [r for r in self.store.all_symbols() if _match_symbol(r, name)]
        rows.sort(key=lambda r: (r["qualified_name"], r["stable_key"]))
        return rows[:limit]

    def get_symbol(self, name: str, limit: int = 10) -> dict[str, Any]:
        out = []
        for row in self._find_symbols(name, limit):
            out.append(
                {
                    "path": self._path_of(row),
                    "symbol": row["qualified_name"],
                    "kind": row["kind"],
                    "signature": row["signature"] or "",
                    "return_type": row["return_type"] or "",
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "visibility": row["visibility"],
                    "doc": (row["doc"] or "").strip().split("\n")[0][:200],
                }
            )
        return {"symbols": out}

    def _path_of(self, row: Any) -> str:
        frow = self.store.conn.execute(
            "SELECT path FROM files WHERE id=?", (row["file_id"],)
        ).fetchone()
        return frow["path"] if frow else ""

    # -- context: budget-assembled by policy -------------------------------------
    def get_context(self, symbol: str, token_budget: int | None = None) -> dict[str, Any]:
        rows = self._find_symbols(symbol, 1)
        if not rows:
            return {}
        row = rows[0]
        view = build_symbol_view(self.store, row, self.cfg)
        budget = token_budget if token_budget is not None else int(
            self.cfg.get("budget.context_tokens", 400)
        )

        # 1. Identity is unconditional — it is never subject to the budget.
        head = {
            "symbol": view.qualified_name,
            "path": self._path_of(row),
            "kind": view.kind,
            "line_start": view.line_start,
            "line_end": view.line_end,
            "signature": view.signature,
            "return_type": view.return_type,
            "visibility": view.visibility,
        }
        spent = self.counter.count(
            " ".join(str(v) for v in head.values() if v)
        )

        # 2. Everything else competes for the remainder, in the configured order.
        order = context_order(self.cfg)
        items: list[BudgetItem] = []
        for position, kind in enumerate(order):
            for value, confidence, _prov, priority in view.facts.get(kind, []):
                items.append(
                    BudgetItem(
                        kind=kind,
                        value=value,
                        priority=priority if priority is not None else position,
                        confidence=confidence,
                    )
                )
        if "keyword" in order:
            for term, score in view.keywords:
                items.append(
                    BudgetItem(
                        kind="keyword",
                        value=term,
                        priority=order.index("keyword"),
                        confidence=min(1.0, score),
                    )
                )

        admitted, report = admit(
            items,
            max(0, budget - spent),
            per_kind_caps(self.cfg),
            self.counter,
        )
        grouped: dict[str, list[str]] = {}
        for item in admitted:
            grouped.setdefault(item.kind, []).append(item.value)

        result = dict(head)
        for kind in order:
            if kind in grouped:
                result[kind] = sorted(set(grouped[kind]))
        # 3. Never the implementation body. The drop record is not optional.
        result["budget_report"] = report.to_dict()
        return result

    # -- dependencies -------------------------------------------------------------
    def get_dependencies(self, path: str) -> dict[str, Any]:
        view = build_file_view(self.store, path, self.cfg)
        if view is None:
            return {}
        imported_by: list[str] = []
        for row in self.store.conn.execute(
            "SELECT DISTINCT f.path AS path FROM imports i "
            "JOIN files f ON f.id = i.file_id WHERE i.resolved_path = ? ORDER BY f.path",
            (path,),
        ):
            imported_by.append(row["path"])
        return {
            "imports_local": sorted(view.imports_local),
            "imports_external": sorted(view.imports_ext),
            "imported_by": imported_by,
        }

    # -- references ---------------------------------------------------------------
    def get_references(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        kinds = ["REFERENCES", "USES_TYPE", "CALLS", "EXTENDS", "IMPLEMENTS", "TESTS"]
        out: list[dict[str, Any]] = []
        for row in self._find_symbols(symbol, 5):
            for ref in self.store.relationships_to(int(row["id"]), kinds):
                out.append(
                    {
                        "path": ref["source_path"],
                        "symbol": ref["source_qual"],
                        "line": ref["line"],
                        "kind": ref["kind"],
                        "confidence": round(float(ref["confidence"]), 4),
                    }
                )
        out.sort(key=lambda r: (-r["confidence"], r["path"], r["line"], r["symbol"]))
        return {"references": out[:limit]}

    # -- tests ---------------------------------------------------------------------
    def get_tests(self, symbol: str, limit: int = 25) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for row in self._find_symbols(symbol, 5):
            for ref in self.store.relationships_to(int(row["id"]), ["TESTS", "COVERS"]):
                view_row = self.store.symbol_by_id(int(ref["source_symbol_id"] or 0))
                scenario = ""
                if view_row is not None:
                    sv = build_symbol_view(self.store, view_row, self.cfg)
                    values = sv.values("test_expectation") or sv.values("test_case")
                    scenario = values[0] if values else (sv.doc or "").split("\n")[0]
                out.append(
                    {
                        "path": ref["source_path"],
                        "symbol": ref["source_qual"],
                        "line_start": ref["line"],
                        "scenario": scenario[:200],
                        "expects": "",
                    }
                )
        # Tests found only lexically still beat nothing, but rank below real edges.
        if not out:
            bare = symbol.split(".")[-1]
            for hit in self.search_engine.search(bare, 40):
                if hit.symbol and hit.path.startswith(("tests/", "test/")):
                    out.append(
                        {
                            "path": hit.path,
                            "symbol": hit.symbol,
                            "line_start": hit.line_start,
                            "scenario": "",
                            "expects": "",
                        }
                    )
        out.sort(key=lambda r: (r["path"], r["line_start"], r["symbol"]))
        return {"tests": out[:limit]}

    # -- docs ------------------------------------------------------------------------
    def get_docs(self, query: str, limit: int = 5) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for hit in self.search_engine.search(query, limit * 8):
            frow = self.store.file_by_path(hit.path)
            if frow is None:
                continue
            if frow["file_class"] not in ("documentation", "architecture"):
                continue
            view = build_file_view(self.store, hit.path, self.cfg)
            heading = ""
            excerpt = ""
            if view is not None:
                headings = view.values("documentation")
                heading = headings[0] if headings else ""
                purpose = view.values("purpose")
                excerpt = purpose[0] if purpose else heading
            out.append(
                {
                    "path": hit.path,
                    "heading": heading[:120],
                    "line_start": hit.line_start,
                    "excerpt": excerpt[:300],
                }
            )
            if len(out) >= limit:
                break
        return {"docs": out}

    # -- stats -------------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        from .resolve import tier_rates

        counters = self.store.counters()
        tiers = {
            tier: counters.get(f"relationships_by_tier_{tier}", 0)
            for tier in ("R0", "R1", "R2", "R3", "R4")
        }
        return {
            "files": self.store.count("files"),
            "symbols": self.store.count("symbols"),
            "facts": self.store.count("facts"),
            "relationships": self.store.count("relationships"),
            "index_bytes": self.store.size_bytes(),
            "source_bytes": int(self.store.get_meta("source_bytes", "0") or 0),
            "cold_index_ms": float(self.store.get_meta("cold_index_ms", "0") or 0.0),
            "resolution_rate": tier_rates(tiers),
            "generation": self.store.generation(),
            "idf_generation": self.store.idf_generation(),
            "index_state": self.store.index_state(),
            "counters": counters,
            "durations": self.store.durations(),
        }
