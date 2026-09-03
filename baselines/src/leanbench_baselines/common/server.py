"""JSONL protocol server plumbing shared by the four baselines (PROTOCOL.md §1-§4).

Invariants enforced here, once, for every baseline:

* stdout carries protocol traffic only -- diagnostics go to stderr;
* every response echoes ``id`` and carries a complete ``meta``;
* an op whose capability the candidate did not declare returns ``unsupported_op``;
* an op needing a prepared repository returns ``not_prepared`` before it was prepared;
* every exception is classified into a PROTOCOL.md §3 error code (never a bare except).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from leanbench_baselines.common.payload import Payload, Rendered, render
from leanbench_baselines.common.repo import Repository, RepositoryError

PROTOCOL_VERSION = 1

#: PROTOCOL.md §5 -- capability key gating each op.
CAPABILITY_FOR_OP: dict[str, str] = {
    "search": "search",
    "get_symbol": "symbols",
    "get_context": "context",
    "get_dependencies": "dependencies",
    "get_references": "references",
    "get_tests": "tests",
    "get_docs": "docs",
    "update_repository": "incremental",
}

UNGATED_OPS = frozenset({"prepare_repository", "get_stats", "shutdown"})


class OpError(Exception):
    """An error the candidate can report through the protocol."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def invalid_args(message: str) -> OpError:
    return OpError("invalid_args", message)


def not_found(message: str) -> OpError:
    return OpError("not_found", message)


class BaseServer:
    """Base class for a LeanBench candidate process."""

    NAME = "baseline"
    VERSION = "0.1.0"
    CAPABILITIES: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.repo: Repository | None = None
        self.repo_path: str | None = None
        self.generation = 0
        self.index_state = "ok"
        self.cold_index_ms = 0.0
        self.counters: dict[str, int] = {}
        self._prepare_extras: dict[str, Any] = {}

    # -- subclass hooks --------------------------------------------------------

    def build_index(self, repo: Repository) -> dict[str, Any]:
        """Build whatever index this rung has. Return prepare_repository extras."""
        raise NotImplementedError

    def reindex_paths(self, paths: list[str]) -> int:
        """Re-index the given repository-relative paths; return files reparsed."""
        raise OpError("unsupported_op", "update_repository is not implemented")

    def stats(self) -> dict[str, Any]:
        return {}

    # -- helpers for subclasses ------------------------------------------------

    def require_repo(self) -> Repository:
        if self.repo is None:
            raise OpError("not_prepared", "prepare_repository has not been called")
        return self.repo

    @staticmethod
    def arg_str(args: dict[str, Any], key: str, *, required: bool = True) -> str:
        value = args.get(key)
        if value is None:
            if required:
                raise invalid_args(f"missing required argument '{key}'")
            return ""
        if not isinstance(value, str):
            raise invalid_args(f"argument '{key}' must be a string")
        if required and not value.strip():
            raise invalid_args(f"argument '{key}' must be non-empty")
        return value

    @staticmethod
    def arg_int(args: dict[str, Any], key: str, default: int, *, maximum: int = 500) -> int:
        value = args.get(key, default)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid_args(f"argument '{key}' must be an integer")
        if value < 1:
            raise invalid_args(f"argument '{key}' must be >= 1")
        return min(value, maximum)

    def bump(self, counter: str, amount: int = 1) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + amount

    # -- ops implemented for every baseline ------------------------------------

    def op_prepare_repository(self, args: dict[str, Any]) -> Payload:
        path_arg = self.arg_str(args, "path")
        root = Path(path_arg)
        if not root.is_absolute():
            raise invalid_args("'path' must be absolute")
        if not root.is_dir():
            raise not_found(f"repository path does not exist: {path_arg}")
        resolved = str(root.resolve())
        if self.repo is not None and self.repo_path == resolved:
            # Idempotent for the same path (PROTOCOL.md §4.1).
            return self._structural(self._prepare_result())
        started = time.perf_counter()
        try:
            repo = Repository(root)
        except RepositoryError as exc:
            self.index_state = "failed"
            raise OpError("index_error", str(exc)) from exc
        self.repo = repo
        self.repo_path = resolved
        extras = self.build_index(repo)
        self.cold_index_ms = (time.perf_counter() - started) * 1000.0
        self.generation += 1
        self._prepare_extras = extras
        return self._structural(self._prepare_result())

    @staticmethod
    def _structural(header: dict[str, Any]) -> Payload:
        """Metadata result: machine-readable fields plus a one-line compact rendering."""
        text = " ".join(f"{key}={header[key]}" for key in sorted(header) if key != "counters")
        return Payload(header=header, header_text=text, structural=True)

    def _prepare_result(self) -> dict[str, Any]:
        repo = self.require_repo()
        result: dict[str, Any] = {
            "indexed": True,
            "files": len(repo.files),
            "symbols": 0,
            "cold_index_ms": round(self.cold_index_ms, 3),
            "index_bytes": 0,
        }
        result.update(self._prepare_extras)
        return result

    def op_update_repository(self, args: dict[str, Any]) -> Payload:
        repo = self.require_repo()
        started = time.perf_counter()
        paths: list[str] = []
        for key in ("changed", "added", "removed"):
            value = args.get(key, [])
            if value is None:
                continue
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise invalid_args(f"argument '{key}' must be a list of strings")
            paths.extend(value)
        repo.rescan()
        reparsed = self.reindex_paths(sorted(set(paths)))
        self.generation += 1
        return self._structural(
            {
                "updated": True,
                "update_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "files_reparsed": reparsed,
                "generation": self.generation,
            }
        )

    def op_get_stats(self, args: dict[str, Any]) -> Payload:  # noqa: ARG002
        header: dict[str, Any] = {
            "candidate": self.NAME,
            "version": self.VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": sorted(self.CAPABILITIES),
            "files": len(self.repo.files) if self.repo else 0,
            "source_bytes": self.repo.source_bytes if self.repo else 0,
            "cold_index_ms": round(self.cold_index_ms, 3),
            "index_state": self.index_state,
            "generation": self.generation,
            "counters": {key: self.counters[key] for key in sorted(self.counters)},
        }
        header.update(self.stats())
        return self._structural(header)

    # -- main loop -------------------------------------------------------------

    def dispatch(self, op: str, args: dict[str, Any], fmt: str, budget: int | None) -> Rendered:
        if op == "shutdown":
            return render(
                Payload(header={"ok": True}, header_text="ok", structural=True), fmt, budget
            )
        capability = CAPABILITY_FOR_OP.get(op)
        handler = getattr(self, f"op_{op}", None)
        if handler is None or (capability is not None and capability not in self.CAPABILITIES):
            raise OpError("unsupported_op", f"op '{op}' is not supported by {self.NAME}")
        if op not in UNGATED_OPS and self.repo is None:
            raise OpError("not_prepared", "prepare_repository has not been called")
        payload = handler(args)
        self.bump(f"op.{op}")
        return render(payload, fmt, budget)

    def _handle_line(self, line: str, out: TextIO) -> bool:
        """Process one request line. Returns False when the loop should stop."""
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            self._write(out, self._error("", "invalid_args", f"malformed JSON request: {exc}"))
            return True
        if not isinstance(request, dict):
            self._write(out, self._error("", "invalid_args", "request must be a JSON object"))
            return True

        req_id = request.get("id")
        req_id = req_id if isinstance(req_id, str) else str(req_id)
        op = request.get("op")
        if not isinstance(op, str):
            self._write(out, self._error(req_id, "invalid_args", "'op' must be a string"))
            return True
        args = request.get("args") or {}
        if not isinstance(args, dict):
            self._write(out, self._error(req_id, "invalid_args", "'args' must be an object"))
            return True
        fmt = request.get("format") or "compact"
        if fmt not in ("compact", "json"):
            self._write(out, self._error(req_id, "invalid_args", f"unknown format '{fmt}'"))
            return True
        budget = request.get("token_budget")
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int)):
            self._write(out, self._error(req_id, "invalid_args", "'token_budget' must be an int"))
            return True
        # `get_context` also accepts the budget inside args (PROTOCOL.md §4.4 example).
        arg_budget = args.get("token_budget")
        if budget is None and isinstance(arg_budget, int) and not isinstance(arg_budget, bool):
            budget = arg_budget
        if budget is not None and budget < 1:
            self._write(out, self._error(req_id, "invalid_args", "'token_budget' must be >= 1"))
            return True

        started = time.perf_counter()
        try:
            rendered = self.dispatch(op, args, fmt, budget)
        except OpError as exc:
            self._write(out, self._error(req_id, exc.code, exc.message, retryable=exc.retryable))
            return True
        except RepositoryError as exc:
            self._write(out, self._error(req_id, "index_error", str(exc)))
            return True
        except (OSError, RecursionError, MemoryError) as exc:
            self._write(out, self._error(req_id, "internal", f"{type(exc).__name__}: {exc}"))
            return True
        except Exception as exc:  # classified, never swallowed
            sys.stderr.write(f"[{self.NAME}] internal error on op={op}: {exc!r}\n")
            self._write(out, self._error(req_id, "internal", f"{type(exc).__name__}: {exc}"))
            return True

        elapsed = (time.perf_counter() - started) * 1000.0
        meta: dict[str, Any] = {
            "tokens_approx": rendered.tokens_approx,
            "truncated": rendered.truncated,
            "index_state": self.index_state,
            "generation": self.generation,
            "elapsed_ms": round(elapsed, 3),
        }
        if rendered.dropped:
            meta["dropped"] = rendered.dropped
        self._write(out, {"id": req_id, "status": "ok", "result": rendered.result, "meta": meta})
        return op != "shutdown"

    @staticmethod
    def _error(
        req_id: str, code: str, message: str, *, retryable: bool = False
    ) -> dict[str, Any]:
        return {
            "id": req_id,
            "status": "error",
            "code": code,
            "message": message,
            "retryable": retryable,
        }

    @staticmethod
    def _write(out: TextIO, obj: dict[str, Any]) -> None:
        out.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        out.write("\n")
        out.flush()

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        source = stdin if stdin is not None else sys.stdin
        sink = stdout if stdout is not None else sys.stdout
        for line in source:
            if not line.strip():
                continue
            if not self._handle_line(line, sink):
                return 0
        # EOF without shutdown: exit cleanly rather than crash.
        return 0


def main(server_cls: type[BaseServer]) -> int:
    server = server_cls()
    return server.serve()
