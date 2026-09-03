"""The single redaction choke point.

Product invariant 5 (*secrets never persist*) is enforced by a **mechanism**, not a
policy: a `Fact` cannot reach SQLite except wrapped in a `Redacted`, and a `Redacted`
cannot be constructed outside this module (its constructor demands a module-private
token). The store's raw insert is private and accepts only `Redacted`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .model import Fact

_TOKEN = object()

PLACEHOLDER_DEFAULT = "<redacted>"


class RedactionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Redacted:
    """A value that has provably passed through :func:`redact`."""

    fact: Fact
    was_redacted: bool

    def __init__(self, fact: Fact, was_redacted: bool, _token: Any = None) -> None:
        if _token is not _TOKEN:
            raise RedactionError(
                "Redacted() is private; construct facts via redact()/redact_fact()"
            )
        object.__setattr__(self, "fact", fact)
        object.__setattr__(self, "was_redacted", was_redacted)


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


_TOKEN_SPLIT = re.compile(r"[\s\"'`,;()\[\]{}<>=|]+")


class Redactor:
    """Config-driven secret detection. Retains the IDENTIFIER, never the value."""

    def __init__(self, cfg: Any) -> None:
        self.enabled = bool(cfg.get("redaction.enabled", True))
        self.key_patterns: list[str] = list(cfg.get("redaction.key_patterns", []))
        self.provider_prefixes: list[str] = list(cfg.get("redaction.provider_prefixes", []))
        self.value_patterns: list[str] = list(cfg.get("redaction.value_patterns", []))
        self.entropy_threshold = float(cfg.get("redaction.entropy_threshold", 4.0))
        self.entropy_min_length = int(cfg.get("redaction.entropy_min_length", 20))
        self.max_value_chars = int(cfg.get("redaction.max_value_chars", 200))
        self.placeholder = str(cfg.get("redaction.placeholder", PLACEHOLDER_DEFAULT))
        self.redacted_count = 0

    # -- detection ------------------------------------------------------
    def is_secret_key(self, identifier: str) -> bool:
        low = identifier.lower()
        return any(p in low for p in self.key_patterns)

    def is_secret_token(self, token: str) -> bool:
        if any(token.startswith(p) for p in self.provider_prefixes):
            return True
        return (
            len(token) >= self.entropy_min_length
            and shannon_entropy(token) > self.entropy_threshold
        )

    def scrub_text(self, text: str) -> tuple[str, bool]:
        """Replace secret-looking substrings inside free text."""
        if not self.enabled or not text:
            return text, False
        changed = False
        out = text
        for pattern in self.value_patterns:
            new = _compile(pattern).sub(self.placeholder, out)
            if new != out:
                changed = True
                out = new
        pieces = []
        pos = 0
        for m in _TOKEN_SPLIT.finditer(out):
            tok = out[pos : m.start()]
            if tok and self.is_secret_token(tok):
                pieces.append(self.placeholder)
                changed = True
            else:
                pieces.append(tok)
            pieces.append(m.group(0))
            pos = m.end()
        tail = out[pos:]
        if tail and self.is_secret_token(tail):
            pieces.append(self.placeholder)
            changed = True
        else:
            pieces.append(tail)
        out = "".join(pieces)
        if len(out) > self.max_value_chars:
            out = out[: self.max_value_chars]
            changed = True
        return out, changed

    def scrub_value(self, identifier: str, value: str) -> tuple[str, bool]:
        if not self.enabled:
            return value, False
        if identifier and self.is_secret_key(identifier):
            return self.placeholder, True
        return self.scrub_text(value)

    # -- the choke point ------------------------------------------------
    def redact(self, fact: Fact) -> Redacted:
        """The ONE path a fact takes to become storable.

        For ``name=value`` shaped kinds the IDENTIFIER is retained and only the
        value half is replaced, so ``STRIPE_API_KEY`` survives and its contents
        do not.
        """
        raw = fact.value
        if fact.kind in KV_KINDS and "=" in raw:
            key, _, val = raw.partition("=")
            new_val, changed = self.scrub_value(key, val)
            value = f"{key}={new_val}"
        else:
            value, changed = self.scrub_value(_identifier_of(fact), raw)
        if changed:
            self.redacted_count += 1
        fact.value = value
        return Redacted(fact, changed, _TOKEN)

    def redact_all(self, facts: Iterable[Fact]) -> list[Redacted]:
        return [self.redact(f) for f in facts]

    def scrub_free_text(self, text: str) -> str:
        """For non-fact text (docstrings, signatures, roles) headed for storage."""
        out, changed = self.scrub_text(text)
        if changed:
            self.redacted_count += 1
        return out


#: Fact kinds whose value is shaped ``name=value``; the name half is preserved.
KV_KINDS = frozenset({"config_key", "resource", "framework_metadata", "route"})

#: Fact kinds that never carry a literal value, so key-name matching must not fire
#: (a parameter literally named ``password`` is signal we want to keep).
IDENTIFIER_ONLY_KINDS = frozenset(
    {"parameter", "keyword", "signature", "return_type", "return_shape", "type_use", "call"}
)


def _identifier_of(fact: Fact) -> str:
    if fact.kind in IDENTIFIER_ONLY_KINDS:
        return ""
    return fact.kind
