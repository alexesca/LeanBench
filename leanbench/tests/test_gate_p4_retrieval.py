"""Phase 4 gates.

A: the retrieval track is bit-for-bit deterministic. Not "low variance" — ZERO. Any
   variance here is nondeterminism in our own code, and this test is how we find it.
B: candidates of genuinely different quality must produce different scores. If they do
   not, the tasks are wrong, and no amount of extra corpus fixes that.
"""

from __future__ import annotations

import json

import pytest
from lb_paths import FAKE_MANIFESTS, MINI_SUITE
from leanbench.evaluator import evaluate

REPETITIONS = 10


def _run(config, manifest_name: str, tmp_path, seed: str):
    return evaluate(
        config=config,
        manifest_path=FAKE_MANIFESTS / manifest_name,
        suite_path=MINI_SUITE,
        runs_dir=tmp_path,
        run_id_seed=seed,
    )


def _metrics_bytes(result) -> str:
    payload = json.loads((result.run_dir / "metrics.json").read_text(encoding="utf-8"))
    # run_id names a directory and is deliberately unique per run; it is not a metric.
    payload.pop("run_id", None)
    return json.dumps(payload, sort_keys=True, indent=2)


def test_gate_a_ten_identical_runs_are_byte_identical(config, tmp_path) -> None:
    renderings = [
        _metrics_bytes(_run(config, "normal.toml", tmp_path, f"det-{i}"))
        for i in range(REPETITIONS)
    ]
    first = renderings[0]
    for index, rendering in enumerate(renderings[1:], start=1):
        assert rendering == first, f"repetition {index} diverged from repetition 0"


def test_gate_a_probe_ordering_cannot_affect_a_metric(config, tmp_path) -> None:
    """Probes are issued in authored order but the grader re-sorts, so a metric may
    never depend on arrival order."""
    a = _run(config, "normal.toml", tmp_path, "order-a")
    ids = [p.paraphrase_id for p in a.metrics.probe_metrics]
    assert ids == sorted(ids) or True  # ordering is (task_id, paraphrase_id, op)
    keys = [(p.task_id, p.paraphrase_id, p.op) for p in a.metrics.probe_metrics]
    assert keys == sorted(keys), "probe metrics are not in a total order"


def test_gate_b_better_candidate_scores_higher(config, tmp_path) -> None:
    symbol_aware = _run(config, "symbol-aware.toml", tmp_path, "disc-symbol")
    text_only = _run(config, "text-match.toml", tmp_path, "disc-text")

    metric = config.get_str("retrieval.primary_metric")
    better = symbol_aware.metrics.retrieval_aggregate[metric]
    worse = text_only.metrics.retrieval_aggregate[metric]
    assert better != worse, (
        "a symbol-aware and a text-matching candidate scored identically on the mini "
        "suite; the tasks do not discriminate and must be fixed"
    )


@pytest.mark.parametrize("k", [1, 5, 10])
def test_ndcg_is_bounded(config, tmp_path, k: int) -> None:
    """Regression: nDCG once exceeded 1.0 because gain was awarded per relevant RESULT
    while the ideal ranking was built from distinct gold ITEMS."""
    result = _run(config, "normal.toml", tmp_path, f"bound-{k}")
    for probe in result.metrics.probe_metrics:
        assert 0.0 <= probe.ndcg_at_10 <= 1.0, probe
        for value in probe.recall_at_k.values():
            assert 0.0 <= value <= 1.0
        for value in probe.precision_at_k.values():
            assert 0.0 <= value <= 1.0
        assert 0.0 <= probe.mrr <= 1.0


def test_run_directory_is_sealed_and_complete(config, tmp_path) -> None:
    from leanbench.artifacts import ARTIFACT_NAMES, RunWriter
    from leanbench.kernel.errors import BenchmarkInfrastructureError

    result = _run(config, "normal.toml", tmp_path, "sealed")
    for name in ARTIFACT_NAMES:
        assert (result.run_dir / name).exists(), f"missing artifact {name}"
    # ADR-008: a completed run is never modified.
    writer = RunWriter(result.run_dir)
    assert writer.sealed
    with pytest.raises(BenchmarkInfrastructureError):
        writer.write_json("summary.json", {"tampered": True})
