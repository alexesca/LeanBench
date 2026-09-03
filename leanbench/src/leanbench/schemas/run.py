"""Run artifact models — the exact contents of `runs/<run-id>/` (build spec §13.1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from leanbench.schemas.events import ResourceSample
from leanbench.schemas.protocol import CandidateDigests


class RunManifest(BaseModel):
    """`manifest.json` — what was run, against what, when."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    schema_version: int = 1
    leanbench_version: str
    suite: str
    track: str
    candidate_name: str
    candidate_version: str
    started_at: str
    finished_at: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    status: str = "running"
    artifacts: list[str] = Field(default_factory=list)


class CandidateArtifact(BaseModel):
    """`candidate.json` — manifest + digests + declared capabilities."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    protocol_version: int
    manifest_path: str
    command: list[str]
    declared_capabilities: list[str]
    timeouts: dict[str, float]
    digests: CandidateDigests
    stats: dict[str, Any] = Field(default_factory=dict)
    resources: ResourceSample | None = None


class EnvironmentArtifact(BaseModel):
    """`environment.json` — enough to explain a number six months later."""

    model_config = ConfigDict(extra="forbid")

    python_version: str
    platform: str
    machine: str
    cpu_count: int | None
    total_memory_bytes: int | None
    leanbench_version: str
    package_versions: dict[str, str] = Field(default_factory=dict)
    tokenizer: str = ""
    tokenizer_approximate: bool = True
    tokenizer_unavailable_reason: str | None = None


class RunSummary(BaseModel):
    """`summary.json` — the human-facing headline."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite: str
    candidate: str
    track: str
    status: str
    task_count: int
    tasks_scored: int
    tasks_failed: int
    infrastructure_failure_rate: float
    degraded: bool
    informative_task_fraction: float | None = None
    suite_gate_passed: bool | None = None
    headline: dict[str, Any] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    tokenizer: str = ""
    tokenizer_approximate: bool = True
