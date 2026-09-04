"""LeanBench command line — composition only.

Every command below is a thin shell over pure functions and port implementations.
No scoring arithmetic lives here; if a number appears on screen it was computed by
something in `leanbench.scoring`, which does no I/O and is unit-tested without fixtures.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import typer
from leanbench.artifacts import ARTIFACT_NAMES, read_json
from leanbench.config import parse_cli_overrides, resolve_config
from leanbench.corpus import load_corpus
from leanbench.evaluator import evaluate
from leanbench.kernel.errors import LeanBenchError
from leanbench.noise import (
    dispersion,
    gate_raw_vs_semantic,
    profile_key,
    separation_matrix,
)
from leanbench.scoring.compare import compare_runs
from leanbench.scoring.task_rules import (
    discrimination_index,
    informative_fraction,
    triage_flags,
)
from leanbench.tasks import find_suite, load_suite, validate_suite

app = typer.Typer(add_completion=False, help="LeanBench — a benchmark for repository intelligence.")
tasks_app = typer.Typer(help="Author, validate and triage tasks.")
config_app = typer.Typer(help="Inspect resolved configuration.")
app.add_typer(tasks_app, name="tasks")
app.add_typer(config_app, name="config")

SUITE_SEARCH = [Path("suites"), Path("leanbench/fixtures")]


def _config(overrides: list[str] | None = None) -> Any:
    return resolve_config(
        cli_overrides=parse_cli_overrides(overrides or []), search_from=Path.cwd()
    )


def _echo(message: str) -> None:
    typer.echo(message)


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def evaluate_cmd(
    candidate: Path = typer.Option(..., "--candidate", help="Path to leanbench-candidate.toml"),
    suite: str = typer.Option(..., "--suite", help="Suite name or path"),
    track: str = typer.Option("retrieval", "--track", help="retrieval | agent | both"),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    repo: Path | None = typer.Option(None, "--repo", help="Override the repository path"),
    run_id_seed: str | None = typer.Option(None, "--run-id-seed", help="Deterministic run id"),
    policy: str | None = typer.Option(None, "--policy", help="Agent-track policy"),
    set_: list[str] = typer.Option([], "--set", help="Config override key=value"),
) -> None:
    """Run a suite against a candidate and write an immutable run directory."""
    config = _config(set_)
    suite_path = find_suite(suite, SUITE_SEARCH)
    try:
        result = evaluate(
            config=config,
            manifest_path=candidate,
            suite_path=suite_path,
            track=track,
            repo_root=repo,
            run_id_seed=run_id_seed,
            runs_dir=runs_dir,
            agent_policy=policy,
        )
    except LeanBenchError as exc:
        _fail(f"{type(exc).__name__}: {exc}")
        return

    summary = result.summary
    head = summary.headline
    marker = "~" if summary.tokenizer_approximate else ""
    _echo(f"run {result.run_id}   {summary.candidate} on {summary.suite}   [{summary.status}]")
    _echo(f"  tasks            {summary.tasks_scored}/{summary.task_count}")
    _echo(
        f"  {head['primary_metric']:<16s} {head['mean']:.4f}  (worst-case {head['worst_case']:.4f})"
    )
    _echo(f"  tokens returned  {marker}{head['tokens_returned_total']}")
    _echo(f"  brittle tasks    {len(head['brittle_tasks'])}")
    if "repository_tokens_to_correct_solution" in head:
        # The signature metric is never printed without the correctness rate beside it:
        # a candidate that fails fast otherwise posts a beautiful token number.
        _echo(
            f"  correctness      {head['correctness']:.4f}"
            f"  ({int(head['tasks_correct'])}/{summary.task_count} tasks)"
        )
        _echo(
            f"  repo tokens to correct solution  {marker}"
            f"{head['repository_tokens_to_correct_solution']:.0f}"
        )
    if summary.degraded:
        typer.secho(
            "  DEGRADED: infrastructure failures exceed the threshold; "
            "no conclusions may be drawn from this run.",
            fg=typer.colors.RED,
        )
    _echo(f"  artifacts        {result.run_dir}")


# Typer derives the command name from the function; `evaluate_cmd` would become
# `evaluate-cmd`, so the name is pinned explicitly.
app.command(name="evaluate")(evaluate_cmd)


@config_app.command("show")
def config_show(set_: list[str] = typer.Option([], "--set")) -> None:
    """Print every resolved value with the precedence layer it came from."""
    config = _config(set_)
    provenance = config.as_provenance_dict()
    width = max((len(k) for k in provenance), default=10)
    for key in sorted(provenance):
        entry = provenance[key]
        _echo(f"{key:<{width}}  = {entry['value']!r}   [{entry['source']}]")


@app.command()
def doctor() -> None:
    """Check that the environment can run a benchmark at all."""
    ok = True
    _echo(f"python           {platform.python_version()}  ({sys.executable})")
    _echo(f"platform         {platform.platform()}")
    for module in ("pydantic", "typer", "psutil"):
        try:
            __import__(module)
            _echo(f"{module:<16} present")
        except ImportError:
            _echo(f"{module:<16} MISSING")
            ok = False
    for module in ("tiktoken", "tree_sitter"):
        try:
            __import__(module)
            _echo(f"{module:<16} present")
        except ImportError:
            # Optional: absence changes what is available, not whether it works.
            _echo(f"{module:<16} absent (optional; token counts will be approximate)")
    _echo(f"ripgrep          {shutil.which('rg') or 'absent (the ripgrep baseline needs it)'}")
    try:
        corpus = load_corpus()
        for repo_id in corpus.ids():
            entry = corpus.get(repo_id)
            path = entry.path(Path.cwd())
            state = "present" if path.is_dir() else "NOT CLONED"
            _echo(f"corpus {repo_id:<10} {state}  @ {entry.commit[:12]}")
    except LeanBenchError as exc:
        _echo(f"corpus           unreadable: {exc}")
        ok = False
    if not ok:
        raise typer.Exit(code=1)


@tasks_app.command("validate")
def tasks_validate(suite: str = typer.Argument(...)) -> None:
    """Schema plus gold resolution at the pinned commit. Stale gold is a hard failure."""
    loaded = load_suite(find_suite(suite, SUITE_SEARCH))
    issues = validate_suite(loaded, load_corpus())
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    for issue in sorted(issues, key=lambda i: (i.severity, i.task_id, i.code)):
        colour = typer.colors.RED if issue.severity == "error" else typer.colors.YELLOW
        typer.secho(
            f"{issue.severity:<7} {issue.task_id:<20} {issue.code}: {issue.message}", fg=colour
        )
    _echo(f"{len(loaded.tasks)} tasks, {len(errors)} errors, {len(warnings)} warnings")
    if errors:
        raise typer.Exit(code=1)


@tasks_app.command("inspect")
def tasks_inspect(suite: str = typer.Argument(...), task_id: str = typer.Argument(...)) -> None:
    """Prompt, probes, gold and justification for one task."""
    loaded = load_suite(find_suite(suite, SUITE_SEARCH))
    for task in loaded.tasks:
        if task.id != task_id:
            continue
        _echo(
            f"{task.id}  [{task.category} / {task.difficulty}]  "
            f"{task.repository}@{task.commit[:12]}"
        )
        _echo(f"prompt: {task.prompt.strip()}")
        for probe in task.probes:
            _echo(
                f"  probe[{probe.paraphrase_id}] {probe.op} "
                f"{json.dumps(probe.args, sort_keys=True)}"
            )
        _echo(f"gold.files   {task.gold.files}")
        _echo(f"gold.symbols {task.gold.symbols}")
        _echo(f"gold.tests   {task.gold.tests}")
        _echo(f"why: {task.gold.justification}")
        return
    _fail(f"no task {task_id!r} in suite {suite!r}")


@tasks_app.command("triage")
def tasks_triage(
    suite: str = typer.Argument(...),
    runs: list[Path] = typer.Option([], "--run", help="Baseline run directories"),
    write: bool = typer.Option(False, "--write", help="Write triage.json into the suite"),
) -> None:
    """Rank tasks by information content, using baseline runs as the reference spread.

    A task every baseline passes, or every baseline fails, carries zero information.
    The suite gate is the fraction that are informative.
    """
    config = _config()
    loaded = load_suite(find_suite(suite, SUITE_SEARCH))
    if not runs:
        _fail("triage needs at least one --run; discrimination is measured, not assumed")

    per_task: dict[str, dict[str, float]] = {}
    for run_dir in runs:
        metrics = read_json(run_dir, "metrics.json")
        name = metrics["candidate"]
        for task in metrics.get("retrieval_tasks", []):
            per_task.setdefault(task["task_id"], {})[name] = float(
                task["mean"].get(config.get_str("retrieval.primary_metric"), 0.0)
            )

    flags_by_task: dict[str, list[str]] = {}
    report: dict[str, Any] = {}
    for task in loaded.tasks:
        scores = per_task.get(task.id, {})
        flags = triage_flags(
            scores,
            ceiling_threshold=config.get_float("suite.ceiling_threshold"),
            floor_threshold=config.get_float("suite.floor_threshold"),
            unstable_cv=config.get_float("suite.unstable_cv"),
        )
        flags_by_task[task.id] = flags
        report[task.id] = {
            "baseline_scores": {k: round(v, 4) for k, v in sorted(scores.items())},
            "discrimination_index": round(discrimination_index(scores), 4) if scores else 0.0,
            "flags": flags,
            "category": task.category,
            "difficulty": task.difficulty,
        }

    fraction = informative_fraction(flags_by_task)
    threshold = loaded.spec.informative_task_rate_threshold
    ranked = sorted(report.items(), key=lambda kv: -kv[1]["discrimination_index"])
    _echo(f"{'task':<22} {'disc':>6}  flags")
    for task_id, row in ranked:
        _echo(f"{task_id:<22} {row['discrimination_index']:>6.3f}  {','.join(row['flags']) or '-'}")
    _echo("")
    _echo(f"informative task rate  {fraction:.1%}  (gate {threshold:.0%})")
    counts: dict[str, int] = {}
    for flags in flags_by_task.values():
        for flag in flags:
            counts[flag] = counts.get(flag, 0) + 1
    if counts:
        _echo("flagged: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if write:
        target = loaded.path / "triage.json"
        target.write_text(
            json.dumps(
                {"informative_task_rate": round(fraction, 4), "tasks": report},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _echo(f"wrote {target}")
    if fraction < threshold:
        typer.secho(
            f"SUITE GATE FAILED: {fraction:.1%} informative, below {threshold:.0%}. "
            "The suite is not usable until this is fixed; adding tasks is not a fix "
            "unless they discriminate.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


@app.command()
def report(
    run_id: Path = typer.Argument(..., help="Run directory"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print a run's summary. Shaped for an optimization agent under --json."""
    summary = read_json(run_id, "summary.json")
    metrics = read_json(run_id, "metrics.json")
    if as_json:
        tasks = sorted(
            metrics.get("retrieval_tasks", []),
            key=lambda t: -t.get("tokens_returned_total", 0),
        )
        weak = sorted(
            metrics.get("retrieval_tasks", []),
            key=lambda t: t["mean"].get("ndcg_at_10", 0.0),
        )[:10]
        _echo(
            json.dumps(
                {
                    "score": summary["headline"]["mean"],
                    "dimensions": metrics.get("retrieval_aggregate", {}),
                    "weak_categories": [t["task_id"] for t in weak],
                    "largest_token_costs": [
                        {"task_id": t["task_id"], "tokens": t["tokens_returned_total"]}
                        for t in tasks[:10]
                    ],
                    "brittle_tasks": summary["headline"]["brittle_tasks"],
                    "suite_health": {
                        "infra_failure_rate": summary["infrastructure_failure_rate"],
                        "degraded": summary["degraded"],
                    },
                    "tokenizer": summary["tokenizer"],
                    "tokenizer_approximate": summary["tokenizer_approximate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    _echo(
        f"{summary['run_id']}  {summary['candidate']} on {summary['suite']}  [{summary['status']}]"
    )
    for key, value in sorted(metrics.get("retrieval_aggregate", {}).items()):
        _echo(f"  {key:<28} {value:.4f}")
    missing = [n for n in ARTIFACT_NAMES if not (Path(run_id) / n).exists()]
    if missing:
        typer.secho(f"  missing artifacts: {missing}", fg=typer.colors.RED)


@app.command()
def compare(run_a: Path = typer.Argument(...), run_b: Path = typer.Argument(...)) -> None:
    """Compare two runs, refusing to call a sub-noise delta an improvement."""
    config = _config()
    metric = config.get_str("retrieval.primary_metric")
    a_metrics, b_metrics = read_json(run_a, "metrics.json"), read_json(run_b, "metrics.json")
    a_summary, b_summary = read_json(run_a, "summary.json"), read_json(run_b, "summary.json")

    scores_a = {t["task_id"]: t["mean"].get(metric, 0.0) for t in a_metrics["retrieval_tasks"]}
    scores_b = {t["task_id"]: t["mean"].get(metric, 0.0) for t in b_metrics["retrieval_tasks"]}
    result = compare_runs(
        scores_a,
        scores_b,
        degraded=bool(a_summary["degraded"] or b_summary["degraded"]),
        tokenizer_a=a_summary["tokenizer"],
        tokenizer_b=b_summary["tokenizer"],
    )
    _echo(f"{a_metrics['candidate']} vs {b_metrics['candidate']}   metric={metric}  n={result.n}")
    _echo(
        f"  {result.mean_a:.4f} -> {result.mean_b:.4f}   "
        f"delta {-result.delta:+.4f}   p={result.p_value:.4f}"
    )
    if not result.comparable:
        typer.secho(f"  NOT COMPARABLE: {result.reason}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    # The retrieval track is deterministic, so a non-zero delta is real by construction.
    # Stochastic dimensions must not reach this path without a noise profile.
    verdict = (
        "IMPROVED"
        if result.p_value < 0.05 and result.delta < 0
        else ("REGRESSED" if result.p_value < 0.05 and result.delta > 0 else "NO CONCLUSION")
    )
    colour = {"IMPROVED": typer.colors.GREEN, "REGRESSED": typer.colors.RED}.get(
        verdict, typer.colors.YELLOW
    )
    typer.secho(f"  {verdict}", fg=colour)


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    return 0


#: `tokens` is the signature dimension and is lower-is-better, so it is negated to keep
#: "a larger delta means the row is better" true for every metric in the matrix.
TOKENS_METRIC = "tokens"


def _task_scores(run_dir: Path, metric: str) -> dict[str, float]:
    metrics = read_json(run_dir, "metrics.json")
    if metric == TOKENS_METRIC:
        return {
            t["task_id"]: -float(t["tokens_returned_total"])
            for t in metrics["retrieval_tasks"]
        }
    return {t["task_id"]: float(t["mean"].get(metric, 0.0)) for t in metrics["retrieval_tasks"]}


@app.command()
def noise(
    candidate: Path = typer.Option(..., "--candidate"),
    suite: str = typer.Option(..., "--suite"),
    repetitions: int = typer.Option(10, "--repetitions"),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    out: Path | None = typer.Option(None, "--out", help="Where to write noise-profile.json"),
) -> None:
    """Measure the noise floor by re-running an identical configuration N times.

    Retrieval-track noise must be exactly zero. If it is not, the nondeterminism is ours,
    and this command is how we find out rather than discovering it as a phantom result.
    """
    config = _config()
    suite_path = find_suite(suite, SUITE_SEARCH)
    metric = config.get_str("retrieval.primary_metric")

    per_repetition: list[dict[str, float]] = []
    headline: list[float] = []
    for index in range(repetitions):
        result = evaluate(
            config=config, manifest_path=candidate, suite_path=suite_path,
            runs_dir=runs_dir, run_id_seed=f"noise-{index}",
        )
        per_repetition.append(_task_scores(result.run_dir, metric))
        headline.append(float(result.metrics.retrieval_aggregate.get(metric, 0.0)))

    noisy = config.get_float("suite.unstable_cv") / 4.0
    unusable = config.get_float("suite.unstable_cv")
    overall = dispersion(metric, headline, noisy_above=noisy, unusable_above=unusable)

    per_task = {}
    for task_id in sorted(per_repetition[0]):
        values = [rep.get(task_id, 0.0) for rep in per_repetition]
        per_task[task_id] = dispersion(
            task_id, values, noisy_above=noisy, unusable_above=unusable
        ).as_dict()

    key = profile_key(suite=suite_path.name, harness="retrieval", model="none",
                      model_settings="deterministic")
    profile = {
        "key": key,
        "suite": suite_path.name,
        "track": "retrieval",
        "candidate": str(candidate),
        "repetitions": repetitions,
        "metric": metric,
        "overall": overall.as_dict(),
        "per_task": per_task,
    }

    _echo(f"noise profile {key}   {repetitions} repetitions of {metric}")
    _echo(f"  mean {overall.mean:.6f}   stdev {overall.stdev:.6f}   CV {overall.cv:.4%}")
    for n in sorted(overall.mde_at):
        _echo(f"  minimum detectable effect at n={n:<3d} {overall.mde_at[n]:.6f}")
    counts: dict[str, int] = {}
    for row in per_task.values():
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    _echo("  per-task: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if overall.stdev == 0.0:
        typer.secho("  retrieval-track variance is exactly zero, as required.",
                    fg=typer.colors.GREEN)
    else:
        typer.secho(
            f"  NONDETERMINISM: the retrieval track varied by {overall.stdev:.6g}. "
            "This is a defect in LeanBench or in the candidate, not a property of the "
            "measurement; fix it before trusting any comparison.",
            fg=typer.colors.RED,
        )

    target = out or (Path(runs_dir) if runs_dir else Path(config.get_str("run.runs_dir")))
    target = target / "noise-profile.json" if target.is_dir() or not target.suffix else target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _echo(f"  wrote {target}")
    if overall.stdev != 0.0:
        raise typer.Exit(code=1)


@app.command()
def separation(
    runs: list[Path] = typer.Argument(..., help="Run directories, one per candidate"),
    raw: str = typer.Option("RawRepository", "--raw"),
    semantic: str = typer.Option("MinimalAST", "--semantic"),
    threshold: float = typer.Option(0.474, "--threshold"),
    metric: str = typer.Option(
        TOKENS_METRIC, "--metric",
        help="'tokens' (the signature dimension the gate is defined on) or a retrieval metric",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Pairwise effect size between candidates: the benchmark's discriminative power.

    If the reference baselines are not separable, the suite is measuring the agent's
    stubbornness rather than the candidate.
    """
    config = _config()
    if metric == "primary":
        metric = config.get_str("retrieval.primary_metric")
    scores = {}
    for run_dir in runs:
        name = read_json(run_dir, "metrics.json")["candidate"]
        scores[name] = _task_scores(run_dir, metric)

    matrix = separation_matrix(scores)
    gate = gate_raw_vs_semantic(matrix, raw=raw, semantic=semantic, threshold=threshold)
    if as_json:
        _echo(json.dumps({"metric": metric, "matrix": matrix, "gate": gate},
                         indent=2, sort_keys=True))
        raise typer.Exit(code=0 if gate["passed"] else 1)

    names = matrix["candidates"]
    width = max(len(n) for n in names) + 1
    label = "repository tokens (lower is better)" if metric == TOKENS_METRIC else metric
    _echo(f"Cliff's delta on {label} (row vs column; |d|>=0.33 medium, >=0.474 large)")
    _echo(" " * width + "".join(f"{n[:10]:>11}" for n in names))
    for row in names:
        cells = "".join(
            f"{'    --   ':>11}" if row == col
            else f"{matrix['delta'][row][col]:>11.3f}"
            for col in names
        )
        _echo(f"{row:<{width}}{cells}")
    _echo("")
    if gate.get("delta") is None:
        typer.secho(f"  gate not evaluable: {gate['reason']}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    verdict = "PASSED" if gate["passed"] else "FAILED"
    colour = typer.colors.GREEN if gate["passed"] else typer.colors.RED
    typer.secho(
        f"  discrimination gate {verdict}: |delta({raw}, {semantic})| = "
        f"{abs(gate['delta']):.3f} ({gate['magnitude']}) against threshold {threshold}",
        fg=colour,
    )
    if not gate["passed"]:
        typer.secho(
            "  The suite is not usable for headline claims until this passes. "
            "Adding corpus does not fix it.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
