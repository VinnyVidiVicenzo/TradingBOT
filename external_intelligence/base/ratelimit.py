"""Rate-limit awareness.

A limiter that *reports* how long to wait, rather than sleeping: the caller
decides what to do, and tests never block. Exceeding a provider's limit is
treated as a data-quality event (see ``health``), never as a reason to act.
"""

from __future__ import annotations

from collections import deque

from ..errors import ProviderRateLimited
from .clock import Clock


class RateLimiter:
    """At most ``max_calls`` calls in any window of ``per_ms`` milliseconds."""

    def __init__(self, max_calls: int, per_ms: int, clock: Clock) -> None:
        if max_calls < 1 or per_ms < 1:
            raise ValueError("max_calls and per_ms must be positive")
        self.max_calls = max_calls
        self.per_ms = per_ms
        self._clock = clock
        self._calls: deque[int] = deque()

    def try_acquire(self) -> int:
        """0 if a call may go ahead now (and is recorded); otherwise the wait in ms."""
        now = self._clock.now_ms()
        while self._calls and self._calls[0] <= now - self.per_ms:
            self._calls.popleft()
        if len(self._calls) < self.max_calls:
            self._calls.append(now)
            return 0
        return self._calls[0] + self.per_ms - now

    def acquire_or_raise(self) -> None:
        wait = self.try_acquire()
        if wait > 0:
            raise ProviderRateLimited(
                f"local rate limit of {self.max_calls} per {self.per_ms} ms reached",
                retry_after_ms=wait,
            )
