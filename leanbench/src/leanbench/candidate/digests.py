"""PROTOCOL.md §6 digests. BLAKE2b-256 everywhere, deterministic ordering."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DIGEST_SIZE = 32  # BLAKE2b-256
CHUNK_BYTES = 1 << 20
#: Directories never walked when hashing an interpreted candidate's source tree.
SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)


def _hasher() -> hashlib._Hash:
    return hashlib.blake2b(digest_size=DIGEST_SIZE)


def digest_bytes(data: bytes) -> str:
    h = _hasher()
    h.update(data)
    return h.hexdigest()


def digest_file(path: Path) -> str:
    h = _hasher()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def digest_source_tree(root: Path) -> str:
    """Sorted concatenation of the tree's per-file hashes (PROTOCOL.md §6)."""
    h = _hasher()
    for path in iter_source_files(root):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest_file(path).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def binary_digest(command: str, candidate_root: Path) -> str:
    """The resolved executable's hash if it is a file on disk; otherwise the candidate
    source tree's hash (interpreted candidates)."""
    resolved = shutil.which(command)
    if resolved:
        resolved_path = Path(resolved)
        # An interpreter (python, node, ...) says nothing about the candidate: hash the
        # source tree instead so that editing the candidate changes the digest.
        if not _is_interpreter(resolved_path.name):
            return digest_file(resolved_path)
    direct = Path(command)
    if direct.is_file() and not _is_interpreter(direct.name):
        return digest_file(direct)
    return digest_source_tree(candidate_root)


_INTERPRETERS = ("python", "python3", "node", "ruby", "perl", "sh", "bash", "uv", "uvx", "pypy")


def _is_interpreter(name: str) -> bool:
    stem = name.lower().removesuffix(".exe")
    return any(stem == i or stem.startswith(i + "3.") for i in _INTERPRETERS)


def canonical_json(data: Any) -> str:
    """Canonical serialization used for `config_digest`: sorted keys, tight separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_digest(config_resolved: Any | None) -> str | None:
    if config_resolved is None:
        return None
    return digest_bytes(canonical_json(config_resolved).encode("utf-8"))
