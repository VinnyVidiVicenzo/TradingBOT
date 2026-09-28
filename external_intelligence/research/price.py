"""Price lookups that cannot see the future.

The price "at" a time ``t`` is the close of the last bar that had *completed and
become observable* by ``t`` -- the same rule the engine's ``MarketView`` uses. A
bar still forming at ``t`` is not visible, so a price at receipt never contains
information from after the receipt.

Resolution matters and is reported, not hidden. With daily bars, a 5-minute
forward return has no meaning (the next observable price is a day away); the
lookup then returns ``None`` instead of a stale number. A price that is too old to
be trusted (a gap in the bars) is likewise ``None``.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from crypto_research.models import MarketBar
from crypto_research.storage.repositories import BarRepository


@dataclass(frozen=True, slots=True)
class PricePoint:
    price: Decimal
    as_of: int  # when the bar this price comes from became observable

    def age_at(self, time_ms: int) -> int:
        return time_ms - self.as_of


class PriceSource(Protocol):
    @property
    def resolution_ms(self) -> int: ...

    @property
    def last_observable(self) -> int: ...

    def price_at(self, time_ms: int) -> PricePoint | None: ...

    def path(self, start_exclusive: int, end_inclusive: int) -> list[PricePoint]: ...


class BarPriceSource:
    """A price source over completed bars of one symbol and interval."""

    def __init__(
        self, bars: Sequence[MarketBar], *, extra_delay_ms: int = 0, max_age_bars: int = 2
    ) -> None:
        if not bars:
            raise ValueError("a price source needs at least one bar")
        ordered = sorted(bars, key=lambda b: b.open_time)
        self._closes = [b.close for b in ordered]
        self._observable = [b.observable_at + extra_delay_ms for b in ordered]
        if self._observable != sorted(self._observable):
            raise ValueError("bars are not in observable order")
        self._resolution = ordered[0].close_time - ordered[0].open_time + 1
        self._max_age = max_age_bars * self._resolution
        self.symbol = ordered[0].symbol
        self.interval = ordered[0].interval

    @classmethod
    def from_repository(
        cls, repository: BarRepository, symbol: str, interval: str, *,
        start_time: int | None = None, end_time: int | None = None, extra_delay_ms: int = 0,
    ) -> "BarPriceSource":
        return cls(
            repository.load(symbol, interval, start_time=start_time, end_time=end_time),
            extra_delay_ms=extra_delay_ms,
        )

    @property
    def resolution_ms(self) -> int:
        return self._resolution

    @property
    def last_observable(self) -> int:
        return self._observable[-1]

    @property
    def first_observable(self) -> int:
        return self._observable[0]

    def price_at(self, time_ms: int) -> PricePoint | None:
        index = bisect.bisect_right(self._observable, time_ms) - 1
        if index < 0:
            return None
        point = PricePoint(self._closes[index], self._observable[index])
        return None if point.age_at(time_ms) > self._max_age else point

    def path(self, start_exclusive: int, end_inclusive: int) -> list[PricePoint]:
        low = bisect.bisect_right(self._observable, start_exclusive)
        high = bisect.bisect_right(self._observable, end_inclusive)
        return [PricePoint(self._closes[i], self._observable[i]) for i in range(low, high)]

    def observable_times(self) -> list[int]:
        return list(self._observable)


def forward_return(source: PriceSource, entry_time: int, horizon_ms: int) -> tuple[float | None, str | None]:
    """Simple return from the price known at ``entry_time`` to the one known at ``entry + horizon``.

    ``(None, reason)`` when either price is unavailable or the two are the very
    same bar (the horizon is finer than the data can resolve).
    """
    start = source.price_at(entry_time)
    if start is None:
        return None, "no_price_at_entry"
    end_time = entry_time + horizon_ms
    if end_time > source.last_observable:
        return None, "horizon_beyond_data"
    end = source.price_at(end_time)
    if end is None:
        return None, "no_price_at_horizon"
    if end.as_of <= start.as_of:
        return None, "horizon_finer_than_resolution"
    return float(end.price / start.price - 1), None
