"""The tool gateway (build spec §8.1).

The agent touches the repository ONLY through here. Every call is: validate args ->
dispatch -> measure latency -> measure payload bytes -> count tokens -> emit
`tool_called`/`tool_completed` -> return a normalized response.

There is deliberately no accessor that hands out the repository or the candidate: an
agent policy is given a `ToolGateway` and nothing else, so "read a file behind the
benchmark's back" is not expressible. `tests/test_gateway.py::test_no_bypass_path`
asserts that.
"""

from __future__ import annotations

import json
import time
from typing import Any

from leanbench.instrumentation import TOOL_CALLED, Recorder
from leanbench.kernel.errors import GatewayError, LeanBenchError
from leanbench.kernel.logging import get_logger
from leanbench.schemas.common import GATEWAY_TOOLS
from leanbench.schemas.config import ResolvedConfig

#: tool name -> (candidate op, required arg names, optional arg names)
CANDIDATE_TOOLS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "candidate.search": ("search", ("query",), ("limit",)),
    "candidate.symbol": ("get_symbol", ("name",), ()),
    "candidate.context": ("get_context", ("symbol",), ("token_budget",)),
    "candidate.dependencies": ("get_dependencies", ("path",), ()),
    "candidate.references": ("get_references", ("symbol",), ("limit",)),
    "candidate.tests": ("get_tests", ("symbol",), ()),
    "candidate.docs": ("get_docs", ("query",), ("limit",)),
}

REPO_TOOLS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "repo.list": ((), ("path",)),
    "repo.search": (("pattern",), ("limit",)),
    "repo.read": (("path",), ()),
    "repo.read_range": (("path", "start", "end"), ()),
    "repo.stat": (("path",), ()),
    "repo.patch": (("path", "content"), ("start", "end")),
}


class ToolResponse(dict):
    """A normalized tool result. Always `{tool, ok, result|error, tokens, bytes, latency_ms}`."""


class ToolGateway:
    """Mediates every repository and candidate interaction for one run."""

    def __init__(
        self,
        *,
        repository: Any,
        candidate: Any | None,
        recorder: Recorder,
        config: ResolvedConfig,
        run_id: str,
        allowed_tools: list[str] | None = None,
        allow_writes: bool = False,
        mutation: Any | None = None,
    ) -> None:
        self.__repository = repository
        self.__candidate = candidate
        self._recorder = recorder
        self._config = config
        self._run_id = run_id
        self._allowed = set(allowed_tools) if allowed_tools else set(GATEWAY_TOOLS)
        self._allow_writes = allow_writes
        self._mutation = mutation
        self._log = get_logger("gateway", run_id=run_id)
        self.call_counts: dict[str, int] = {}
        self.calls = 0

    # --- public API -----------------------------------------------------------

    @property
    def tools(self) -> list[str]:
        return sorted(self._allowed)

    def call(self, tool: str, args: dict[str, Any] | None = None, *, task_id: str = "") -> ToolResponse:
        args = dict(args or {})
        started = time.perf_counter()
        self._recorder.bus.emit(TOOL_CALLED, "gateway", task_id=task_id, tool=tool, args=args)
        self.calls += 1
        self.call_counts[tool] = self.call_counts.get(tool, 0) + 1
        try:
            self._validate(tool, args)
            result = self._dispatch(tool, args, task_id=task_id)
            ok = True
            error: str | None = None
        except GatewayError as exc:
            result, ok, error = {}, False, str(exc)
        except LeanBenchError as exc:
            # A candidate failure is the candidate's problem, but the agent still sees
            # (and is charged tokens for) the error string.
            result, ok, error = {}, False, f"{exc.classification}: {exc.message}"
        latency_ms = (time.perf_counter() - started) * 1000.0
        envelope: dict[str, Any] = {"tool": tool, "ok": ok}
        if ok:
            envelope["result"] = result
        else:
            envelope["error"] = error
        serialized = _serialize(envelope)
        entry = self._recorder.record(
            task_id=task_id,
            tool=tool,
            payload=serialized,
            path=args.get("path") if isinstance(args.get("path"), str) else None,
            line_range=_line_range(args),
            latency_ms=latency_ms,
        )
        return ToolResponse(
            tool=tool,
            ok=ok,
            result=result,
            error=error,
            tokens=entry.tokens,
            bytes=entry.bytes,
            latency_ms=latency_ms,
            serialized=serialized,
        )

    # --- validation -----------------------------------------------------------

    def _validate(self, tool: str, args: dict[str, Any]) -> None:
        if tool not in GATEWAY_TOOLS:
            raise GatewayError(f"unknown tool {tool!r}; known: {', '.join(GATEWAY_TOOLS)}")
        if tool not in self._allowed:
            raise GatewayError(f"tool {tool!r} is not allowed for this task")
        required, optional = (
            (CANDIDATE_TOOLS[tool][1], CANDIDATE_TOOLS[tool][2])
            if tool in CANDIDATE_TOOLS
            else REPO_TOOLS[tool]
        )
        missing = [name for name in required if name not in args]
        if missing:
            raise GatewayError(f"{tool}: missing required argument(s) {missing}")
        unknown = sorted(set(args) - set(required) - set(optional))
        if unknown:
            raise GatewayError(f"{tool}: unknown argument(s) {unknown}")

    # --- dispatch -------------------------------------------------------------

    def _dispatch(self, tool: str, args: dict[str, Any], *, task_id: str) -> dict[str, Any]:
        if tool in CANDIDATE_TOOLS:
            return self._call_candidate(tool, args, task_id=task_id)
        return self._call_repository(tool, args)

    def _call_candidate(self, tool: str, args: dict[str, Any], *, task_id: str) -> dict[str, Any]:
        if self.__candidate is None:
            raise GatewayError(f"{tool}: no candidate is attached to this run")
        op, _required, _optional = CANDIDATE_TOOLS[tool]
        budget = args.pop("token_budget", None)
        response = self.__candidate.call(op, args, task_id=task_id, token_budget=budget)
        return response.result if hasattr(response, "result") else response

    def _call_repository(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        repo = self.__repository
        if tool == "repo.list":
            entries = repo.list_files(args.get("path"))
            cap = self._config.get_int("gateway.max_list_entries")
            return self._truncate_list("entries", entries, cap)
        if tool == "repo.search":
            limit = min(
                int(args.get("limit", self._config.get_int("gateway.max_search_results"))),
                self._config.get_int("gateway.max_search_results"),
            )
            matches = repo.search(str(args["pattern"]), limit)
            return {"matches": matches, "count": len(matches)}
        if tool == "repo.read":
            return self._truncate_text(repo.read(str(args["path"])), path=str(args["path"]))
        if tool == "repo.read_range":
            start, end = int(args["start"]), int(args["end"])
            text = repo.read_range(str(args["path"]), start, end)
            payload = self._truncate_text(text, path=str(args["path"]))
            payload["start"], payload["end"] = start, end
            return payload
        if tool == "repo.stat":
            return repo.stat(str(args["path"]))
        if tool == "repo.patch":
            return self._patch(args)
        raise GatewayError(f"tool {tool!r} has no dispatch")

    def _patch(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self._allow_writes or self._mutation is None:
            raise GatewayError("repo.patch is disabled: this run has a read-only working copy")
        changed = self._mutation.apply(
            self.__repository.root,
            {
                "path": str(args["path"]),
                "content": str(args["content"]),
                "start": args.get("start"),
                "end": args.get("end"),
            },
        )
        return {"patched": True, **changed}

    # --- truncation (notices are counted, per build spec §8.2) ----------------

    def _truncate_text(self, text: str, *, path: str) -> dict[str, Any]:
        cap = self._config.get_int("gateway.max_read_bytes")
        raw = text.encode("utf-8")
        if len(raw) <= cap:
            return {"path": path, "text": text, "truncated": False}
        clipped = raw[:cap].decode("utf-8", errors="ignore")
        notice = self._config.get_str("gateway.truncation_notice")
        return {"path": path, "text": clipped + "\n" + notice, "truncated": True}

    def _truncate_list(self, key: str, values: list[str], cap: int) -> dict[str, Any]:
        if len(values) <= cap:
            return {key: values, "truncated": False, "count": len(values)}
        notice = self._config.get_str("gateway.truncation_notice")
        return {key: values[:cap], "truncated": True, "count": len(values), "notice": notice}


def _serialize(envelope: dict[str, Any]) -> str:
    """The exact string handed to the model: JSON envelope, field names included."""
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _line_range(args: dict[str, Any]) -> tuple[int, int] | None:
    if "start" in args and "end" in args:
        try:
            return int(args["start"]), int(args["end"])
        except (TypeError, ValueError):
            return None
    return None
