"""The visible-market state and the read-only view handed to strategies.

Look-ahead prevention is structural. The engine feeds observations into
:class:`MarketState` as simulated time advances, and a strategy can only read
through a :class:`MarketView` pinned to the current decision time. There is no
handle on the full dataset anywhere in the strategy API, so a strategy cannot
peek at a future bar even by mistake -- and the assertions below catch the
case where the engine itself is fed out of order.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from ..decimals import ZERO, div
from ..errors import LookaheadError
from ..models import MarketBar, MarketQuote


class MarketState:
    """Everything observed so far, in arrival order."""

    def __init__(self, history_limit: int | None = None) -> None:
        self._bars: dict[str, list[MarketBar]] = {}
        self._last_quote: dict[str, MarketQuote] = {}
        self._history_limit = history_limit
        self._clock: int = 0

    # -- ingestion ---------------------------------------------------------

    def ingest_bar(self, bar: MarketBar) -> None:
        if bar.observable_at < self._clock:
            raise LookaheadError(
                f"{bar.symbol} bar observable at {bar.observable_at} arrived after "
                f"simulated time {self._clock}; the event stream is out of order"
            )
        series = self._bars.setdefault(bar.symbol, [])
        series.append(bar)
        if self._history_limit is not None and len(series) > self._history_limit:
            del series[: len(series) - self._history_limit]

    def ingest_quote(self, quote: MarketQuote) -> None:
        if quote.observable_at < self._clock:
            raise LookaheadError(
                f"{quote.symbol} quote observable at {quote.observable_at} arrived "
                f"after simulated time {self._clock}"
            )
        self._last_quote[quote.symbol] = quote

    def advance_clock(self, now: int) -> None:
        if now < self._clock:
            raise LookaheadError(f"simulated clock moved backwards: {self._clock} -> {now}")
        self._clock = now

    # -- reads -------------------------------------------------------------

    @property
    def clock(self) -> int:
        return self._clock

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._bars))

    def last_bar(self, symbol: str) -> MarketBar | None:
        series = self._bars.get(symbol)
        return series[-1] if series else None

    def history(self, symbol: str, count: int) -> tuple[MarketBar, ...]:
        series = self._bars.get(symbol, ())
        if count <= 0:
            return ()
        return tuple(series[-count:])

    def last_quote(self, symbol: str) -> MarketQuote | None:
        return self._last_quote.get(symbol)

    def visible_prices(self) -> dict[str, Decimal]:
        """Latest visible price per symbol, for valuation.

        A quote mid is preferred over a bar close when it is more recent,
        because it is closer to what could actually be transacted at.
        """
        prices: dict[str, Decimal] = {}
        for symbol, series in self._bars.items():
            if series:
                prices[symbol] = series[-1].close
        for symbol, quote in self._last_quote.items():
            bar = self.last_bar(symbol)
            if bar is None or quote.observable_at >= bar.observable_at:
                prices[symbol] = quote.mid
        return prices

    # -- ExecutionMarketView -----------------------------------------------

    def reference_price(self, symbol: str, at: int) -> tuple[Decimal | None, int]:
        """Best price known at ``at``, with the time it was observed."""
        bar = self.last_bar(symbol)
        quote = self._last_quote.get(symbol)
        best: tuple[Decimal | None, int] = (None, 0)
        if bar is not None and bar.observable_at <= at:
            best = (bar.close, bar.observable_at)
        if quote is not None and quote.observable_at <= at:
            if best[0] is None or quote.observable_at >= best[1]:
                best = (quote.mid, quote.observable_at)
        return best

    def observed_half_spread_fraction(self, symbol: str, at: int) -> Decimal | None:
        quote = self._last_quote.get(symbol)
        if quote is None or quote.observable_at > at or quote.mid <= 0:
            return None
        return div(quote.spread, quote.mid) / 2

    def bar_quote_volume(self, symbol: str, at: int) -> Decimal | None:
        bar = self.last_bar(symbol)
        if bar is None or bar.observable_at > at:
            return None
        return bar.quote_volume


class MarketView:
    """A strategy-facing window onto :class:`MarketState`, frozen at ``now``.

    Every accessor re-checks that what it is about to return was observable
    at or before ``now``. The check is redundant when the engine is correct,
    which is exactly why it is worth keeping: it turns an engine bug into a
    loud failure rather than an inflated backtest.
    """

    __slots__ = ("_state", "_now")

    def __init__(self, state: MarketState, now: int) -> None:
        self._state = state
        self._now = now

    @property
    def now(self) -> int:
        return self._now

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._state.symbols

    def last_bar(self, symbol: str) -> MarketBar | None:
        bar = self._state.last_bar(symbol)
        if bar is None:
            return None
        self._guard(bar.observable_at, f"{symbol} bar")
        return bar

    def last_close(self, symbol: str) -> Decimal | None:
        bar = self.last_bar(symbol)
        return bar.close if bar else None

    def history(self, symbol: str, count: int) -> tuple[MarketBar, ...]:
        bars = self._state.history(symbol, count)
        for bar in bars:
            self._guard(bar.observable_at, f"{symbol} bar")
        return bars

    def last_quote(self, symbol: str) -> MarketQuote | None:
        quote = self._state.last_quote(symbol)
        if quote is None:
            return None
        self._guard(quote.observable_at, f"{symbol} quote")
        return quote

    def prices(self) -> Mapping[str, Decimal]:
        prices = self._state.visible_prices()
        for symbol in prices:
            bar = self._state.last_bar(symbol)
            if bar is not None:
                self._guard(bar.observable_at, f"{symbol} bar")
        return prices

    def has_price(self, symbol: str) -> bool:
        price, observed_at = self._state.reference_price(symbol, self._now)
        return price is not None and price > ZERO and observed_at <= self._now

    def _guard(self, observable_at: int, what: str) -> None:
        if observable_at > self._now:
            raise LookaheadError(
                f"{what} observable at {observable_at} was exposed to a strategy "
                f"at decision time {self._now}"
            )


def bar_close_times(
    bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    *,
    start_time: int | None = None,
    end_time: int | None = None,
) -> tuple[int, ...]:
    """Sorted union of the times at which any bar becomes observable."""
    times: set[int] = set()
    for bars in bars_by_symbol.values():
        for bar in bars:
            moment = bar.observable_at
            if start_time is not None and moment < start_time:
                continue
            if end_time is not None and moment > end_time:
                continue
            times.add(moment)
    return tuple(sorted(times))
