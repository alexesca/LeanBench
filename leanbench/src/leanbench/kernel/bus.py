"""Event bus. Synchronous, in-process, insertion-ordered, assigns the monotonic `seq`
that every downstream artifact orders by."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from leanbench.schemas.events import Event

Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self, run_id: str, *, clock: Callable[[], float] | None = None) -> None:
        self.run_id = run_id
        self._seq = 0
        self._subscribers: list[tuple[str, Subscriber]] = []
        self._clock = clock if clock is not None else time.time
        self._history: list[Event] = []
        self._keep_history = True

    def subscribe(self, name: str, fn: Subscriber) -> None:
        self._subscribers.append((name, fn))

    def next_seq(self) -> int:
        """Reserve a sequence number without emitting (records that pair with an event)."""
        self._seq += 1
        return self._seq

    def emit(
        self,
        kind: str,
        component: str,
        *,
        task_id: str | None = None,
        seq: int | None = None,
        **payload: object,
    ) -> Event:
        event = Event(
            seq=self._seq + 1 if seq is None else seq,
            run_id=self.run_id,
            kind=kind,
            component=component,
            task_id=task_id,
            ts=self._clock(),
            payload=dict(payload),
        )
        if seq is None:
            self._seq += 1
        for _name, fn in self._subscribers:
            fn(event)
        if self._keep_history:
            self._history.append(event)
        return event

    def history(self) -> Iterator[Event]:
        return iter(self._history)

    def disable_history(self) -> None:
        self._keep_history = False
        self._history.clear()
