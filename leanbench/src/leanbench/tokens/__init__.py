"""Token counters. Exactly one is chosen per run and used for every candidate.

`ApproximateTokenCounter` always works and is honest about being approximate (`~`).
`ModelSpecificTokenCounter` requires tiktoken; if tiktoken is absent or the encoding
cannot be loaded it reports `available = False` and raises on use. It never falls back
to the approximate estimate wearing an exact label.
"""

from __future__ import annotations

import math

from leanbench.kernel.errors import ConfigError
from leanbench.kernel.registry import register
from leanbench.schemas.config import ResolvedConfig


class ApproximateTokenCounter:
    """chars / chars_per_token, rounded up. Deterministic and dependency-free."""

    approximate = True
    available = True

    def __init__(self, *, chars_per_token: float, min_tokens_nonempty: int) -> None:
        if chars_per_token <= 0:
            raise ConfigError("tokens.chars_per_token must be > 0")
        self.chars_per_token = chars_per_token
        self.min_tokens_nonempty = min_tokens_nonempty
        self.name = f"approximate:{chars_per_token:g}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(self.min_tokens_nonempty, math.ceil(len(text) / self.chars_per_token))


class ModelSpecificTokenCounter:
    """Real BPE counts via tiktoken. Honestly unavailable when tiktoken is not usable."""

    approximate = False

    def __init__(self, *, model: str, encoding: str) -> None:
        self.model = model
        self.encoding_name = encoding
        self.name = f"tiktoken:{encoding}"
        self._encoding = None
        self.available = False
        self.unavailable_reason: str | None = None
        try:
            import tiktoken
        except ImportError as exc:
            self.unavailable_reason = f"tiktoken not installed ({exc})"
            return
        try:
            self._encoding = tiktoken.get_encoding(encoding)
        except (ValueError, KeyError, OSError, ConnectionError) as exc:
            self.unavailable_reason = f"cannot load encoding {encoding!r}: {exc}"
            return
        self.available = True

    def count(self, text: str) -> int:
        if self._encoding is None:
            raise ConfigError(
                f"model-specific tokenizer unavailable: {self.unavailable_reason}. "
                "Set tokens.counter='approximate' to run with labelled estimates instead."
            )
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))


def build_token_counter(config: ResolvedConfig):
    """Config-selected counter. `tokens.counter` is 'approximate' or 'model'."""
    kind = config.get_str("tokens.counter")
    if kind == "approximate":
        return ApproximateTokenCounter(
            chars_per_token=config.get_float("tokens.chars_per_token"),
            min_tokens_nonempty=config.get_int("tokens.min_tokens_nonempty"),
        )
    if kind == "model":
        counter = ModelSpecificTokenCounter(
            model=config.get_str("tokens.model"),
            encoding=config.get_str("tokens.encoding"),
        )
        if not counter.available:
            raise ConfigError(
                f"tokens.counter='model' requested but unavailable: {counter.unavailable_reason}"
            )
        return counter
    raise ConfigError(f"tokens.counter must be 'approximate' or 'model', got {kind!r}")


register("token_counter", "approximate", ApproximateTokenCounter)
register("token_counter", "model", ModelSpecificTokenCounter)

__all__ = ["ApproximateTokenCounter", "ModelSpecificTokenCounter", "build_token_counter"]
