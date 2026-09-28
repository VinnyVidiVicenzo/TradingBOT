"""An injectable clock.

The wall clock is passed in, never reached for, so a test (or a simulation) can
hold time still and so nothing simulated can quietly depend on "now".
"""

from __future__ import annotations

from typing import Protocol

from crypto_research.timeutil import now_ms


class Clock(Protocol):
    def now_ms(self) -> int: ...


class SystemClock:
    """The real clock. Used by live ingestion only."""

    def now_ms(self) -> int:
        return now_ms()


class FixedClock:
    """A clock that only moves when told to."""

    def __init__(self, start_ms: int) -> None:
        self._now = start_ms

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("a clock does not run backwards")
        self._now += ms

    def set(self, ms: int) -> None:
        self._now = ms
