"""State directory layout, locking and crash recovery.

    $LEANVFS_DATA_DIR/<repo-fingerprint>/{index.sqlite,state.json,config-resolved.json,.lock}

Generated state is disposable (product invariant 2): deleting the directory costs a
resync and nothing else.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .hashing import repo_fingerprint

STATE_VERSION = 1


def default_data_dir() -> Path:
    env = os.environ.get("LEANVFS_DATA_DIR")
    if env:
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "leanvfs"
    if os.name == "nt":  # pragma: no cover
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "leanvfs" / "Cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "leanvfs"


def state_dir_for(repo_root: Path, override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return default_data_dir() / repo_fingerprint(str(Path(repo_root).resolve()))


@dataclass
class IndexState:
    state_version: int = STATE_VERSION
    repo_root: str = ""
    generation: int = 0
    idf_generation: int = 0
    idf_doc_count: int = 0
    files_added_since_idf: int = 0
    files_removed_since_idf: int = 0
    index_state: str = "ok"
    schema_version: int = 1
    config_digest: str = ""
    last_sync_kind: str = ""
    file_count: int = 0
    symbol_count: int = 0
    source_bytes: int = 0
    cold_index_ms: float = 0.0
    partial_transaction: bool = False
    stale_files: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2) + "\n"


def load_state(state_dir: Path) -> IndexState:
    path = state_dir / "state.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return IndexState()
    known = set(IndexState.__dataclass_fields__)
    return IndexState(**{k: v for k, v in data.items() if k in known})


def save_state(state_dir: Path, state: IndexState) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / "state.json.tmp"
    tmp.write_text(state.to_json())
    tmp.replace(state_dir / "state.json")


def write_resolved_config(state_dir: Path, cfg: Any) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "values": cfg.values,
        "sources": cfg.sources,
        "path_rules": cfg.path_rules,
        "digest": cfg.digest(),
    }
    tmp = state_dir / "config-resolved.json.tmp"
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n")
    tmp.replace(state_dir / "config-resolved.json")


class LockError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover
        return False
    return True


class Lock:
    """Advisory lock with stale-PID detection."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / ".lock"
        self.acquired = False
        self.stole = False

    def acquire(self, *, force: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                pid = int(data.get("pid", -1))
            except (OSError, ValueError):
                pid = -1
            if not force and pid != os.getpid() and _pid_alive(pid):
                raise LockError(f"index is locked by live process {pid} ({self.path})")
            self.stole = True
        self.path.write_text(
            json.dumps({"pid": os.getpid(), "acquired_monotonic": time.monotonic()})
        )
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            with contextlib.suppress(OSError):  # best-effort teardown
                self.path.unlink()
            self.acquired = False

    def __enter__(self) -> Lock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def recover(state_dir: Path) -> list[str]:
    """Detect and clean orphaned temp files. Never triggers a full rebuild."""
    actions: list[str] = []
    if not state_dir.exists():
        return actions
    for tmp in sorted(state_dir.glob("*.tmp")):
        try:
            tmp.unlink()
            actions.append(f"removed orphaned temp file {tmp.name}")
        except OSError:  # pragma: no cover
            pass
    lock = state_dir / ".lock"
    if lock.exists():
        try:
            pid = int(json.loads(lock.read_text()).get("pid", -1))
        except (OSError, ValueError):
            pid = -1
        if not _pid_alive(pid):
            try:
                lock.unlink()
                actions.append(f"removed stale lock from dead pid {pid}")
            except OSError:  # pragma: no cover
                pass
    return actions
