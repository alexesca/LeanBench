"""Instrumentation records. Every one of these is serialized to a `runs/<id>/*.jsonl`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Token-accounting buckets (build spec §8.2). Only `repository` feeds the headline metric.
TOKEN_BUCKET_REPOSITORY = "repository"
TOKEN_BUCKET_PROMPT = "prompt"
TOKEN_BUCKETS: tuple[str, ...] = (TOKEN_BUCKET_REPOSITORY, TOKEN_BUCKET_PROMPT)


class Event(BaseModel):
    """A line of `events.jsonl`. `seq` is a per-run monotonic integer and is the only
    ordering key used by anything that feeds metrics (wall clock never is)."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    run_id: str
    kind: str
    component: str
    task_id: str | None = None
    #: Wall clock, informational only. Never read by scoring.
    ts: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RepositoryAccessRecord(BaseModel):
    """Every byte of repository content that reached the model."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    task_id: str
    tool: str
    path: str | None = None
    byte_range: tuple[int, int] | None = None
    line_range: tuple[int, int] | None = None
    bytes_returned: int
    tokens_returned: int
    token_bucket: str = TOKEN_BUCKET_REPOSITORY
    approximate: bool = True
    timestamp: float | None = None


class CandidateCallRecord(BaseModel):
    """One request/response pair against the candidate subprocess."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    request_id: str
    task_id: str | None
    op: str
    args_digest: str
    status: str
    code: str | None = None
    latency_ms: float
    bytes_returned: int
    tokens_returned: int
    truncated: bool | None = None
    index_state: str | None = None
    classification: str | None = None


class ResourceSample(BaseModel):
    """Externally measured (psutil) candidate process-tree resource usage."""

    model_config = ConfigDict(extra="forbid")

    pid: int | None = None
    cpu_user_s: float = 0.0
    cpu_system_s: float = 0.0
    rss_peak_bytes: int = 0
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    available: bool = True
    reason: str | None = None


class CostLedgerEntry(BaseModel):
    """One row of `cost-ledger.jsonl` — what a single interaction cost in tokens."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    task_id: str
    tool: str
    bucket: str
    tokens: int
    bytes: int
    approximate: bool
    counted_in_metric: bool


class FailureRecord(BaseModel):
    """One row of `failures.jsonl` (PROTOCOL.md §7)."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    run_id: str
    task_id: str | None
    component: str
    classification: str
    op: str | None = None
    message: str
    stderr_tail: str = ""
    exit_code: int | None = None
