"""The candidate runner: launch, converse, classify, shut down, hard-kill.

Implements `ports.CandidatePort`. Every failure path in PROTOCOL.md §7 exits through
`_fail`, which raises the classified exception; nothing else raises bare.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from leanbench.candidate.classify import classify_error_response, error_for
from leanbench.candidate.digests import binary_digest, canonical_json, config_digest, digest_bytes
from leanbench.candidate.manifest import candidate_root, manifest_bytes
from leanbench.candidate.resources import ResourceMonitor
from leanbench.candidate.transport import JsonlTransport, RawLine, StreamClosed
from leanbench.kernel.capabilities import op_declared
from leanbench.kernel.errors import (
    BenchmarkInfrastructureError,
    CandidateCrash,
    LeanBenchError,
    ProtocolError,
)
from leanbench.kernel.registry import register
from leanbench.schemas.config import ResolvedConfig
from leanbench.schemas.events import CandidateCallRecord, ResourceSample
from leanbench.schemas.protocol import (
    CandidateDigests,
    CandidateManifest,
    ErrorResponse,
    OkResponse,
    ProgressResponse,
    Request,
)

TERMINAL_STATUSES = frozenset({"ok", "error"})
KNOWN_STATUSES = frozenset({"ok", "error", "indexing"})


class CandidateResponse:
    """A successful terminal response plus everything LeanBench measured about it."""

    __slots__ = ("code", "latency_ms", "meta", "op", "request_id", "result", "serialized", "status")

    def __init__(
        self,
        *,
        op: str,
        request_id: str,
        result: dict[str, Any],
        meta: dict[str, Any],
        latency_ms: float,
        serialized: str,
        status: str,
        code: str | None = None,
    ) -> None:
        self.op = op
        self.request_id = request_id
        self.result = result
        self.meta = meta
        self.latency_ms = latency_ms
        self.serialized = serialized
        self.status = status
        self.code = code

    @property
    def bytes_returned(self) -> int:
        return len(self.serialized.encode("utf-8"))


class SubprocessCandidate:
    """A candidate subprocess speaking PROTOCOL.md v1."""

    def __init__(
        self,
        manifest: CandidateManifest,
        config: ResolvedConfig,
        *,
        on_call: Any = None,
        on_event: Any = None,
    ) -> None:
        self.manifest = manifest
        self.config = config
        self.name = manifest.candidate.name
        self.version = manifest.candidate.version
        self._on_call = on_call
        self._on_event = on_event
        self._root = candidate_root(manifest)
        self._transport = JsonlTransport(
            [manifest.runtime.command, *manifest.runtime.args],
            cwd=self._root,
            env=dict(manifest.runtime.env),
            max_line_bytes=config.get_int("candidate.max_line_bytes"),
            stderr_tail_bytes=config.get_int("candidate.stderr_tail_bytes"),
            kill_grace_s=config.get_float("candidate.kill_grace_s"),
        )
        self._monitor: ResourceMonitor | None = None
        self._resources = ResourceSample(available=False, reason="not started")
        self._pending: dict[str, dict[str, Any]] = {}
        self._request_seq = 0
        self._started = False
        self._shutdown_sent = False
        self._stats: dict[str, Any] = {}
        self._call_index = 0

    # --- lifecycle ------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._transport.pid

    def declared_capabilities(self) -> frozenset[str]:
        return self.manifest.declared_capabilities

    def start(self) -> None:
        self._transport.start()
        self._monitor = ResourceMonitor(
            self._transport.pid,
            interval_s=self.config.get_float("candidate.resource_sample_interval_s"),
        )
        self._monitor.start()
        self._started = True
        self._emit("candidate_started", pid=self._transport.pid, command=self._command_list())
        # Startup probe: the first round trip must complete inside `startup_s`. This is
        # what turns "crashes immediately" and "prints a banner" into distinct verdicts.
        self._request("get_stats", {}, timeout_s=self.manifest.timeouts.startup_s, phase="startup")

    def _command_list(self) -> list[str]:
        return [self.manifest.runtime.command, *self.manifest.runtime.args]

    def prepare(self, path: str, commit: str) -> dict[str, Any]:
        response = self._request(
            "prepare_repository",
            {"path": str(Path(path).resolve()), "commit": commit},
            timeout_s=self.manifest.timeouts.prepare_s,
            phase="prepare",
        )
        return response.result

    def call(
        self,
        op: str,
        args: dict[str, Any],
        *,
        task_id: str | None = None,
        token_budget: int | None = None,
        response_format: str | None = None,
    ) -> CandidateResponse:
        """One op. Raises a classified `LeanBenchError` for every §7 failure mode."""
        if not op_declared(op, self.declared_capabilities()):
            raise error_for(
                "op_not_declared",
                f"op {op!r} requires a capability {self.name!r} never declared",
                task_id=task_id,
                op=op,
            )
        return self._request(
            op,
            args,
            timeout_s=self.manifest.timeouts.op_s,
            task_id=task_id,
            token_budget=token_budget,
            response_format=response_format,
        )

    def get_stats(self) -> dict[str, Any]:
        # JSON form: §4.10 results are a free-form object with reserved keys LeanBench
        # reads (including `config_resolved` for the config digest).
        response = self._request(
            "get_stats", {}, timeout_s=self.manifest.timeouts.op_s, response_format="json"
        )
        self._stats = response.result
        return response.result

    def shutdown(self) -> None:
        """Clean shutdown, then hard-kill the process group if it overstays."""
        if not self._started or self._shutdown_sent:
            self._finalize_resources()
            self._transport.cleanup()
            return
        self._shutdown_sent = True
        timeout = self.manifest.timeouts.shutdown_s
        try:
            self._request("shutdown", {}, timeout_s=timeout, phase="shutdown")
        except LeanBenchError as exc:
            self._emit("candidate_shutdown_unclean", reason=str(exc))
        self._transport.close_stdin()
        exit_code = self._transport.wait(timeout)
        if exit_code is None:
            self._emit("candidate_hard_killed", pid=self._transport.pid)
            self._transport.terminate_group()
            exit_code = self._transport.wait(self.config.get_float("candidate.kill_grace_s"))
        self._finalize_resources()
        self._transport.cleanup()
        self._emit("candidate_stopped", exit_code=exit_code)

    def _finalize_resources(self) -> None:
        if self._monitor is not None:
            self._resources = self._monitor.stop()
            self._monitor = None

    def resources(self) -> ResourceSample:
        if self._monitor is not None:
            self._monitor.sample_once()
            return self._monitor.snapshot()
        return self._resources

    def digests(self) -> CandidateDigests:
        return CandidateDigests(
            binary_digest=binary_digest(self.manifest.runtime.command, self._root),
            manifest_digest=digest_bytes(manifest_bytes(self.manifest)),
            config_digest=config_digest(self._stats.get("config_resolved")),
        )

    # --- request/response -----------------------------------------------------

    def _next_id(self) -> str:
        self._request_seq += 1
        return str(self._request_seq)

    def _request(
        self,
        op: str,
        args: dict[str, Any],
        *,
        timeout_s: float,
        task_id: str | None = None,
        token_budget: int | None = None,
        response_format: str | None = None,
        phase: str = "op",
    ) -> CandidateResponse:
        request_id = self._next_id()
        request = Request(
            id=request_id,
            op=op,
            args=args,
            token_budget=token_budget,
            format=response_format or self.config.get_str("candidate.default_format"),
        )
        payload = request.model_dump(exclude_none=False)
        started = time.perf_counter()
        self._emit("candidate_request", op=op, request_id=request_id, task_id=task_id, phase=phase)
        try:
            self._transport.send(payload)
        except CandidateCrash as exc:
            self._record_call(
                request_id, task_id, op, args, "crash", None, started, 0, 0, "candidate_crash"
            )
            raise self._crash(str(exc), task_id=task_id, op=op) from exc

        envelope = self._await_terminal(request_id, timeout_s, op=op, task_id=task_id)
        latency_ms = (time.perf_counter() - started) * 1000.0

        if isinstance(envelope, ErrorResponse):
            capability_declared = op_declared(op, self.declared_capabilities())
            classification = classify_error_response(
                envelope.code, capability_declared=capability_declared
            )
            serialized = canonical_json({"error": envelope.code, "message": envelope.message})
            self._record_call(
                request_id,
                task_id,
                op,
                args,
                "error",
                envelope.code,
                started,
                len(serialized.encode("utf-8")),
                0,
                classification,
            )
            if classification is not None:
                raise error_for(
                    "unsupported_op_for_declared_capability"
                    if classification == "candidate_protocol_error"
                    else (
                        "op_not_declared"
                        if classification == "unsupported_capability"
                        else "response_schema_violation"
                    ),
                    f"{op}: candidate returned error {envelope.code!r}: {envelope.message}",
                    task_id=task_id,
                    op=op,
                )
            return CandidateResponse(
                op=op,
                request_id=request_id,
                result={},
                meta={},
                latency_ms=latency_ms,
                serialized=serialized,
                status="error",
                code=envelope.code,
            )

        serialized = canonical_json(envelope.result)
        response = CandidateResponse(
            op=op,
            request_id=request_id,
            result=envelope.result,
            meta=envelope.meta.model_dump(),
            latency_ms=latency_ms,
            serialized=serialized,
            status="ok",
        )
        self._record_call(
            request_id,
            task_id,
            op,
            args,
            "ok",
            None,
            started,
            response.bytes_returned,
            0,
            None,
            truncated=envelope.meta.truncated,
            index_state=envelope.meta.index_state,
        )
        return response

    def _await_terminal(
        self, request_id: str, timeout_s: float, *, op: str, task_id: str | None
    ) -> OkResponse | ErrorResponse:
        """Wait for the terminal response for `request_id`. Out-of-order responses are
        buffered; progress responses reset the deadline (PROTOCOL.md §3)."""
        buffered = self._pending.pop(request_id, None)
        if buffered is not None:
            return self._validate(buffered, op=op, task_id=task_id)
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._timeout(op, timeout_s, task_id=task_id)
            item = self._transport.read_line(remaining)
            if item is None:
                if self._transport.poll() is not None:
                    raise self._crash(
                        f"{op}: candidate exited before answering", task_id=task_id, op=op
                    )
                raise self._timeout(op, timeout_s, task_id=task_id)
            if isinstance(item, StreamClosed):
                if item.reason == "line_too_long":
                    raise ProtocolError(
                        f"{op}: candidate wrote a stdout line beyond "
                        f"{self.config.get_int('candidate.max_line_bytes')} bytes",
                        task_id=task_id,
                        op=op,
                    )
                raise self._crash(
                    f"{op}: candidate closed stdout before answering ({item.reason})",
                    task_id=task_id,
                    op=op,
                )
            assert isinstance(item, RawLine)
            parsed = self._parse_line(item.text, op=op, task_id=task_id)
            status = parsed.get("status")
            if status == "indexing":
                progress = self._validate_progress(parsed, op=op, task_id=task_id)
                self._emit(
                    "candidate_progress",
                    op=op,
                    task_id=task_id,
                    request_id=progress.id,
                    progress=progress.progress,
                )
                deadline = time.monotonic() + timeout_s  # progress resets the op timeout
                continue
            if parsed.get("id") != request_id:
                self._pending[str(parsed.get("id"))] = parsed
                continue
            return self._validate(parsed, op=op, task_id=task_id)

    def _parse_line(self, text: str, *, op: str, task_id: str | None) -> dict[str, Any]:
        try:
            return self._transport.parse(text)
        except ProtocolError as exc:
            raise ProtocolError(f"{op}: {exc.message}", task_id=task_id, op=op) from exc

    def _validate(
        self, parsed: dict[str, Any], *, op: str, task_id: str | None
    ) -> OkResponse | ErrorResponse:
        status = parsed.get("status")
        if status not in KNOWN_STATUSES:
            raise error_for(
                "unknown_status",
                f"{op}: unknown response status {status!r}",
                task_id=task_id,
                op=op,
            )
        model = OkResponse if status == "ok" else ErrorResponse
        try:
            return model.model_validate(parsed)  # type: ignore[return-value]
        except ValueError as exc:
            raise error_for(
                "response_schema_violation",
                f"{op}: response failed the {status} schema: {exc}",
                task_id=task_id,
                op=op,
            ) from exc

    def _validate_progress(
        self, parsed: dict[str, Any], *, op: str, task_id: str | None
    ) -> ProgressResponse:
        try:
            return ProgressResponse.model_validate(parsed)
        except ValueError as exc:
            raise error_for(
                "response_schema_violation",
                f"{op}: progress response failed the schema: {exc}",
                task_id=task_id,
                op=op,
            ) from exc

    # --- failure helpers ------------------------------------------------------

    def _crash(self, message: str, *, task_id: str | None, op: str | None) -> LeanBenchError:
        exit_code = self._transport.poll()
        tail = self._transport.stderr_tail()
        self._emit("candidate_crash", op=op, task_id=task_id, exit_code=exit_code, stderr_tail=tail)
        error = CandidateCrash(
            f"{message} (exit_code={exit_code})", exit_code=exit_code, task_id=task_id, op=op
        )
        error.stderr_tail = tail  # type: ignore[attr-defined]
        return error

    def _timeout(self, op: str, timeout_s: float, *, task_id: str | None) -> LeanBenchError:
        return error_for(
            "no_terminal_response",
            f"{op}: no terminal response within {timeout_s}s",
            task_id=task_id,
            op=op,
        )

    def stderr_tail(self) -> str:
        return self._transport.stderr_tail()

    # --- instrumentation ------------------------------------------------------

    def _emit(self, kind: str, **payload: Any) -> None:
        if self._on_event is not None:
            self._on_event(kind, payload)

    def _record_call(
        self,
        request_id: str,
        task_id: str | None,
        op: str,
        args: dict[str, Any],
        status: str,
        code: str | None,
        started: float,
        bytes_returned: int,
        tokens_returned: int,
        classification: str | None,
        *,
        truncated: bool | None = None,
        index_state: str | None = None,
    ) -> None:
        if self._on_call is None:
            return
        self._call_index += 1
        self._on_call(
            CandidateCallRecord(
                seq=self._call_index,
                request_id=request_id,
                task_id=task_id,
                op=op,
                args_digest=digest_bytes(canonical_json(args).encode("utf-8")),
                status=status,
                code=code,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                bytes_returned=bytes_returned,
                tokens_returned=tokens_returned,
                truncated=truncated,
                index_state=index_state,
                classification=classification,
            )
        )

    def __enter__(self) -> SubprocessCandidate:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.shutdown()
        except BenchmarkInfrastructureError:
            self._transport.cleanup()


register("candidate", "subprocess", SubprocessCandidate)
