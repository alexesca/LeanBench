"""Minimal manifest-driven client used by the baseline tests and the bench script.

This is *not* part of any baseline: it stands in for LeanBench's own runner so the
baselines can be exercised exactly as a third-party candidate would be.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "baselines" / "manifests"
BASELINES = ("raw", "ripgrep", "ctags", "minast")


class CandidateProcess:
    """Spawns a candidate from its manifest and speaks JSONL to it."""

    def __init__(self, manifest_path: Path, *, python: str | None = None) -> None:
        self.manifest_path = manifest_path
        self.manifest: dict[str, Any] = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        runtime = self.manifest["runtime"]
        cwd = (manifest_path.parent / runtime.get("cwd", ".")).resolve()
        command = runtime["command"]
        if command == "python":
            command = python or sys.executable
        env = dict(os.environ)
        env.update(runtime.get("env", {}))
        self.process = subprocess.Popen(  # noqa: S603
            [command, *runtime.get("args", [])],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._counter = 0

    @property
    def capabilities(self) -> set[str]:
        return {key for key, value in self.manifest.get("capabilities", {}).items() if value}

    @property
    def name(self) -> str:
        return str(self.manifest["candidate"]["name"])

    def request_raw(self, payload: dict[str, Any]) -> str:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"candidate exited unexpectedly; stderr:\n{stderr}")
            data = json.loads(line)
            if data.get("status") == "indexing":
                continue
            return line.rstrip("\n")

    def request(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        token_budget: int | None = None,
        fmt: str = "compact",
        req_id: str | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        payload: dict[str, Any] = {
            "id": req_id or str(self._counter),
            "op": op,
            "args": args or {},
            "format": fmt,
        }
        if token_budget is not None:
            payload["token_budget"] = token_budget
        return json.loads(self.request_raw(payload))

    def timed(self, op: str, args: dict[str, Any], **kwargs: Any) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        response = self.request(op, args, **kwargs)
        return response, (time.perf_counter() - started) * 1000.0

    def prepare(self, repo: Path) -> dict[str, Any]:
        return self.request("prepare_repository", {"path": str(repo)}, fmt="json")

    def close(self) -> int:
        try:
            self.request("shutdown", {})
        except (RuntimeError, json.JSONDecodeError, BrokenPipeError, ValueError):
            pass
        try:
            return self.process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return -1

    def __enter__(self) -> CandidateProcess:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def manifest_for(name: str) -> Path:
    return MANIFEST_DIR / f"{name}.toml"
