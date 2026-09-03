"""Structural ports. `typing.Protocol` only — no ABCs, no registration side effects,
no version negotiation. An implementation satisfies a port by having the methods.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from leanbench.schemas.events import CostLedgerEntry, RepositoryAccessRecord, ResourceSample
from leanbench.schemas.metrics import ProbeMetrics, RetrievalTaskMetrics
from leanbench.schemas.protocol import CandidateDigests
from leanbench.schemas.task import Task


@runtime_checkable
class RepositoryPort(Protocol):
    """Read-only view of a checked-out repository at a pinned commit."""

    root: Any

    def list_files(self, subpath: str | None = None) -> list[str]:
        """Repo-relative POSIX paths, sorted."""

    def read(self, path: str) -> str:
        """Full text of a file."""

    def read_range(self, path: str, start: int, end: int) -> str:
        """Inclusive 1-based line span."""

    def stat(self, path: str) -> dict[str, Any]:
        """`{path, bytes, lines, is_dir, exists}`."""

    def search(self, pattern: str, limit: int) -> list[dict[str, Any]]:
        """Plain-text/regex search; `[{path, line, text}]`, deterministically ordered."""

    def exists(self, path: str) -> bool: ...


@runtime_checkable
class CandidatePort(Protocol):
    """A live candidate system speaking PROTOCOL.md."""

    name: str

    def start(self) -> None: ...

    def prepare(self, path: str, commit: str) -> dict[str, Any]: ...

    def call(
        self, op: str, args: dict[str, Any], *, task_id: str | None = None
    ) -> dict[str, Any]:
        """Terminal `result` for a successful op. Raises a classified LeanBenchError
        for every failure mode in PROTOCOL.md §7."""

    def declared_capabilities(self) -> frozenset[str]: ...

    def digests(self) -> CandidateDigests: ...

    def resources(self) -> ResourceSample: ...

    def shutdown(self) -> None: ...


@runtime_checkable
class HarnessPort(Protocol):
    """Drives one task against one candidate and returns raw observations."""

    track: str

    def run_task(self, task: Task) -> dict[str, Any]: ...


@runtime_checkable
class GraderPort(Protocol):
    """Turns raw observations into metrics. Knows gold; knows nothing of candidates."""

    def grade(self, task: Task, observations: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class MetricPort(Protocol):
    """Aggregates per-task metrics into run-level numbers. Pure."""

    name: str

    def aggregate(
        self, probes: list[ProbeMetrics], tasks: list[RetrievalTaskMetrics]
    ) -> dict[str, float]: ...


@runtime_checkable
class ReporterPort(Protocol):
    """Renders a finished run. Reads artifacts; never mutates them."""

    def render(self, run_dir: Any) -> str: ...


@runtime_checkable
class MutationPort(Protocol):
    """Applies a controlled change to a working copy for incremental-update tasks."""

    def apply(self, root: Any, spec: dict[str, Any]) -> dict[str, list[str]]:
        """Returns `{changed, added, removed}` repo-relative path lists."""

    def revert(self, root: Any) -> None: ...


@runtime_checkable
class TokenCounterPort(Protocol):
    """One tokenizer per run, identical for every candidate."""

    name: str
    approximate: bool
    available: bool

    def count(self, text: str) -> int: ...


@runtime_checkable
class InstrumentationPort(Protocol):
    """Sink for the access/cost records the gateway produces."""

    def record_access(self, record: RepositoryAccessRecord) -> None: ...

    def record_cost(self, entry: CostLedgerEntry) -> None: ...


__all__ = [
    "CandidatePort",
    "GraderPort",
    "HarnessPort",
    "InstrumentationPort",
    "MetricPort",
    "MutationPort",
    "ReporterPort",
    "RepositoryPort",
    "TokenCounterPort",
]
