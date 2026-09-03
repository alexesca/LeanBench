"""The composition layer: turns a suite plus a candidate into an immutable run.

This is the only module allowed to know about *all* of config, corpus, candidate
transport, harness, grader, instrumentation and artifacts at once. Everything it calls is
either a pure function or a port implementation, so the evaluator itself stays a
straight-line script that is easy to read against build spec §13.1.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from leanbench.artifacts import (
    LEANBENCH_VERSION,
    RunWriter,
    environment_artifact,
)
from leanbench.candidate.manifest import load_manifest
from leanbench.candidate.runner import SubprocessCandidate
from leanbench.corpus import load_corpus
from leanbench.grading.retrieval import RetrievalGrader
from leanbench.harness.retrieval import RetrievalHarness
from leanbench.instrumentation import Recorder
from leanbench.kernel.bus import EventBus
from leanbench.kernel.capabilities import assert_capabilities, required_for_probes
from leanbench.kernel.context import RunContext
from leanbench.kernel.errors import BenchmarkInfrastructureError, LeanBenchError
from leanbench.kernel.ids import new_run_id, run_id_from_seed
from leanbench.schemas.config import ResolvedConfig
from leanbench.schemas.metrics import RunMetrics
from leanbench.schemas.run import CandidateArtifact, RunManifest, RunSummary
from leanbench.scoring.aggregate import aggregate_tasks
from leanbench.store import RunStore
from leanbench.tasks import Suite, load_suite
from leanbench.tokens import build_token_counter


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class EvaluationResult:
    run_id: str
    run_dir: Path
    summary: RunSummary
    metrics: RunMetrics
    failures: list[dict[str, Any]] = field(default_factory=list)


def evaluate(
    *,
    config: ResolvedConfig,
    manifest_path: Path,
    suite_path: Path,
    track: str = "retrieval",
    repo_root: Path | None = None,
    run_id_seed: str | None = None,
    runs_dir: Path | None = None,
    corpus_manifest: Path | None = None,
) -> EvaluationResult:
    """Run `suite_path` against the candidate described by `manifest_path`.

    The run directory is created fresh and sealed at the end: a completed run is never
    mutated (ADR-008), so a re-run always means a new id.
    """
    if track != "retrieval":
        raise BenchmarkInfrastructureError(
            f"track {track!r} is not available; only the retrieval track is implemented"
        )

    suite: Suite = load_suite(suite_path)
    manifest = load_manifest(manifest_path)
    corpus = load_corpus(corpus_manifest)

    # --- capability assertion, before any work is done (build spec §3.2) -------------
    # Gate on what this track will actually issue, not on the suite's general-purpose
    # declaration: see `required_for_probes` for why the distinction is load-bearing.
    required = required_for_probes(probe.op for task in suite.tasks for probe in task.probes)
    assert_capabilities(
        required=required,
        declared=set(manifest.declared_capabilities),
        candidate=manifest.candidate.name,
    )

    run_id = run_id_from_seed(run_id_seed) if run_id_seed else new_run_id()
    base = Path(runs_dir) if runs_dir else Path(config.get_str("run.runs_dir"))
    run_dir = base / run_id
    if run_dir.exists():
        raise BenchmarkInfrastructureError(f"run directory {run_dir} already exists")
    writer = RunWriter(run_dir)

    bus = EventBus(run_id)
    events: list[dict[str, Any]] = []
    bus.subscribe("artifact", lambda event: events.append(event.model_dump()))

    context = RunContext(
        run_id=run_id,
        config=config,
        bus=bus,
        run_dir=run_dir,
        suite=suite.name,
        candidate=manifest.candidate.name,
        track=track,
    )
    counter = build_token_counter(config)
    counter_reason = getattr(counter, "unavailable_reason", None)
    recorder = Recorder(bus, counter, run_id=run_id)

    started_at = _utc_now()
    wall_start = time.perf_counter()

    # --- resolve the repository working copy -----------------------------------------
    repo_id = suite.spec.repository
    repo_path = Path(repo_root) if repo_root else corpus.resolve_path(repo_id)
    if not repo_path.is_dir():
        raise BenchmarkInfrastructureError(
            f"repository {repo_id!r} not found at {repo_path}; clone it into corpus/ first"
        )

    candidate = SubprocessCandidate(
        manifest,
        config,
        on_call=recorder.record_candidate_call,
        on_event=lambda kind, payload: bus.emit(kind, "candidate", **payload),
    )

    task_results: list[dict[str, Any]] = []
    probe_metrics: list[Any] = []
    task_metrics: list[Any] = []
    prepare_ms = 0.0
    stats: dict[str, Any] = {}

    try:
        candidate.start()
        t0 = time.perf_counter()
        candidate.prepare(str(repo_path), suite.spec.commit)
        prepare_ms = (time.perf_counter() - t0) * 1000.0

        harness = RetrievalHarness(context, candidate, recorder)
        grader = RetrievalGrader(config)

        for task in suite.tasks:
            observations = harness.run_task(task)
            graded = grader.grade(task, observations)
            probe_metrics.extend(graded["probe_metrics"])
            task_metrics.append(graded["task_metrics"])
            task_results.append(
                {
                    "task_id": task.id,
                    "category": task.category,
                    "difficulty": task.difficulty,
                    "probe_count": len(task.probes),
                }
            )
        try:
            stats = candidate.get_stats()
        except LeanBenchError as exc:
            context.record_failure(
                component="evaluator",
                classification=exc.classification,
                message=f"get_stats failed: {exc}",
            )
    finally:
        try:
            candidate.shutdown()
        except LeanBenchError as exc:
            context.record_failure(
                component="evaluator",
                classification=exc.classification,
                message=f"shutdown failed: {exc}",
            )

    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    precision = config.get_int("report.float_precision")

    aggregate = aggregate_tasks([m.mean for m in task_metrics], precision=precision)
    worst_aggregate = aggregate_tasks([m.worst for m in task_metrics], precision=precision)
    for key, value in worst_aggregate.items():
        aggregate[f"worst_{key}"] = value

    metrics = RunMetrics(
        run_id=run_id,
        suite=suite.name,
        candidate=manifest.candidate.name,
        track=track,
        tokenizer=recorder.tokenizer,
        tokenizer_approximate=recorder.approximate,
        retrieval_tasks=sorted(task_metrics, key=lambda m: m.task_id),
        retrieval_aggregate=aggregate,
        probe_metrics=sorted(probe_metrics, key=lambda m: (m.task_id, m.paraphrase_id, m.op)),
    )

    usage = recorder.token_usage()
    scored = len(task_metrics)
    failed_tasks = sum(1 for m in task_metrics if m.failed_probes)
    infra_rate = context.infrastructure_failure_rate(max(len(suite.tasks), 1))
    degraded = infra_rate > config.get_float("run.degraded_infrastructure_failure_rate")

    tokens_total = sum(m.tokens_returned_total for m in task_metrics)
    headline = {
        "primary_metric": config.get_str("retrieval.primary_metric"),
        "mean": aggregate.get(config.get_str("retrieval.primary_metric"), 0.0),
        "worst_case": aggregate.get(f"worst_{config.get_str('retrieval.primary_metric')}", 0.0),
        "tokens_returned_total": tokens_total,
        "brittle_tasks": sorted(m.task_id for m in task_metrics if m.brittle),
        "prepare_ms": round(prepare_ms, 2),
    }

    summary = RunSummary(
        run_id=run_id,
        suite=suite.name,
        candidate=manifest.candidate.name,
        track=track,
        status="degraded" if degraded else "ok",
        task_count=len(suite.tasks),
        tasks_scored=scored,
        tasks_failed=failed_tasks,
        infrastructure_failure_rate=round(infra_rate, precision),
        degraded=degraded,
        headline=headline,
        failure_counts=context.counters.as_dict(),
        timings_ms={"wall_ms": round(wall_ms, 2), "prepare_ms": round(prepare_ms, 2)},
        tokenizer=recorder.tokenizer,
        tokenizer_approximate=recorder.approximate,
    )

    finished_at = _utc_now()
    run_manifest = RunManifest(
        run_id=run_id,
        leanbench_version=LEANBENCH_VERSION,
        suite=suite.name,
        track=track,
        candidate_name=manifest.candidate.name,
        candidate_version=manifest.candidate.version,
        started_at=started_at,
        finished_at=finished_at,
        task_ids=suite.task_ids(),
        status=summary.status,
    )

    candidate_artifact = CandidateArtifact(
        name=manifest.candidate.name,
        version=manifest.candidate.version,
        protocol_version=manifest.protocol_version,
        manifest_path=str(manifest_path),
        command=[manifest.runtime.command, *manifest.runtime.args],
        declared_capabilities=sorted(manifest.declared_capabilities),
        timeouts=manifest.timeouts.model_dump(),
        digests=candidate.digests(),
        stats=stats,
        resources=candidate.resources(),
    )

    # --- artifacts (build spec §13.1) -------------------------------------------------
    writer.write_json("manifest.json", run_manifest.model_dump())
    writer.write_json("config.json", config.as_provenance_dict())
    writer.write_json("candidate.json", candidate_artifact.model_dump())
    writer.write_json(
        "environment.json",
        environment_artifact(
            tokenizer=recorder.tokenizer,
            approximate=recorder.approximate,
            reason=counter_reason,
        ).model_dump(),
    )
    writer.write_jsonl("tasks.jsonl", task_results)
    writer.write_jsonl("events.jsonl", events)
    writer.write_json("metrics.json", metrics.model_dump())
    writer.write_json("token-usage.json", usage.model_dump())
    writer.write_jsonl("cost-ledger.jsonl", recorder.ledger_rows())
    writer.write_jsonl("failures.jsonl", [f.model_dump() for f in context.failures])
    writer.write_json("summary.json", summary.model_dump())

    missing = writer.missing_artifacts()
    if missing:
        raise BenchmarkInfrastructureError(f"run {run_id} is missing artifacts: {missing}")

    if config.get_bool("storage.enabled"):
        store_path = base / config.get_str("storage.sqlite_filename")
        with RunStore(store_path) as store:
            store.record_run(
                {
                    "run_id": run_id,
                    "suite": suite.name,
                    "candidate": manifest.candidate.name,
                    "track": track,
                    "status": summary.status,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "tokenizer": recorder.tokenizer,
                    "tokenizer_approximate": int(recorder.approximate),
                    "degraded": int(summary.degraded),
                    "run_dir": str(run_dir),
                }
            )
            store.record_tasks(
                run_id,
                [
                    {
                        "task_id": m.task_id,
                        "track": track,
                        "failed": bool(m.failed_probes),
                        "repository_tokens": m.tokens_returned_total,
                    }
                    for m in task_metrics
                ],
            )
            store.record_metrics(run_id, "retrieval", aggregate)

    writer.seal()
    return EvaluationResult(
        run_id=run_id,
        run_dir=run_dir,
        summary=summary,
        metrics=metrics,
        failures=[f.model_dump() for f in context.failures],
    )
