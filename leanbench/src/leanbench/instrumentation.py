"""Instrumentation: token counting, access records, the cost ledger.

Everything the model was ever shown passes through `Recorder.record`, which is the only
place tokens are counted. `events.jsonl` carries enough in each `tool_completed` payload
to reconstruct `token-usage.json` without any other artifact (gate P5).
"""

from __future__ import annotations

import time
from typing import Any

from leanbench.kernel.bus import EventBus
from leanbench.schemas.events import (
    TOKEN_BUCKET_PROMPT,
    TOKEN_BUCKET_REPOSITORY,
    CandidateCallRecord,
    CostLedgerEntry,
    RepositoryAccessRecord,
)
from leanbench.schemas.metrics import TokenUsage, TokenUsageTask
from leanbench.scoring.tokens import (
    counted_in_metric,
    cumulative_by_task,
    per_tool_totals,
    total_prompt_tokens,
    total_repository_tokens,
)

TOOL_COMPLETED = "tool_completed"
TOOL_CALLED = "tool_called"


class Recorder:
    """Collects every measured interaction for one run."""

    def __init__(self, bus: EventBus, counter: Any, *, run_id: str) -> None:
        self.bus = bus
        self.counter = counter
        self.run_id = run_id
        self.accesses: list[RepositoryAccessRecord] = []
        self.costs: list[CostLedgerEntry] = []
        self.candidate_calls: list[CandidateCallRecord] = []

    @property
    def tokenizer(self) -> str:
        return str(getattr(self.counter, "name", "unknown"))

    @property
    def approximate(self) -> bool:
        return bool(getattr(self.counter, "approximate", True))

    def count(self, text: str) -> int:
        return self.counter.count(text)

    def record(
        self,
        *,
        task_id: str,
        tool: str,
        payload: str,
        bucket: str = TOKEN_BUCKET_REPOSITORY,
        path: str | None = None,
        byte_range: tuple[int, int] | None = None,
        line_range: tuple[int, int] | None = None,
        candidate_internal: bool = False,
        latency_ms: float | None = None,
    ) -> CostLedgerEntry:
        """Count `payload` — the exact serialized string handed to the model — and file
        the access record, the ledger row and the event that makes both reconstructible."""
        tokens = self.count(payload)
        n_bytes = len(payload.encode("utf-8"))
        counted = counted_in_metric(bucket, candidate_internal=candidate_internal)
        seq = self.bus.next_seq()
        self.accesses.append(
            RepositoryAccessRecord(
                seq=seq,
                task_id=task_id,
                tool=tool,
                path=path,
                byte_range=byte_range,
                line_range=line_range,
                bytes_returned=n_bytes,
                tokens_returned=tokens,
                token_bucket=bucket,
                approximate=self.approximate,
                timestamp=time.time(),
            )
        )
        entry = CostLedgerEntry(
            seq=seq,
            task_id=task_id,
            tool=tool,
            bucket=bucket,
            tokens=tokens,
            bytes=n_bytes,
            approximate=self.approximate,
            counted_in_metric=counted,
        )
        self.costs.append(entry)
        self.bus.emit(
            TOOL_COMPLETED,
            "gateway",
            task_id=task_id,
            seq=seq,
            tool=tool,
            bucket=bucket,
            tokens=tokens,
            bytes=n_bytes,
            counted_in_metric=counted,
            path=path,
            line_range=list(line_range) if line_range else None,
            latency_ms=latency_ms,
        )
        return entry

    def record_prompt(self, *, task_id: str, text: str, tool: str = "prompt") -> CostLedgerEntry:
        """System/task prompt: reported separately and excluded from the metric."""
        return self.record(task_id=task_id, tool=tool, payload=text, bucket=TOKEN_BUCKET_PROMPT)

    def record_candidate_call(self, record: CandidateCallRecord) -> None:
        self.candidate_calls.append(record)

    def ledger_rows(self) -> list[dict[str, Any]]:
        return [entry.model_dump() for entry in self.costs]

    def token_usage(self) -> TokenUsage:
        rows = self.ledger_rows()
        per_task = cumulative_by_task(rows)
        return TokenUsage(
            run_id=self.run_id,
            tokenizer=self.tokenizer,
            approximate=self.approximate,
            total_repository_tokens=total_repository_tokens(per_task),
            total_prompt_tokens=total_prompt_tokens(per_task),
            total_bytes=sum(int(r["bytes"]) for r in rows),
            total_interactions=len(rows),
            per_task=[
                TokenUsageTask(
                    task_id=task_id,
                    repository_tokens=values["repository_tokens"],
                    prompt_tokens=values["prompt_tokens"],
                    bytes_returned=values["bytes_returned"],
                    interactions=values["interactions"],
                )
                for task_id, values in per_task.items()
            ],
            per_tool=per_tool_totals(rows),
        )


def reconstruct_token_usage(events: list[dict[str, Any]], *, run_id: str, tokenizer: str,
                            approximate: bool) -> TokenUsage:
    """Rebuild `token-usage.json` from `events.jsonl` alone (gate P5)."""
    rows = [
        {
            "task_id": event.get("task_id") or "",
            "tool": event["payload"].get("tool", "unknown"),
            "bucket": event["payload"].get("bucket", TOKEN_BUCKET_REPOSITORY),
            "tokens": event["payload"].get("tokens", 0),
            "bytes": event["payload"].get("bytes", 0),
            "counted_in_metric": event["payload"].get("counted_in_metric", False),
        }
        for event in events
        if event.get("kind") == TOOL_COMPLETED
    ]
    per_task = cumulative_by_task(rows)
    return TokenUsage(
        run_id=run_id,
        tokenizer=tokenizer,
        approximate=approximate,
        total_repository_tokens=total_repository_tokens(per_task),
        total_prompt_tokens=total_prompt_tokens(per_task),
        total_bytes=sum(int(r["bytes"]) for r in rows),
        total_interactions=len(rows),
        per_task=[
            TokenUsageTask(
                task_id=task_id,
                repository_tokens=values["repository_tokens"],
                prompt_tokens=values["prompt_tokens"],
                bytes_returned=values["bytes_returned"],
                interactions=values["interactions"],
            )
            for task_id, values in per_task.items()
        ],
        per_tool=per_tool_totals(rows),
    )
