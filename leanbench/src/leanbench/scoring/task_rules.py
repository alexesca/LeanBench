"""Task validation rules (TASKS.md §1 field requirements). Pure: takes a Task and a set
of known facts about the world, returns issues. Never touches the filesystem — resolution
of gold references is done by the caller and passed in as `resolver` results.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from leanbench.schemas.common import CATEGORIES, DIFFICULTIES, GATEWAY_TOOLS, PARAPHRASE_IDS
from leanbench.schemas.task import Task, TaskIssue

ERROR = "error"
WARNING = "warning"

MIN_PROBES = 3
MIN_DISTINCT_PARAPHRASES = 3


def _issue(task_id: str, severity: str, code: str, message: str) -> TaskIssue:
    return TaskIssue(task_id=task_id, severity=severity, code=code, message=message)


def validate_task(
    task: Task,
    *,
    filename_stem: str | None = None,
    known_repositories: Iterable[str] = (),
    known_commits: Iterable[tuple[str, str]] = (),
    known_capabilities: Iterable[str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    symbol_exists: Callable[[str], bool] | None = None,
    released: bool = False,
    seen_ids: Iterable[str] = (),
) -> list[TaskIssue]:
    """All TASKS.md §1 rules. Returns issues sorted by (severity, code)."""
    issues: list[TaskIssue] = []
    tid = task.id

    if not tid:
        issues.append(_issue("<unknown>", ERROR, "id_missing", "task id is empty"))
    if filename_stem is not None and tid != filename_stem:
        issues.append(
            _issue(tid, ERROR, "id_filename_mismatch", f"id {tid!r} != filename stem {filename_stem!r}")
        )
    if tid in set(seen_ids):
        issues.append(_issue(tid, ERROR, "id_duplicate", f"duplicate task id {tid!r} in suite"))

    if task.category not in CATEGORIES:
        issues.append(
            _issue(tid, ERROR, "category_invalid", f"category {task.category!r} not in TASKS.md §2")
        )
    if task.difficulty not in DIFFICULTIES:
        issues.append(
            _issue(tid, ERROR, "difficulty_invalid", f"difficulty {task.difficulty!r} not L1..L5")
        )

    repositories = set(known_repositories)
    if repositories and task.repository not in repositories:
        issues.append(
            _issue(
                tid,
                ERROR,
                "repository_unknown",
                f"repository {task.repository!r} not in the corpus manifest",
            )
        )
    commits = set(known_commits)
    if commits and (task.repository, task.commit) not in commits:
        issues.append(
            _issue(
                tid,
                ERROR,
                "commit_mismatch",
                f"commit {task.commit!r} is not the pinned commit for {task.repository!r}",
            )
        )

    if not task.prompt.strip():
        issues.append(_issue(tid, ERROR, "prompt_empty", "prompt is required for the agent track"))

    if len(task.probes) < MIN_PROBES:
        issues.append(
            _issue(tid, ERROR, "probes_too_few", f"{len(task.probes)} probes, need >= {MIN_PROBES}")
        )
    paraphrases = {p.paraphrase_id for p in task.probes}
    if len(paraphrases) < MIN_DISTINCT_PARAPHRASES:
        issues.append(
            _issue(
                tid,
                ERROR,
                "paraphrases_too_few",
                f"{len(paraphrases)} distinct paraphrase_id values, need >= "
                f"{MIN_DISTINCT_PARAPHRASES}",
            )
        )
    for probe in task.probes:
        if probe.paraphrase_id not in PARAPHRASE_IDS:
            issues.append(
                _issue(
                    tid,
                    ERROR,
                    "paraphrase_id_invalid",
                    f"paraphrase_id {probe.paraphrase_id!r} not in {list(PARAPHRASE_IDS)}",
                )
            )
        if not probe.op:
            issues.append(_issue(tid, ERROR, "probe_op_missing", "probe has no op"))

    if not (task.gold.files or task.gold.symbols):
        issues.append(
            _issue(tid, ERROR, "gold_empty", "gold needs at least one of files/symbols non-empty")
        )
    if not task.gold.justification.strip():
        issues.append(_issue(tid, ERROR, "justification_missing", "gold.justification is required"))

    for rel in task.gold.relationships:
        if len(rel) != 3:
            issues.append(
                _issue(
                    tid,
                    ERROR,
                    "relationship_malformed",
                    f"relationship {rel!r} is not a [source, KIND, target] triple",
                )
            )
    for rng in task.gold.ranges:
        if rng.end < rng.start:
            issues.append(
                _issue(tid, ERROR, "range_inverted", f"range {rng.path} {rng.start}>{rng.end}")
            )

    if path_exists is not None:
        for path in sorted(set(task.gold.files + task.gold.tests + task.gold.docs)):
            if not path_exists(path):
                issues.append(
                    _issue(tid, ERROR, "stale_gold", f"gold path {path!r} does not resolve at commit")
                )
        for rng in task.gold.ranges:
            if not path_exists(rng.path):
                issues.append(
                    _issue(tid, ERROR, "stale_gold", f"gold range path {rng.path!r} does not resolve")
                )
    if symbol_exists is not None:
        for symbol in sorted(set(task.gold.symbols)):
            if not symbol_exists(symbol):
                issues.append(
                    _issue(
                        tid, ERROR, "stale_gold", f"gold symbol {symbol!r} does not resolve at commit"
                    )
                )

    if not task.required_capabilities:
        issues.append(
            _issue(tid, ERROR, "capabilities_missing", "required_capabilities must be non-empty")
        )
    if known_capabilities is not None:
        unknown = sorted(set(task.required_capabilities) - set(known_capabilities))
        if unknown:
            issues.append(
                _issue(
                    tid,
                    ERROR,
                    "capabilities_unknown",
                    f"required_capabilities not manifest keys: {unknown}",
                )
            )

    bad_tools = sorted(set(task.allowed_tools) - set(GATEWAY_TOOLS))
    if bad_tools:
        issues.append(
            _issue(tid, ERROR, "tools_unknown", f"allowed_tools not gateway tools: {bad_tools}")
        )

    if released and not task.reviewed_by.strip():
        issues.append(
            _issue(tid, ERROR, "reviewed_by_missing", "reviewed_by is required in a released suite")
        )
    elif not task.reviewed_by.strip():
        issues.append(_issue(tid, WARNING, "reviewed_by_missing", "reviewed_by is empty (dev suite)"))

    if not task.authored_at.strip():
        issues.append(
            _issue(tid, WARNING, "authored_at_missing", "authored_at is needed for contamination provenance")
        )

    return sorted(issues, key=lambda i: (i.severity, i.code, i.message))


def has_errors(issues: Iterable[TaskIssue]) -> bool:
    return any(issue.severity == ERROR for issue in issues)


# --- TASKS.md §5 triage --------------------------------------------------------


def discrimination_index(baseline_scores: dict[str, float]) -> float:
    if not baseline_scores:
        return 0.0
    values = sorted(baseline_scores.values())
    return values[-1] - values[0]


def triage_flags(
    baseline_scores: dict[str, float],
    *,
    ceiling_threshold: float,
    floor_threshold: float,
    unstable_cv: float,
    stability_cv: float = 0.0,
    stale_gold: bool = False,
) -> list[str]:
    flags: list[str] = []
    values = list(baseline_scores.values())
    if values and all(v >= ceiling_threshold for v in values):
        flags.append("ceiling")
    if values and all(v <= floor_threshold for v in values):
        flags.append("floor")
    if stability_cv > unstable_cv:
        flags.append("unstable")
    if stale_gold:
        flags.append("stale_gold")
    return sorted(flags)


def is_informative(flags: Iterable[str]) -> bool:
    return not set(flags) & {"ceiling", "floor", "unstable", "stale_gold"}


def informative_fraction(flags_by_task: dict[str, list[str]]) -> float:
    if not flags_by_task:
        return 0.0
    good = sum(1 for flags in flags_by_task.values() if is_informative(flags))
    return good / len(flags_by_task)
