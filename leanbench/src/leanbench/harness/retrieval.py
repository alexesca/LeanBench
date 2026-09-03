"""The Retrieval Track runner: issue each probe, capture the result, hand it to the
grader. Owns no scoring logic of its own.
"""

from __future__ import annotations

import json
from typing import Any

from leanbench.kernel.context import RunContext
from leanbench.kernel.errors import LeanBenchError
from leanbench.kernel.registry import register
from leanbench.schemas.task import Task


class RetrievalHarness:
    track = "retrieval"

    def __init__(self, context: RunContext, candidate: Any, recorder: Any) -> None:
        self.context = context
        self.candidate = candidate
        self.recorder = recorder
        config = context.config
        self.default_limit = config.get_int("retrieval.default_limit")
        budget = config.get_int("retrieval.token_budget")
        self.token_budget = budget if budget > 0 else None
        self.probe_format = config.get_str("retrieval.probe_format")

    def run_task(self, task: Task) -> dict[str, Any]:
        """Run every probe of one task. Probe order is the task's authored order; the
        grader re-sorts, so ordering here cannot affect a metric."""
        probes: list[dict[str, Any]] = []
        for probe in task.probes:
            args = dict(probe.args)
            if probe.op == "search" and "limit" not in args:
                args["limit"] = self.default_limit
            observation: dict[str, Any] = {
                "paraphrase_id": probe.paraphrase_id,
                "op": probe.op,
                "args": args,
            }
            try:
                response = self.candidate.call(
                    probe.op,
                    args,
                    task_id=task.id,
                    token_budget=self.token_budget,
                    response_format=self.probe_format,
                )
            except LeanBenchError as exc:
                self.context.record_failure(
                    component="harness.retrieval",
                    classification=exc.classification,
                    message=str(exc),
                    task_id=task.id,
                    op=probe.op,
                    stderr_tail=getattr(exc, "stderr_tail", "") or "",
                    exit_code=getattr(exc, "exit_code", None),
                )
                observation.update(
                    {
                        "failed": True,
                        "classification": exc.classification,
                        "result": {},
                        "tokens_returned": 0,
                        "latency_ms": 0.0,
                    }
                )
                probes.append(observation)
                continue

            result = response.result if response.status == "ok" else {}
            serialized = _canonical(result)
            entry = self.recorder.record(
                task_id=task.id,
                tool=f"candidate.{probe.op}",
                payload=serialized,
            )
            observation.update(
                {
                    "failed": False,
                    "classification": None,
                    "result": result,
                    "tokens_returned": entry.tokens,
                    "bytes_returned": entry.bytes,
                    "latency_ms": response.latency_ms,
                    "meta": response.meta,
                }
            )
            probes.append(observation)
        return {"task_id": task.id, "probes": probes}


def _canonical(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


register("harness", "retrieval", RetrievalHarness)
