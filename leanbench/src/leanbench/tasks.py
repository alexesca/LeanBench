"""Task and suite loading (TASKS.md §1). I/O lives here; the *rules* live in
`leanbench.scoring.task_rules`, which is pure and unit-tested without fixtures.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from leanbench.corpus import Corpus
from leanbench.kernel.errors import BenchmarkInfrastructureError, TaskValidationError
from leanbench.schemas.common import OP_FOR_CAPABILITY
from leanbench.schemas.task import SuiteSpec, Task, TaskIssue
from leanbench.scoring.task_rules import has_errors, validate_task

SUITE_SPEC_NAME = "suite.toml"
TASKS_DIRNAME = "tasks"


@dataclass(frozen=True)
class Suite:
    spec: SuiteSpec
    path: Path
    tasks: list[Task]

    @property
    def name(self) -> str:
        return self.spec.name

    def task_ids(self) -> list[str]:
        return sorted(task.id for task in self.tasks)


def load_task(path: Path) -> Task:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchmarkInfrastructureError(f"cannot read task {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise TaskValidationError(f"malformed task TOML {path}: {exc}") from exc
    try:
        return Task.model_validate({**data, "source_path": str(Path(path).resolve())})
    except ValueError as exc:
        raise TaskValidationError(f"task {path} does not match the schema: {exc}") from exc


def load_suite(path: Path) -> Suite:
    """Load `suite.toml` plus every `tasks/*.toml`, sorted by task id."""
    suite_dir = Path(path).resolve()
    spec_path = suite_dir / SUITE_SPEC_NAME
    if not spec_path.is_file():
        raise BenchmarkInfrastructureError(f"suite {suite_dir} has no {SUITE_SPEC_NAME}")
    try:
        spec = SuiteSpec.model_validate(tomllib.loads(spec_path.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise BenchmarkInfrastructureError(f"invalid {spec_path}: {exc}") from exc
    tasks = [load_task(p) for p in sorted((suite_dir / TASKS_DIRNAME).glob("*.toml"))]
    tasks.sort(key=lambda t: t.id)
    return Suite(spec=spec, path=suite_dir, tasks=tasks)


def find_suite(name: str, search_paths: Iterable[Path]) -> Path:
    """Resolve a suite by name or by path."""
    direct = Path(name)
    if (direct / SUITE_SPEC_NAME).is_file():
        return direct.resolve()
    for base in search_paths:
        candidate = Path(base) / name
        if (candidate / SUITE_SPEC_NAME).is_file():
            return candidate.resolve()
    searched = ", ".join(str(p) for p in search_paths)
    raise BenchmarkInfrastructureError(f"no suite named {name!r}; searched: {searched}")


def validate_suite(
    suite: Suite,
    corpus: Corpus,
    *,
    resolve_paths: bool = True,
    symbol_resolver: Callable[[str, str], bool] | None = None,
) -> list[TaskIssue]:
    """Every TASKS.md §1 rule, including gold resolution at the pinned commit."""
    issues: list[TaskIssue] = []
    seen: list[str] = []
    for task in suite.tasks:
        path_exists = None
        if resolve_paths:
            try:
                repo_path = corpus.resolve_path(task.repository)
            except BenchmarkInfrastructureError:
                repo_path = None
            if repo_path is not None:
                path_exists = _path_checker(repo_path)
        symbol_exists = None
        if symbol_resolver is not None:
            symbol_exists = lambda s, _t=task: symbol_resolver(_t.repository, s)  # noqa: E731
        issues.extend(
            validate_task(
                task,
                filename_stem=Path(task.source_path).stem if task.source_path else None,
                known_repositories=corpus.ids(),
                known_commits=corpus.commits(),
                known_capabilities=sorted(OP_FOR_CAPABILITY),
                path_exists=path_exists,
                symbol_exists=symbol_exists,
                released=suite.spec.released,
                seen_ids=list(seen),
            )
        )
        seen.append(task.id)
    return issues


def _path_checker(repo_path: Path) -> Callable[[str], bool]:
    def check(rel: str) -> bool:
        target = (repo_path / rel).resolve()
        return target.exists() and (repo_path in target.parents or target == repo_path)

    return check


def assert_suite_valid(issues: Iterable[TaskIssue]) -> None:
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        detail = "; ".join(f"{i.task_id}:{i.code}: {i.message}" for i in errors[:10])
        raise TaskValidationError(f"{len(errors)} task validation error(s): {detail}")


__all__ = [
    "Suite",
    "assert_suite_valid",
    "find_suite",
    "has_errors",
    "load_suite",
    "load_task",
    "validate_suite",
]
