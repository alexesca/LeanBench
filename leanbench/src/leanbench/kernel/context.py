"""RunContext — the single object threaded through a run. Owns identity, config, bus,
counters and the failure list. Owns no I/O and knows no implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from leanbench.kernel.bus import EventBus
from leanbench.kernel.counters import Counters
from leanbench.kernel.logging import ContextLogger, get_logger
from leanbench.schemas.common import INFRASTRUCTURE_CLASSIFICATIONS
from leanbench.schemas.config import ResolvedConfig
from leanbench.schemas.events import FailureRecord


@dataclass
class RunContext:
    run_id: str
    config: ResolvedConfig
    bus: EventBus
    run_dir: Path
    counters: Counters = field(default_factory=Counters)
    failures: list[FailureRecord] = field(default_factory=list)
    suite: str = ""
    candidate: str = ""
    track: str = "retrieval"

    def logger(self, component: str, task_id: str | None = None) -> ContextLogger:
        return get_logger(component, run_id=self.run_id, task_id=task_id)

    def record_failure(
        self,
        *,
        component: str,
        classification: str,
        message: str,
        task_id: str | None = None,
        op: str | None = None,
        stderr_tail: str = "",
        exit_code: int | None = None,
    ) -> FailureRecord:
        """The one and only way a handled failure is registered: counter + event + record."""
        self.counters.incr(f"failure.{classification}")
        self.counters.incr("failure.total")
        seq = self.bus.next_seq()
        record = FailureRecord(
            seq=seq,
            run_id=self.run_id,
            task_id=task_id,
            component=component,
            classification=classification,
            op=op,
            message=message,
            stderr_tail=stderr_tail,
            exit_code=exit_code,
        )
        self.failures.append(record)
        self.bus.emit(
            "failure",
            component,
            task_id=task_id,
            seq=seq,
            classification=classification,
            op=op,
            message=message,
            exit_code=exit_code,
        )
        self.logger(component, task_id).error(
            message, classification=classification, op=op, exit_code=exit_code
        )
        return record

    @property
    def infrastructure_failures(self) -> int:
        return sum(1 for f in self.failures if f.classification in INFRASTRUCTURE_CLASSIFICATIONS)

    def infrastructure_failure_rate(self, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return self.infrastructure_failures / denominator
