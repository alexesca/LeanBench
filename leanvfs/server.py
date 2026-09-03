"""The LeanBench candidate protocol server (PROTOCOL.md).

stdout carries protocol traffic and nothing else — every diagnostic goes to stderr.
A stray print here would be classified by the benchmark as a protocol error, which is
exactly the right outcome and exactly why there is a single writer below.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from .config import load_config
from .indexer import Indexer
from .queries import QueryEngine
from .render.compact import render_context, render_hits
from .state import state_dir_for
from .store import Store

PROTOCOL_VERSION = 1

CAPABILITY_OPS = {
    "search": "search",
    "get_symbol": "symbols",
    "get_context": "context",
    "get_dependencies": "dependencies",
    "get_references": "references",
    "get_tests": "tests",
    "get_docs": "docs",
    "update_repository": "incremental",
}


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Server:
    def __init__(self, *, out: Any = None, err: Any = None) -> None:
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.store: Store | None = None
        self.engine: QueryEngine | None = None
        self.cfg: Any = None
        self.repo_root: Path | None = None
        self.running = True
        self.cold_index_ms = 0.0

    # -- io ----------------------------------------------------------------------
    def _write(self, obj: dict[str, Any]) -> None:
        self.out.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.out.flush()

    def log(self, message: str) -> None:
        self.err.write(message.rstrip() + "\n")
        self.err.flush()

    # -- dispatch ------------------------------------------------------------------
    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            self._write(
                {"id": "", "status": "error", "code": "invalid_args",
                 "message": f"malformed JSON request: {exc}", "retryable": False}
            )
            return

        req_id = str(request.get("id", ""))
        op = str(request.get("op", ""))
        args = request.get("args") or {}
        budget = request.get("token_budget")
        fmt = str(request.get("format") or "compact")

        started = time.perf_counter()
        try:
            result = self.dispatch(op, args, budget, fmt)
        except ProtocolError as exc:
            self._write(
                {"id": req_id, "status": "error", "code": exc.code,
                 "message": exc.message, "retryable": exc.retryable}
            )
            return
        except Exception as exc:  # a bug in us is `internal`, never a silent wrong answer
            self.log("internal error:\n" + traceback.format_exc())
            self._write(
                {"id": req_id, "status": "error", "code": "internal",
                 "message": f"{type(exc).__name__}: {exc}", "retryable": False}
            )
            return

        elapsed = (time.perf_counter() - started) * 1000.0
        payload, meta = result
        meta.setdefault("elapsed_ms", round(elapsed, 3))
        self._write({"id": req_id, "status": "ok", "result": payload, "meta": meta})

    def _meta(self, tokens: int, truncated: bool, dropped: dict[str, int] | None = None) -> dict[str, Any]:
        state = self.store.index_state() if self.store else "failed"
        return {
            "tokens_approx": int(tokens),
            "truncated": bool(truncated),
            "dropped": dropped or {},
            "index_state": state,
            "generation": self.store.generation() if self.store else 0,
            "idf_generation": self.store.idf_generation() if self.store else 0,
        }

    def _require_ready(self) -> QueryEngine:
        if self.engine is None:
            raise ProtocolError("not_prepared", "call prepare_repository first")
        return self.engine

    def dispatch(
        self, op: str, args: dict[str, Any], budget: int | None, fmt: str
    ) -> tuple[Any, dict[str, Any]]:
        if op == "prepare_repository":
            return self.prepare(args)
        if op == "get_stats":
            engine = self.engine
            stats = engine.get_stats() if engine else {"index_state": "failed"}
            stats["config_resolved"] = self.cfg.digest() if self.cfg else ""
            return stats, self._meta(0, False)
        if op == "shutdown":
            self.running = False
            return {"ok": True}, self._meta(0, False)

        engine = self._require_ready()
        if op == "search":
            payload = engine.search(str(args.get("query", "")), int(args.get("limit", 10)))
            return self._finish(payload, fmt, budget, render_hits)
        if op == "get_symbol":
            payload = engine.get_symbol(str(args.get("name", "")), int(args.get("limit", 10)))
            return self._finish(payload, fmt, budget, None)
        if op == "get_context":
            payload = engine.get_context(
                str(args.get("symbol", "")),
                int(args["token_budget"]) if args.get("token_budget") else budget,
            )
            return self._finish(payload, fmt, budget, render_context)
        if op == "get_dependencies":
            payload = engine.get_dependencies(str(args.get("path", "")))
            return self._finish(payload, fmt, budget, None)
        if op == "get_references":
            payload = engine.get_references(
                str(args.get("symbol", "")), int(args.get("limit", 50))
            )
            return self._finish(payload, fmt, budget, None)
        if op == "get_tests":
            payload = engine.get_tests(str(args.get("symbol", "")), int(args.get("limit", 25)))
            return self._finish(payload, fmt, budget, None)
        if op == "get_docs":
            payload = engine.get_docs(str(args.get("query", "")), int(args.get("limit", 5)))
            return self._finish(payload, fmt, budget, None)
        if op == "update_repository":
            return self.update(args)
        raise ProtocolError("unsupported_op", f"unknown op {op!r}")

    def _finish(
        self, payload: Any, fmt: str, budget: int | None, renderer: Any
    ) -> tuple[Any, dict[str, Any]]:
        engine = self._require_ready()
        dropped: dict[str, int] = {}
        truncated = False
        if isinstance(payload, dict) and isinstance(payload.get("budget_report"), dict):
            report = payload["budget_report"]
            dropped = {k: int(v) for k, v in (report.get("dropped") or {}).items()}
            truncated = bool(report.get("truncated"))
        if fmt == "compact" and renderer is not None:
            text = renderer(payload)
            tokens = engine.counter.count(text)
            return {"text": text}, self._meta(tokens, truncated, dropped)
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return payload, self._meta(engine.counter.count(text), truncated, dropped)

    # -- lifecycle ------------------------------------------------------------------
    def prepare(self, args: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        raw = args.get("path")
        if not raw:
            raise ProtocolError("invalid_args", "prepare_repository requires `path`")
        repo = Path(str(raw)).resolve()
        if not repo.is_dir():
            raise ProtocolError("not_found", f"repository not found: {repo}")

        self.repo_root = repo
        self.cfg = load_config(repo, skip_user_layers=True)
        state_dir = state_dir_for(repo, self.cfg.get("general.state_dir") or None)
        state_dir.mkdir(parents=True, exist_ok=True)
        db = state_dir / "index.sqlite"
        # A prepared repository is indexed from clean: the benchmark measures cold
        # index cost, and silently reusing a warm database would misreport it.
        if db.exists():
            db.unlink()
        for suffix in ("-wal", "-shm"):
            extra = db.with_name(db.name + suffix)
            if extra.exists():
                extra.unlink()

        self.store = Store(db)
        indexer = Indexer(repo, self.store, self.cfg, state_dir=state_dir)
        started = time.perf_counter()
        result = indexer.full_sync()
        self.cold_index_ms = (time.perf_counter() - started) * 1000.0
        self.store.set_meta("cold_index_ms", str(self.cold_index_ms))
        self.store.set_meta("source_bytes", str(result.source_bytes))
        self.engine = QueryEngine(self.store, self.cfg)
        self.log(
            f"indexed {result.files} files, {result.symbols} symbols in "
            f"{self.cold_index_ms:.0f}ms"
        )
        return (
            {
                "indexed": True,
                "files": result.files,
                "symbols": result.symbols,
                "cold_index_ms": round(self.cold_index_ms, 3),
                "index_bytes": self.store.size_bytes(),
            },
            self._meta(0, False),
        )

    def update(self, args: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        if self.store is None or self.repo_root is None or self.cfg is None:
            raise ProtocolError("not_prepared", "call prepare_repository first")
        started = time.perf_counter()
        indexer = Indexer(self.repo_root, self.store, self.cfg)
        result = indexer.full_sync()
        elapsed = (time.perf_counter() - started) * 1000.0
        self.engine = QueryEngine(self.store, self.cfg)
        return (
            {
                "updated": True,
                "update_ms": round(elapsed, 3),
                "files_reparsed": result.files,
                "generation": result.generation,
            },
            self._meta(0, False),
        )

    def serve(self, stream: Any = None) -> int:
        stream = stream or sys.stdin
        for line in stream:
            self.handle_line(line)
            if not self.running:
                break
        return 0


def main(argv: list[str] | None = None) -> int:
    return Server().serve()
