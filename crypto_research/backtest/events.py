"""The simulation event queue.

Ordering is (timestamp, kind priority, insertion sequence). The third term
matters: two events that land on the same millisecond with the same priority
must still resolve in a fixed order, or two runs of the same configuration
could diverge.

Priorities encode the causal order within a single millisecond:

* market data is ingested first, so a decision made at a bar close sees that
  bar;
* contributions land next, so cash that becomes available at time T is
  spendable by a decision at time T;
* the strategy then decides;
* orders reach the venue after the configured latency, and with zero latency
  that is still strictly after the decision;
* the portfolio is marked last, so the equity point for time T includes
  anything that happened at T.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterator


class EventKind(IntEnum):
    BAR_CLOSE = 0
    QUOTE = 1
    CONTRIBUTION = 2
    DECISION = 3
    EXECUTION = 4
    MARK = 5


@dataclass(frozen=True, slots=True, order=False)
class Event:
    time: int
    kind: EventKind
    sequence: int
    payload: Any = None

    def sort_key(self) -> tuple[int, int, int]:
        return (self.time, int(self.kind), self.sequence)


class EventQueue:
    """A deterministic min-heap of simulation events."""

    def __init__(self) -> None:
        self._heap: list[tuple[tuple[int, int, int], Event]] = []
        self._counter = itertools.count()

    def __len__(self) -> int:
        return len(self._heap)

    def push(self, time: int, kind: EventKind, payload: Any = None) -> Event:
        event = Event(time=time, kind=kind, sequence=next(self._counter), payload=payload)
        heapq.heappush(self._heap, (event.sort_key(), event))
        return event

    def pop(self) -> Event:
        _, event = heapq.heappop(self._heap)
        return event

    def drain(self) -> Iterator[Event]:
        while self._heap:
            yield self.pop()


@dataclass(slots=True)
class PendingExecution:
    """An order intent in flight between the strategy and the venue."""

    intent: Any
    decision_time: int
    decision_index: int
    extra: dict[str, Any] = field(default_factory=dict)
