"""Keyword scoring against a **versioned frozen IDF snapshot**.

The latent bug this module exists to avoid: repository-wide IDF is a global statistic,
and reading a live global statistic during an incremental update destroys both
"incremental converges to clean" and "bounded work per edit".

Resolution: IDF is computed only during a full `sync`, frozen under an
`idf_generation`, and thereafter READ ONLY. :func:`compute_idf` refuses to run unless
the caller holds a :class:`GlobalStatsPermit`, which only the full-sync path creates.
That is the enforcement mechanism behind the architectural test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .model import KeywordCandidate, ScoredKeyword


class GlobalStatisticViolation(RuntimeError):
    """Raised when a corpus-wide statistic is computed outside a full sync."""


class GlobalStatsPermit:
    """A capability token. Only :meth:`Indexer.full_sync` mints one."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass(frozen=True)
class IdfSnapshot:
    """A frozen, generation-stamped view of corpus-wide term statistics."""

    generation: int
    doc_count: int
    values: dict[str, float] = field(default_factory=dict)
    default: float = 1.0

    def idf(self, term: str) -> float:
        return self.values.get(term, self.default)


def normalize_terms(raw_terms: Iterable[str], cfg: Any) -> list[str]:
    lowercase = bool(cfg.get("keywords.lowercase", True))
    min_len = int(cfg.get("keywords.min_term_length", 3))
    max_len = int(cfg.get("keywords.max_term_length", 40))
    stop = set(cfg.get("keywords.stoplist", []) or [])
    lang = set(cfg.get("keywords.language_keywords", []) or [])
    out: list[str] = []
    for term in raw_terms:
        t = term.lower() if lowercase else term
        t = t.strip("_-.:")
        if not t or t.isdigit():
            continue
        if not (min_len <= len(t) <= max_len):
            continue
        if t in stop or t in lang:
            continue
        out.append(t)
    return out


def compute_idf(
    doc_term_sets: Sequence[set[str]],
    permit: GlobalStatsPermit | None,
) -> tuple[list[tuple[str, int, float]], int]:
    """Corpus-wide IDF. Requires a permit; incremental updates cannot obtain one."""
    if not isinstance(permit, GlobalStatsPermit):
        raise GlobalStatisticViolation(
            "compute_idf() requires a GlobalStatsPermit; incremental updates must read "
            "the frozen snapshot instead"
        )
    n = len(doc_term_sets)
    df: dict[str, int] = {}
    for terms in doc_term_sets:
        for term in terms:
            df[term] = df.get(term, 0) + 1
    entries = [
        (term, count, math.log(1.0 + (n + 1.0) / (count + 0.5)))
        for term, count in sorted(df.items())
    ]
    return entries, n


def default_idf(doc_count: int) -> float:
    return math.log(1.0 + (doc_count + 1.0) / 0.5)


def score(
    candidates: Sequence[KeywordCandidate],
    snapshot: IdfSnapshot,
    cfg: Any,
) -> list[ScoredKeyword]:
    """score = structural_weight(source) x idf(term, idf_generation). Ties: term asc."""
    weights: dict[str, float] = {
        k: float(v) for k, v in (cfg.section("keywords.structural_weights") or {}).items()
    }
    max_sym = int(cfg.get("keywords.max_per_symbol", 8))
    max_file = int(cfg.get("keywords.max_per_file", 15))

    best: dict[tuple[str | None, str], tuple[float, str]] = {}
    for cand in candidates:
        w = weights.get(cand.source, 1.0)
        s = w * snapshot.idf(cand.term)
        key = (cand.symbol_key, cand.term)
        prev = best.get(key)
        if prev is None or s > prev[0] or (s == prev[0] and cand.source < prev[1]):
            best[key] = (s, cand.source)

    grouped: dict[str | None, list[ScoredKeyword]] = {}
    file_path = candidates[0].file_path if candidates else ""
    for (symbol_key, term), (s, source) in best.items():
        grouped.setdefault(symbol_key, []).append(
            ScoredKeyword(term=term, score=s, source=source, symbol_key=symbol_key,
                          file_path=file_path)
        )

    out: list[ScoredKeyword] = []
    for symbol_key in sorted(grouped, key=lambda k: (k is not None, k or "")):
        items = grouped[symbol_key]
        items.sort(key=ScoredKeyword.sort_key)
        limit = max_file if symbol_key is None else max_sym
        out.extend(items[:limit])
    return out


def file_level_candidates(candidates: Sequence[KeywordCandidate]) -> list[KeywordCandidate]:
    """Every symbol-scoped candidate also contributes to the file-level pool."""
    return [
        KeywordCandidate(term=c.term, source=c.source, symbol_key=None, file_path=c.file_path)
        for c in candidates
    ]


def drift(doc_count_at_snapshot: int, files_added: int, files_removed: int) -> float:
    if doc_count_at_snapshot <= 0:
        return 1.0 if (files_added or files_removed) else 0.0
    return (files_added + files_removed) / float(doc_count_at_snapshot)
