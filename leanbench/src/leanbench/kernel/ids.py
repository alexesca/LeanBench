"""Run identifiers: `lb_` + 8 uppercase base32 characters."""

from __future__ import annotations

import base64
import hashlib
import os
import re

RUN_ID_PATTERN = re.compile(r"^lb_[A-Z2-7]{8}$")
_ALPHABET_LEN = 8


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")[:_ALPHABET_LEN]


def new_run_id() -> str:
    """Fresh id from OS entropy. Never feeds a metric — only names a directory."""
    return "lb_" + _b32(os.urandom(8))


def run_id_from_seed(seed: str) -> str:
    """Deterministic id, for reproducible runs (`--run-id-seed`) and tests."""
    return "lb_" + _b32(hashlib.blake2b(seed.encode("utf-8"), digest_size=8).digest())


def is_run_id(value: str) -> bool:
    return bool(RUN_ID_PATTERN.match(value))
