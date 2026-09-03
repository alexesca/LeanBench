"""Aggregation — mean AND worst-case across paraphrases, then across tasks. Pure."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def _round(value: float, precision: int) -> float:
    """Fixed-precision rounding everywhere a float is emitted, so repeated runs are
    byte-identical rather than merely close."""
    return round(value + 0.0, precision)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate_probes(
    probe_metrics: Sequence[Mapping[str, float]], *, precision: int
) -> tuple[dict[str, float], dict[str, float]]:
    """Mean and worst-case per metric key across a task's paraphrase probes.

    A missing key in one probe is treated as 0.0: a probe that produced nothing is a
    zero, not an absence, otherwise a failing paraphrase would silently improve a mean.
    """
    keys = sorted({key for probe in probe_metrics for key in probe})
    if not probe_metrics:
        return {}, {}
    means: dict[str, float] = {}
    worst: dict[str, float] = {}
    for key in keys:
        values = [float(probe.get(key, 0.0)) for probe in probe_metrics]
        means[key] = _round(mean(values), precision)
        worst[key] = _round(min(values), precision)
    return means, worst


def aggregate_tasks(
    task_metrics: Sequence[Mapping[str, float]], *, precision: int, prefix: str = ""
) -> dict[str, float]:
    """Macro-average across tasks (each task weighs the same regardless of gold size)."""
    keys = sorted({key for task in task_metrics for key in task})
    out: dict[str, float] = {}
    for key in keys:
        values = [float(task.get(key, 0.0)) for task in task_metrics]
        out[f"{prefix}{key}"] = _round(mean(values), precision)
    return out


def brittleness(
    means: Mapping[str, float], worst: Mapping[str, float], *, metric: str, gap: float
) -> bool:
    """A candidate is brittle on a task when its worst paraphrase collapses relative to
    its mean by more than the configured gap."""
    if metric not in means:
        return False
    return (means[metric] - worst.get(metric, 0.0)) > gap


def context_efficiency(baseline_tokens: int, candidate_tokens: int) -> float:
    """Fraction of baseline repository tokens saved, clamped to [0,1].

    0 means "used at least as many tokens as the baseline"; 1 means "used none".
    """
    if baseline_tokens <= 0:
        return 0.0
    saved = (baseline_tokens - max(candidate_tokens, 0)) / baseline_tokens
    return min(1.0, max(0.0, saved))


def effective_context_efficiency(correctness: float, efficiency: float) -> float:
    """build spec §8.2 headline: correctness x context_efficiency."""
    return max(0.0, min(1.0, correctness)) * max(0.0, min(1.0, efficiency))


def counts_by(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))
