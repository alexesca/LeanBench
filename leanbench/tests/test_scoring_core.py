"""Unit tests for the functional core: pure functions, no fixtures, no I/O, no mocks."""

from __future__ import annotations

import pytest
from leanbench.scoring.aggregate import (
    context_efficiency,
    effective_context_efficiency,
)
from leanbench.scoring.compare import compare_runs, paired_permutation_test
from leanbench.scoring.normalize import normalize_path, normalize_symbol, symbol_matches
from leanbench.scoring.retrieval import (
    ResultItem,
    build_gold_set,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from leanbench.scoring.task_rules import (
    discrimination_index,
    informative_fraction,
    is_informative,
    triage_flags,
)


# --- normalization (TASKS.md §4.1) ---------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("./a/b.py", "a/b.py"), ("a\\b.py", "a/b.py"), ("a/b.py", "a/b.py")],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


def test_suffix_qualified_symbol_matching() -> None:
    """Gold `Client.send` must match a module-prefixed candidate answer, but a bare
    `send` must not — that is the difference between tolerating module prefixes and
    accepting a name collision."""
    assert symbol_matches("Client.send", "httpx._client.Client.send")
    assert symbol_matches("Client.send", "Client.send")
    assert not symbol_matches("Client.send", "send")
    assert not symbol_matches("Client.send", "Other.send")


def test_bare_gold_symbol_matches_bare_candidate() -> None:
    assert symbol_matches("send", "send")
    assert symbol_matches("send", "mod.Client.send")


def test_normalize_symbol_is_idempotent() -> None:
    once = normalize_symbol(" Client.send ")
    assert normalize_symbol(once) == once


# --- retrieval metrics ----------------------------------------------------------------
def _gold(**kw):
    base = {"symbols": [], "files": [], "tests": [], "docs": [], "ranges": []}
    base.update(kw)
    return build_gold_set(**base)


def test_recall_counts_distinct_gold_items_not_hits() -> None:
    gold = _gold(files=["a.py", "b.py"])
    results = [ResultItem(path="a.py", symbol=None)] * 5
    assert recall_at_k(results, gold, 10) == 0.5


def test_ndcg_never_exceeds_one_when_hits_are_redundant() -> None:
    """Regression guard for the bound violation: many hits, one gold item."""
    gold = _gold(files=["a.py"])
    results = [ResultItem(path="a.py", symbol=f"S{i}") for i in range(10)]
    assert ndcg_at_k(results, gold, 10) == pytest.approx(1.0)


def test_ndcg_rewards_ranking_gold_earlier() -> None:
    gold = _gold(files=["a.py", "b.py"])
    early = [ResultItem(path="a.py"), ResultItem(path="b.py"), ResultItem(path="z.py")]
    late = [ResultItem(path="z.py"), ResultItem(path="y.py"), ResultItem(path="a.py")]
    assert ndcg_at_k(early, gold, 10) > ndcg_at_k(late, gold, 10)


def test_ndcg_is_zero_without_any_relevant_result() -> None:
    assert ndcg_at_k([ResultItem(path="z.py")], _gold(files=["a.py"]), 10) == 0.0


def test_precision_denominator_is_k_not_results_returned() -> None:
    """Returning one correct result out of one must not read as perfect precision@10."""
    gold = _gold(files=["a.py"])
    assert precision_at_k([ResultItem(path="a.py")], gold, 10) == pytest.approx(0.1)


def test_mrr_is_reciprocal_of_first_relevant_rank() -> None:
    gold = _gold(files=["b.py"])
    results = [ResultItem(path="a.py"), ResultItem(path="b.py")]
    assert mrr(results, gold) == pytest.approx(0.5)


def test_empty_results_score_zero_everywhere() -> None:
    gold = _gold(files=["a.py"])
    assert recall_at_k([], gold, 10) == 0.0
    assert ndcg_at_k([], gold, 10) == 0.0
    assert mrr([], gold) == 0.0


# --- the signature metric's anti-gaming rules (ADR-009) --------------------------------
def test_context_efficiency_rewards_fewer_tokens() -> None:
    assert context_efficiency(1000, 100) > context_efficiency(1000, 500)


def test_a_candidate_that_answers_nothing_scores_zero_not_infinity() -> None:
    """The failing-fast defence: perfect token efficiency at zero correctness is worth
    nothing, not everything."""
    assert effective_context_efficiency(0.0, 1.0) == 0.0
    assert effective_context_efficiency(1.0, 0.8) == pytest.approx(0.8)


# --- task quality ----------------------------------------------------------------------
def test_ceiling_and_floor_tasks_are_uninformative() -> None:
    thresholds = {"ceiling_threshold": 0.95, "floor_threshold": 0.05, "unstable_cv": 0.2}
    ceiling = triage_flags({"raw": 0.99, "minast": 1.0}, **thresholds)
    floor = triage_flags({"raw": 0.0, "minast": 0.01}, **thresholds)
    good = triage_flags({"raw": 0.2, "minast": 0.8}, **thresholds)
    assert "ceiling" in ceiling and not is_informative(ceiling)
    assert "floor" in floor and not is_informative(floor)
    assert good == [] and is_informative(good)


def test_discrimination_index_is_the_baseline_spread() -> None:
    assert discrimination_index({"a": 0.2, "b": 0.9, "c": 0.5}) == pytest.approx(0.7)


def test_informative_fraction() -> None:
    assert informative_fraction({"t1": [], "t2": ["ceiling"], "t3": [], "t4": []}) == 0.75


# --- statistics (ADR-005) ----------------------------------------------------------------
def test_a_clear_effect_is_detected() -> None:
    a = [0.1] * 12
    b = [0.9] * 12
    p, _exact = paired_permutation_test(a, b)
    assert p < 0.05


def test_no_effect_is_not_detected() -> None:
    values = [0.4, 0.5, 0.6, 0.55, 0.45, 0.5, 0.52, 0.48, 0.51, 0.49, 0.5, 0.5]
    p, _exact = paired_permutation_test(values, list(values))
    assert p > 0.05


def test_cross_tokenizer_comparison_is_refused_not_warned() -> None:
    result = compare_runs(
        {"t": 1.0}, {"t": 2.0}, tokenizer_a="approximate:4", tokenizer_b="o200k_base"
    )
    assert not result.comparable
    assert "tokenizer" in result.reason


def test_degraded_run_blocks_conclusions() -> None:
    result = compare_runs({"t": 1.0}, {"t": 2.0}, degraded=True)
    assert not result.comparable
    assert "DEGRADED" in result.reason


def test_comparison_uses_only_shared_tasks() -> None:
    result = compare_runs({"a": 1.0, "b": 2.0}, {"b": 3.0, "c": 4.0})
    assert result.n == 1
