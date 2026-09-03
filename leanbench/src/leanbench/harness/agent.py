"""The Agent Track runner.

An *agent policy* is any callable `(task, gateway, limits) -> answer text`. It receives
the gateway and nothing else, which is what makes the no-bypass guarantee structural
rather than aspirational. Two deterministic policies ship here so the track is runnable
and testable without a model; an LLM-backed policy plugs in at the same seam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from leanbench.kernel.context import RunContext
from leanbench.kernel.errors import LeanBenchError
from leanbench.kernel.registry import register
from leanbench.schemas.task import Task

DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
CONTEXT_LINES = 40


@dataclass
class AgentLimits:
    max_tool_calls: int
    max_repository_tokens: int
    wall_clock_s: float


class BudgetExhausted(Exception):
    """Raised inside a policy when a task limit is hit. Not a failure — a stop signal."""


class PolicyContext:
    """The only handle a policy gets on the world."""

    def __init__(self, gateway: Any, task_id: str, limits: AgentLimits) -> None:
        self._gateway = gateway
        self._task_id = task_id
        self._limits = limits
        self.tokens_used = 0
        self.calls_used = 0
        self.read_ranges: list[tuple[str, int, int]] = []

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        if self.calls_used >= self._limits.max_tool_calls:
            raise BudgetExhausted("tool call budget exhausted")
        if self.tokens_used >= self._limits.max_repository_tokens:
            raise BudgetExhausted("repository token budget exhausted")
        response = self._gateway.call(tool, args, task_id=self._task_id)
        self.calls_used += 1
        self.tokens_used += int(response.get("tokens", 0))
        if tool == "repo.read_range":
            self.read_ranges.append((str(args["path"]), int(args["start"]), int(args["end"])))
        return response


def read_all_policy(task: Task, ctx: PolicyContext) -> str:
    """Baseline: list the repository and read whole files until the budget runs out.

    This is the token-hungry strategy the benchmark exists to beat.
    """
    mentions: list[str] = []
    try:
        listing = ctx.call("repo.list")
        paths = [p for p in listing["result"].get("entries", []) if p.endswith(".py")]
        for path in paths:
            response = ctx.call("repo.read", path=path)
            if not response["ok"]:
                continue
            text = response["result"].get("text", "")
            mentions.append(path)
            mentions.extend(DEF_RE.findall(text))
    except BudgetExhausted:
        pass
    return " ".join(_unique(mentions))


def candidate_guided_policy(task: Task, ctx: PolicyContext) -> str:
    """Ask the candidate where to look, then read only those line ranges."""
    mentions: list[str] = []
    try:
        response = ctx.call("candidate.search", query=task.prompt.strip(), limit=10)
        if response["ok"]:
            for hit in response["result"].get("hits", []):
                path = hit.get("path")
                symbol = hit.get("symbol")
                if symbol:
                    mentions.append(symbol)
                if path:
                    mentions.append(path)
        for symbol in _unique(m for m in mentions if "/" not in m)[:3]:
            ctx_response = ctx.call("candidate.context", symbol=symbol)
            if ctx_response["ok"]:
                result = ctx_response["result"]
                mentions.extend(result.get("tests", []))
                mentions.extend(result.get("calls", []))
                if result.get("path") and result.get("line_start"):
                    start = int(result["line_start"])
                    ctx.call(
                        "repo.read_range",
                        path=result["path"],
                        start=start,
                        end=start + CONTEXT_LINES,
                    )
    except BudgetExhausted:
        pass
    return " ".join(_unique(mentions))


def _unique(values: Any) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(str(value))
    return seen


POLICIES = {
    "read-all": read_all_policy,
    "candidate-guided": candidate_guided_policy,
}


class AgentHarness:
    """Runs one policy over one task through the gateway."""

    track = "agent"

    def __init__(self, context: RunContext, gateway: Any, policy_name: str) -> None:
        self.context = context
        self.gateway = gateway
        self.policy_name = policy_name
        if policy_name not in POLICIES:
            raise LeanBenchError(f"unknown agent policy {policy_name!r}")
        self.policy = POLICIES[policy_name]
        config = context.config
        self.default_limits = AgentLimits(
            max_tool_calls=config.get_int("agent.max_tool_calls"),
            max_repository_tokens=config.get_int("agent.max_repository_tokens"),
            wall_clock_s=config.get_float("agent.wall_clock_s"),
        )

    def limits_for(self, task: Task) -> AgentLimits:
        return AgentLimits(
            max_tool_calls=task.limits.max_tool_calls or self.default_limits.max_tool_calls,
            max_repository_tokens=(
                task.limits.max_repository_tokens or self.default_limits.max_repository_tokens
            ),
            wall_clock_s=task.limits.wall_clock_s or self.default_limits.wall_clock_s,
        )

    def run_task(self, task: Task) -> dict[str, Any]:
        limits = self.limits_for(task)
        ctx = PolicyContext(self.gateway, task.id, limits)
        failed = False
        classification: str | None = None
        answer = ""
        try:
            answer = self.policy(task, ctx)
        except LeanBenchError as exc:
            failed = True
            classification = exc.classification
            self.context.record_failure(
                component="harness.agent",
                classification=exc.classification,
                message=str(exc),
                task_id=task.id,
            )
        return {
            "task_id": task.id,
            "answer": answer,
            "read_ranges": ctx.read_ranges,
            "repository_context_tokens": ctx.tokens_used,
            "baseline_tokens": limits.max_repository_tokens,
            "tool_calls": ctx.calls_used,
            "failed": failed,
            "classification": classification,
        }


register("harness", "agent", AgentHarness)
