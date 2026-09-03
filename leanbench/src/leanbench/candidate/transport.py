"""JSONL subprocess transport (PROTOCOL.md §1).

Owns the process, the reader threads and nothing else: classification decisions live in
`runner.py`. stdout carries protocol traffic only; stderr is captured separately and
never affects grading.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leanbench.kernel.errors import BenchmarkInfrastructureError, CandidateCrash, ProtocolError


@dataclass(frozen=True)
class RawLine:
    """One line off the candidate's stdout, undecoded."""

    text: str


@dataclass(frozen=True)
class StreamClosed:
    """stdout reached EOF."""

    reason: str = "eof"


class JsonlTransport:
    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        max_line_bytes: int,
        stderr_tail_bytes: int,
        kill_grace_s: float,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.max_line_bytes = max_line_bytes
        self.stderr_tail_bytes = stderr_tail_bytes
        self.kill_grace_s = kill_grace_s
        self.process: subprocess.Popen[bytes] | None = None
        self._queue: queue.Queue[RawLine | StreamClosed] = queue.Queue()
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_bytes = 0
        self._stderr_lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # --- lifecycle ------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    def resolved_command(self) -> list[str]:
        """`python` in a manifest means "the interpreter running LeanBench".

        Resolving it keeps manifests portable across venvs instead of depending on
        whatever a bare `python` happens to mean on the launching shell's PATH.
        """
        if not self.command:
            return list(self.command)
        head, *rest = self.command
        if head in ("python", "python3"):
            return [sys.executable, *rest]
        return list(self.command)

    def start(self) -> None:
        merged_env = dict(os.environ)
        merged_env.update(self.env)
        merged_env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self.process = subprocess.Popen(
                self.resolved_command(),
                cwd=str(self.cwd),
                env=merged_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                bufsize=0,
            )
        except (OSError, ValueError) as exc:
            raise BenchmarkInfrastructureError(
                f"cannot launch candidate {self.command!r}: {exc}"
            ) from exc
        self._threads = [
            threading.Thread(target=self._pump_stdout, name="lb-stdout", daemon=True),
            threading.Thread(target=self._pump_stderr, name="lb-stderr", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _pump_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        stream = self.process.stdout
        try:
            for raw in stream:
                if len(raw) > self.max_line_bytes:
                    self._queue.put(StreamClosed(reason="line_too_long"))
                    return
                self._queue.put(RawLine(raw.decode("utf-8", errors="replace").rstrip("\r\n")))
        except (OSError, ValueError) as exc:
            self._queue.put(StreamClosed(reason=f"stdout read error: {exc}"))
            return
        self._queue.put(StreamClosed())

    def _pump_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        stream = self.process.stderr
        try:
            for raw in stream:
                with self._stderr_lock:
                    self._stderr_chunks.append(raw)
                    self._stderr_bytes += len(raw)
                    while self._stderr_bytes > self.stderr_tail_bytes and self._stderr_chunks:
                        self._stderr_bytes -= len(self._stderr_chunks.popleft())
        except (OSError, ValueError):
            return

    def stderr_tail(self) -> str:
        with self._stderr_lock:
            data = b"".join(self._stderr_chunks)
        return data.decode("utf-8", errors="replace")[-self.stderr_tail_bytes :]

    # --- io -------------------------------------------------------------------

    def send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise BenchmarkInfrastructureError("transport not started")
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if "\n" in line:
            raise BenchmarkInfrastructureError("request contains an embedded newline")
        try:
            self.process.stdin.write((line + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CandidateCrash(
                f"candidate stdin closed while sending: {exc}", exit_code=self.process.poll()
            ) from exc

    def read_line(self, timeout_s: float) -> RawLine | StreamClosed | None:
        """Next stdout line, `StreamClosed` on EOF, or None when `timeout_s` elapses."""
        try:
            return self._queue.get(timeout=max(0.0, timeout_s))
        except queue.Empty:
            return None

    @staticmethod
    def parse(line: str) -> dict[str, Any]:
        """JSON object or `ProtocolError` — a non-JSON line on stdout is §7's
        `protocol_error`, distinct from a schema failure."""
        stripped = line.strip()
        if not stripped:
            raise ProtocolError("empty line on candidate stdout")
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            preview = stripped[:200]
            raise ProtocolError(f"non-JSON line on candidate stdout: {preview!r} ({exc})") from exc
        if not isinstance(parsed, dict):
            raise ProtocolError(f"stdout line is JSON but not an object: {type(parsed).__name__}")
        return parsed

    # --- teardown -------------------------------------------------------------

    def poll(self) -> int | None:
        return self.process.poll() if self.process is not None else None

    def wait(self, timeout_s: float) -> int | None:
        if self.process is None:
            return None
        try:
            return self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None

    def close_stdin(self) -> None:
        if self.process is not None and self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                return

    def terminate_group(self) -> None:
        """SIGTERM the process group, then SIGKILL after the configured grace."""
        if self.process is None or self.process.poll() is not None:
            return
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + self.kill_grace_s
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return
            time.sleep(0.01)
        self._signal_group(signal.SIGKILL)
        self.wait(self.kill_grace_s)

    def _signal_group(self, sig: int) -> None:
        assert self.process is not None
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.process.send_signal(sig)
            except (ProcessLookupError, OSError):
                return

    def cleanup(self) -> None:
        self.close_stdin()
        self.terminate_group()
        for stream in (
            getattr(self.process, "stdout", None),
            getattr(self.process, "stderr", None),
        ):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    continue
