"""Statistical comparison of two runs. Pure, deterministic: the permutation test walks
a fixed enumeration (or a deterministic strided subsample), never an RNG.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

MAX_EXACT_PAIRS = 18  # 2**18 sign flips is ~260k evaluations: fast and exact.
SUBSAMPLE_STRIDE_TARGET = 20000


@dataclass(frozen=True)
class ComparisonResult:
    n: int
    mean_a: float
    mean_b: float
    delta: float
    p_value: float
    exact: bool
    comparable: bool
    reason: str = ""


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def paired_permutation_test(a: Sequence[float], b: Sequence[float]) -> tuple[float, bool]:
    """Two-sided paired sign-flip permutation test on the per-task differences.

    Exact enumeration up to MAX_EXACT_PAIRS items; beyond that a deterministic strided
    subsample of the sign-flip space (still reproducible byte-for-byte).
    """
    if len(a) != len(b):
        raise ValueError("paired test requires equal-length samples")
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    n = len(diffs)
    if n == 0:
        return 1.0, True
    observed = abs(sum(diffs))
    if n <= MAX_EXACT_PAIRS:
        total = 0
        extreme = 0
        for signs in itertools.product((1.0, -1.0), repeat=n):
            total += 1
            if abs(sum(s * d for s, d in zip(signs, diffs, strict=True))) >= observed - 1e-12:
                extreme += 1
        return extreme / total, True
    total = 0
    extreme = 0
    space = 1 << n
    stride = max(1, space // SUBSAMPLE_STRIDE_TARGET)
    for index in range(0, space, stride):
        total += 1
        stat = sum(d if (index >> i) & 1 == 0 else -d for i, d in enumerate(diffs))
        if abs(stat) >= observed - 1e-12:
            extreme += 1
    return extreme / total, False


def compare_runs(
    scores_a: dict[str, float],
    scores_b: dict[str, float],
    *,
    digests_match: bool = True,
    degraded: bool = False,
    tokenizer_a: str = "",
    tokenizer_b: str = "",
) -> ComparisonResult:
    """Paired comparison over the intersection of task ids, sorted for determinism."""
    shared = sorted(set(scores_a) & set(scores_b))
    a = [scores_a[t] for t in shared]
    b = [scores_b[t] for t in shared]
    reasons: list[str] = []
    if tokenizer_a != tokenizer_b:
        reasons.append(f"different tokenizers ({tokenizer_a!r} vs {tokenizer_b!r})")
    if degraded:
        reasons.append("a run is DEGRADED (infrastructure failure rate above threshold)")
    if not digests_match:
        reasons.append("candidate digests differ from the recorded ones")
    if not shared:
        reasons.append("no shared tasks")
    p_value, exact = paired_permutation_test(a, b) if shared else (1.0, True)
    return ComparisonResult(
        n=len(shared),
        mean_a=_mean(a),
        mean_b=_mean(b),
        delta=_mean(a) - _mean(b),
        p_value=p_value,
        exact=exact,
        comparable=not reasons,
        reason="; ".join(reasons),
    )
