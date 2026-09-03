"""Counters. Ground rule: every handled failure increments one of these."""

from __future__ import annotations

from collections import Counter


class Counters:
    """Ordered, sorted-on-read counter bag. Deterministic serialization."""

    __slots__ = ("_counts",)

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def incr(self, name: str, amount: int = 1) -> int:
        self._counts[name] += amount
        return self._counts[name]

    def get(self, name: str) -> int:
        return self._counts.get(name, 0)

    def as_dict(self) -> dict[str, int]:
        return {k: self._counts[k] for k in sorted(self._counts)}

    def total(self, prefix: str = "") -> int:
        return sum(v for k, v in self._counts.items() if k.startswith(prefix))

    def __len__(self) -> int:
        return len(self._counts)
