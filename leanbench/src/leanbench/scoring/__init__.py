"""The functional core. PURE: no I/O, no clock, no randomness, no network, no logging.

Enforced by tests/test_architecture.py — this package may import only the stdlib and
`leanbench.schemas`.
"""

from leanbench.scoring.aggregate import (
    aggregate_probes,
    aggregate_tasks,
    brittleness,
    context_efficiency,
    effective_context_efficiency,
)
from leanbench.scoring.compare import ComparisonResult, compare_runs, paired_permutation_test
from leanbench.scoring.normalize import (
    match_gold_symbol,
    normalize_path,
    normalize_symbol,
    symbol_matches,
)
from leanbench.scoring.retrieval import (
    GoldSet,
    ProbeScore,
    ResultItem,
    build_gold_set,
    is_relevant,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    results_from_op,
    score_probe,
    symbol_recall_at_k,
)
from leanbench.scoring.task_rules import (
    discrimination_index,
    has_errors,
    informative_fraction,
    is_informative,
    triage_flags,
    validate_task,
)
from leanbench.scoring.tokens import (
    CrossTokenizerComparison,
    assert_same_tokenizer,
    counted_in_metric,
    cumulative_by_task,
    label,
    per_tool_totals,
    total_prompt_tokens,
    total_repository_tokens,
)

__all__ = [
    "ComparisonResult",
    "CrossTokenizerComparison",
    "GoldSet",
    "ProbeScore",
    "ResultItem",
    "aggregate_probes",
    "aggregate_tasks",
    "assert_same_tokenizer",
    "brittleness",
    "build_gold_set",
    "compare_runs",
    "context_efficiency",
    "counted_in_metric",
    "cumulative_by_task",
    "discrimination_index",
    "effective_context_efficiency",
    "has_errors",
    "informative_fraction",
    "is_informative",
    "is_relevant",
    "label",
    "match_gold_symbol",
    "mrr",
    "ndcg_at_k",
    "normalize_path",
    "normalize_symbol",
    "paired_permutation_test",
    "per_tool_totals",
    "precision_at_k",
    "recall_at_k",
    "results_from_op",
    "score_probe",
    "symbol_matches",
    "symbol_recall_at_k",
    "total_prompt_tokens",
    "total_repository_tokens",
    "triage_flags",
    "validate_task",
]
