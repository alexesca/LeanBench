"""Counters and durations. Everything surfaces through get_stats / `leanvfs stats`."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

COUNTERS = (
    "files_discovered",
    "files_hashed",
    "hash_skips",
    "structure_hash_skips",
    "parse_full",
    "parse_incremental",
    "symbols_added",
    "symbols_removed",
    "symbols_changed",
    "facts_created",
    "facts_dropped_by_budget",
    "relationships_created",
    "relationships_by_tier_R0",
    "relationships_by_tier_R1",
    "relationships_by_tier_R2",
    "relationships_by_tier_R3",
    "relationships_by_tier_R4",
    "unresolved_refs_pending",
    "re_resolutions",
    "queries",
    "cache_hit",
    "cache_miss",
    "secrets_redacted",
)

DURATIONS = (
    "discovery",
    "hashing",
    "parsing",
    "extraction",
    "resolution",
    "budget",
    "commit",
    "render",
    "search",
)


class Telemetry:
    def __init__(self) -> None:
        self.counters: dict[str, int] = dict.fromkeys(COUNTERS, 0)
        self.durations: dict[str, float] = dict.fromkeys(DURATIONS, 0.0)

    def incr(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def add(self, name: str, seconds: float) -> None:
        self.durations[name] = self.durations.get(name, 0.0) + seconds

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - start)

    def flush(self, store) -> None:
        for name, value in sorted(self.counters.items()):
            if value:
                store.incr(name, value)
        for name, value in sorted(self.durations.items()):
            if value:
                store.add_duration(name, value)
        self.counters = dict.fromkeys(COUNTERS, 0)
        self.durations = dict.fromkeys(DURATIONS, 0.0)
