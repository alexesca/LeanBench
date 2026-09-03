"""Retrieval scoring — TASKS.md §4.1, implemented exactly. Pure: no I/O, no clock,
no randomness, no mocks needed to test it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from leanbench.scoring.normalize import match_gold_symbol, normalize_path, normalize_symbol

#: A gold-comparable identifier extracted from one candidate result.
#: `symbol` is None for a file-level hit.


@dataclass(frozen=True)
class ResultItem:
    """One ranked candidate result reduced to its gold-comparable parts."""

    symbol: str | None = None
    path: str | None = None

    @property
    def identifier(self) -> str:
        """TASKS.md §4.1: symbol if non-null, else path."""
        return normalize_symbol(self.symbol) if self.symbol else normalize_path(self.path or "")


@dataclass(frozen=True)
class GoldSet:
    """Gold reduced to comparison-ready form."""

    symbols: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    #: Files known to contain a gold symbol (from gold.ranges), for file-level credit.
    symbol_files: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.files) | set(self.tests) | set(self.docs)))

    @property
    def universe(self) -> tuple[str, ...]:
        """All distinct gold items. The Recall denominator."""
        return tuple(sorted(set(self.symbols) | set(self.paths)))

    def is_empty(self) -> bool:
        return not self.universe


def build_gold_set(
    *,
    symbols: list[str],
    files: list[str],
    tests: list[str],
    docs: list[str],
    ranges: list[tuple[str, int, int]] | None = None,
) -> GoldSet:
    range_paths = sorted({normalize_path(p) for p, _s, _e in (ranges or [])})
    return GoldSet(
        symbols=tuple(sorted({normalize_symbol(s) for s in symbols if s})),
        files=tuple(sorted({normalize_path(p) for p in files if p})),
        tests=tuple(sorted({normalize_path(p) for p in tests if p})),
        docs=tuple(sorted({normalize_path(p) for p in docs if p})),
        symbol_files=tuple(range_paths),
    )


def matched_gold_items(item: ResultItem, gold: GoldSet) -> tuple[str, ...]:
    """Gold items this result satisfies. Empty tuple => irrelevant.

    - a symbol result matches gold symbols by suffix-qualified equality, and ALSO earns
      file-level credit when its path is a gold path;
    - a file-level result matches a gold path, or a file that contains a gold symbol.
    """
    hits: set[str] = set()
    path = normalize_path(item.path or "")
    if item.symbol:
        gold_symbol = match_gold_symbol(gold.symbols, item.symbol)
        if gold_symbol is not None:
            hits.add(gold_symbol)
    if path:
        if path in gold.paths:
            hits.add(path)
        elif not item.symbol and path in gold.symbol_files:
            # "A file-level hit on a file that contains a gold symbol counts as relevant."
            hits.add(path)
    return tuple(sorted(hits))


def is_relevant(item: ResultItem, gold: GoldSet) -> bool:
    return bool(matched_gold_items(item, gold))


def recall_at_k(results: list[ResultItem], gold: GoldSet, k: int) -> float:
    """Distinct gold items covered by the top-k results / total gold items."""
    universe = set(gold.universe)
    if not universe:
        return 0.0
    covered: set[str] = set()
    for item in results[:k]:
        covered.update(matched_gold_items(item, gold))
    return len(covered & universe) / len(universe)


def symbol_recall_at_k(results: list[ResultItem], gold: GoldSet, k: int) -> float:
    """Recall over `gold.symbols` only, and only symbol hits count (TASKS.md §4.1:
    "a symbol hit is required to score Recall@K on the symbols sub-metric")."""
    if not gold.symbols:
        return 0.0
    covered: set[str] = set()
    for item in results[:k]:
        if not item.symbol:
            continue
        gold_symbol = match_gold_symbol(gold.symbols, item.symbol)
        if gold_symbol is not None:
            covered.add(gold_symbol)
    return len(covered) / len(gold.symbols)


def precision_at_k(results: list[ResultItem], gold: GoldSet, k: int) -> float:
    """Relevant results among the top k, over k (standard IR denominator: returning
    fewer than k results does not raise precision)."""
    if k <= 0:
        return 0.0
    return sum(1 for item in results[:k] if is_relevant(item, gold)) / k


def mrr(results: list[ResultItem], gold: GoldSet) -> float:
    for rank, item in enumerate(results, start=1):
        if is_relevant(item, gold):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(results: list[ResultItem], gold: GoldSet, k: int) -> float:
    """Binary gain, log2 discount."""
    universe = gold.universe
    if not universe or k <= 0:
        return 0.0
    dcg = 0.0
    for rank, item in enumerate(results[:k], start=1):
        if is_relevant(item, gold):
            dcg += 1.0 / math.log2(rank + 1)
    ideal_hits = min(len(universe), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


@dataclass(frozen=True)
class ProbeScore:
    """All TASKS.md §4.1 per-probe numbers, as a flat metric map plus counts."""

    metrics: dict[str, float] = field(default_factory=dict)
    recall_at_k: dict[str, float] = field(default_factory=dict)
    precision_at_k: dict[str, float] = field(default_factory=dict)
    symbol_recall_at_k: dict[str, float] = field(default_factory=dict)
    results_returned: int = 0
    relevant_returned: int = 0


def score_probe(
    results: list[ResultItem],
    gold: GoldSet,
    *,
    recall_ks: tuple[int, ...],
    precision_ks: tuple[int, ...],
    ndcg_k: int,
) -> ProbeScore:
    recalls = {f"recall_at_{k}": recall_at_k(results, gold, k) for k in sorted(recall_ks)}
    precisions = {
        f"precision_at_{k}": precision_at_k(results, gold, k) for k in sorted(precision_ks)
    }
    symbol_recalls = {
        f"symbol_recall_at_{k}": symbol_recall_at_k(results, gold, k) for k in sorted(recall_ks)
    }
    flat: dict[str, float] = {}
    flat.update(recalls)
    flat.update(precisions)
    flat.update(symbol_recalls)
    flat["mrr"] = mrr(results, gold)
    flat[f"ndcg_at_{ndcg_k}"] = ndcg_at_k(results, gold, ndcg_k)
    return ProbeScore(
        metrics=dict(sorted(flat.items())),
        recall_at_k={str(k): recalls[f"recall_at_{k}"] for k in sorted(recall_ks)},
        precision_at_k={str(k): precisions[f"precision_at_{k}"] for k in sorted(precision_ks)},
        symbol_recall_at_k={
            str(k): symbol_recalls[f"symbol_recall_at_{k}"] for k in sorted(recall_ks)
        },
        results_returned=len(results),
        relevant_returned=sum(1 for item in results if is_relevant(item, gold)),
    )


#: TASKS.md §4.1 result-reduction rules, keyed by op.
def results_from_op(op: str, result: dict) -> list[ResultItem]:
    """Reduce an op `result` to a ranked list of gold-comparable items. Ordering is the
    candidate's; we never re-sort (that would hide a candidate's ranking quality)."""
    items: list[ResultItem] = []
    if op == "search":
        for hit in result.get("hits", []) or []:
            items.append(ResultItem(symbol=hit.get("symbol") or None, path=hit.get("path")))
    elif op == "get_symbol":
        for sym in result.get("symbols", []) or []:
            items.append(ResultItem(symbol=sym.get("symbol") or None, path=sym.get("path")))
    elif op == "get_context":
        if result.get("symbol"):
            items.append(ResultItem(symbol=result.get("symbol"), path=result.get("path")))
    elif op == "get_tests":
        for test in result.get("tests", []) or []:
            items.append(ResultItem(symbol=None, path=test.get("path")))
    elif op == "get_references":
        for ref in result.get("references", []) or []:
            items.append(ResultItem(symbol=ref.get("symbol") or None, path=ref.get("path")))
    elif op == "get_docs":
        for doc in result.get("docs", []) or []:
            items.append(ResultItem(symbol=None, path=doc.get("path")))
    return items
