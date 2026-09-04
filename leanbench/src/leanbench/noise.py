"""Noise floor and discriminative power — the two properties that decide whether any
other number in this project means anything (ADR-005).

Nothing here interprets a result. It measures dispersion and effect size and hands both
back; refusing to over-claim is enforced at the `compare` boundary.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

#: Two-sided z at alpha = 0.05, and z at 80% power. Hard-coded because they are the
#: definition of the stated test, not tunables.
Z_ALPHA_TWO_SIDED = 1.959963984540054
Z_POWER_80 = 0.8416212335729143

STABLE = "stable"
NOISY = "noisy"
UNUSABLE = "unusable"


@dataclass(frozen=True)
class Dispersion:
    """Per-dimension dispersion across repetitions of an identical configuration."""

    name: str
    n: int
    mean: float
    median: float
    stdev: float
    iqr: float
    cv: float
    minimum: float
    maximum: float
    classification: str
    mde_at: dict[int, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "n": self.n, "mean": self.mean, "median": self.median,
            "stdev": self.stdev, "iqr": self.iqr, "cv": self.cv,
            "min": self.minimum, "max": self.maximum,
            "classification": self.classification,
            "mde_at": {str(k): v for k, v in sorted(self.mde_at.items())},
        }


def _iqr(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    lower = ordered[:mid]
    upper = ordered[-mid:]
    return statistics.median(upper) - statistics.median(lower)


def minimum_detectable_effect(stdev: float, n: int) -> float:
    """Smallest true difference detectable at 80% power, alpha = 0.05, paired.

    An effect below this is not "small" — it is *unmeasurable at this sample size*, and
    reporting it as a result is the Type I failure this whole module exists to prevent.
    """
    if n <= 0 or stdev <= 0.0:
        return 0.0
    return (Z_ALPHA_TWO_SIDED + Z_POWER_80) * stdev / math.sqrt(n)


def classify(cv: float, *, noisy_above: float, unusable_above: float) -> str:
    if cv > unusable_above:
        return UNUSABLE
    if cv > noisy_above:
        return NOISY
    return STABLE


def dispersion(
    name: str,
    values: list[float],
    *,
    noisy_above: float,
    unusable_above: float,
    repetition_points: tuple[int, ...] = (1, 3, 5, 10),
) -> Dispersion:
    n = len(values)
    if n == 0:
        return Dispersion(name, 0, 0, 0, 0, 0, 0, 0, 0, UNUSABLE, {})
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if n > 1 else 0.0
    cv = (stdev / abs(mean)) if mean else 0.0
    return Dispersion(
        name=name,
        n=n,
        mean=mean,
        median=statistics.median(values),
        stdev=stdev,
        iqr=_iqr(values),
        cv=cv,
        minimum=min(values),
        maximum=max(values),
        classification=classify(cv, noisy_above=noisy_above, unusable_above=unusable_above),
        mde_at={k: minimum_detectable_effect(stdev, k) for k in repetition_points},
    )


def profile_key(*, suite: str, harness: str, model: str, model_settings: str) -> str:
    """A noise profile is only valid for the exact configuration that produced it."""
    import hashlib

    raw = "|".join([suite, harness, model, model_settings])
    return "np_" + hashlib.blake2b(raw.encode("utf-8"), digest_size=4).hexdigest()


# --- discriminative power ---------------------------------------------------------


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Non-parametric effect size in [-1, 1]: P(a>b) - P(a<b).

    Chosen over a p-value because significance answers "is there a difference"; the
    question a benchmark must answer is "is the difference large enough to matter".
    """
    if not a or not b:
        return 0.0
    greater = sum(1 for x in a for y in b if x > y)
    less = sum(1 for x in a for y in b if x < y)
    return (greater - less) / (len(a) * len(b))


def effect_magnitude(delta: float) -> str:
    """Conventional thresholds, stated so a reader can disagree with them."""
    value = abs(delta)
    if value < 0.147:
        return "negligible"
    if value < 0.33:
        return "small"
    if value < 0.474:
        return "medium"
    return "large"


def separation_matrix(scores_by_candidate: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Pairwise Cliff's delta between candidates over their shared tasks.

    This is the benchmark's Type II guard: if the reference baselines are not separable,
    the suite is measuring the agent's stubbornness rather than the candidate, and no
    amount of extra corpus fixes that.
    """
    names = sorted(scores_by_candidate)
    pairs: dict[str, dict[str, float]] = {}
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            shared = sorted(set(scores_by_candidate[left]) & set(scores_by_candidate[right]))
            a = [scores_by_candidate[left][t] for t in shared]
            b = [scores_by_candidate[right][t] for t in shared]
            delta = cliffs_delta(a, b)
            pairs.setdefault(left, {})[right] = round(delta, 4)
            pairs.setdefault(right, {})[left] = round(-delta, 4)
    return {"candidates": names, "delta": pairs}


def gate_raw_vs_semantic(
    matrix: dict[str, Any], *, raw: str, semantic: str, threshold: float
) -> dict[str, Any]:
    """The Phase 9 discrimination gate, as data rather than a print statement."""
    delta = matrix.get("delta", {}).get(raw, {}).get(semantic)
    if delta is None:
        return {"passed": False, "reason": f"no comparison between {raw!r} and {semantic!r}"}
    return {
        "passed": abs(delta) >= threshold,
        "delta": delta,
        "magnitude": effect_magnitude(delta),
        "threshold": threshold,
        "raw": raw,
        "semantic": semantic,
    }
