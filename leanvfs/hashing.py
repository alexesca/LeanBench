"""Content hashing. No timestamps, no absolute paths, no dict-order dependence."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

DEFAULT_DIGEST_BYTES = 16


def _hasher(digest_bytes: int = DEFAULT_DIGEST_BYTES):
    return hashlib.blake2b(digest_size=digest_bytes)


def digest_bytes_(data: bytes, digest_bytes: int = DEFAULT_DIGEST_BYTES) -> str:
    h = _hasher(digest_bytes)
    h.update(data)
    return h.hexdigest()


def digest_text(text: str, digest_bytes: int = DEFAULT_DIGEST_BYTES) -> str:
    return digest_bytes_(text.encode("utf-8", "surrogatepass"), digest_bytes)


def digest_parts(parts: Iterable[object], digest_bytes: int = DEFAULT_DIGEST_BYTES) -> str:
    """Order-sensitive hash of a sequence of parts, separated unambiguously."""
    h = _hasher(digest_bytes)
    for part in parts:
        chunk = part if isinstance(part, bytes) else str(part).encode("utf-8", "surrogatepass")
        h.update(len(chunk).to_bytes(4, "big"))
        h.update(chunk)
    return h.hexdigest()


def repo_fingerprint(repo_root: str) -> str:
    """Fingerprint of a canonical absolute repository root."""
    return digest_text(repo_root, 16)
