"""Dataset identity.

A backtest result is only meaningful next to the exact data it was computed
from. Binance.US can and does revise history (late trades, re-published
candles), so "BTCUSD 1d from 2024-01-01" is not an identifier -- the hash of
the actual rows is.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..decimals import format_decimal
from ..errors import ConfigError
from ..models import MarketBar, MarketQuote
from ..timeutil import interval_to_ms


@dataclass(frozen=True, slots=True)
class DataGap:
    """A stretch of missing bars.

    Crypto trades continuously, so consecutive bars of a fixed-width interval
    should be exactly one interval apart. Anything wider is a venue outage, a
    delisting, or a trading suspension -- and a backtest that steps over one
    without noticing is reporting a result for a market that was not there.

    Binance.US USD pairs have a real 586-day gap between 2023-07-14 and
    2025-02-19, covering the period USD trading was suspended. Any USD-quoted
    window spanning it is invalid. The USDT-quoted pairs are continuous.
    """

    symbol: str
    after_open_time: int
    before_open_time: int
    missing_intervals: int

    def describe(self) -> str:
        from ..timeutil import to_iso

        return (
            f"{self.symbol}: {self.missing_intervals} missing bar(s) between "
            f"{to_iso(self.after_open_time)} and {to_iso(self.before_open_time)}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "after_open_time": self.after_open_time,
            "before_open_time": self.before_open_time,
            "missing_intervals": self.missing_intervals,
        }


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """Content hash and shape of the market data used by one run."""

    sha256: str
    bar_count: int
    symbol_count: int
    first_open_time: int
    last_close_time: int
    interval: str
    symbols: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "bar_count": self.bar_count,
            "symbol_count": self.symbol_count,
            "first_open_time": self.first_open_time,
            "last_close_time": self.last_close_time,
            "interval": self.interval,
            "symbols": list(self.symbols),
        }


def build_dataset_version(
    bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    quotes_by_symbol: Mapping[str, Sequence[MarketQuote]] | None = None,
) -> DatasetVersion:
    """Hash every bar that a run can see.

    The digest covers the values a strategy could act on -- symbol, interval,
    times and OHLCV -- but not ``source_received_at``, because re-downloading
    identical history on a different day must not invent a new dataset
    version.
    """
    digest = hashlib.sha256()
    total = 0
    first_open = 0
    last_close = 0
    intervals: set[str] = set()

    for symbol in sorted(bars_by_symbol):
        bars = bars_by_symbol[symbol]
        digest.update(f"|S|{symbol}|{len(bars)}".encode("utf-8"))
        for bar in bars:
            intervals.add(bar.interval)
            digest.update(
                "|".join(
                    (
                        bar.exchange,
                        bar.symbol,
                        bar.interval,
                        str(bar.open_time),
                        str(bar.close_time),
                        str(bar.feed_delay_ms),
                        format_decimal(bar.open),
                        format_decimal(bar.high),
                        format_decimal(bar.low),
                        format_decimal(bar.close),
                        format_decimal(bar.volume),
                        "" if bar.quote_volume is None else format_decimal(bar.quote_volume),
                    )
                ).encode("utf-8")
            )
            total += 1
            first_open = bar.open_time if first_open == 0 else min(first_open, bar.open_time)
            last_close = max(last_close, bar.close_time)

    for symbol, quotes in sorted((quotes_by_symbol or {}).items()):
        for quote in sorted(quotes, key=lambda q: (q.observable_at, q.source_received_at)):
            digest.update(repr((symbol, quote.exchange, quote.exchange_event_time,
                                quote.source_received_at, format_decimal(quote.bid),
                                format_decimal(quote.ask),
                                None if quote.bid_size is None else format_decimal(quote.bid_size),
                                None if quote.ask_size is None else format_decimal(quote.ask_size))).encode())

    if len(intervals) > 1:
        raise ConfigError(
            f"dataset mixes intervals {sorted(intervals)}; one run uses one bar interval"
        )

    return DatasetVersion(
        sha256=digest.hexdigest(),
        bar_count=total,
        symbol_count=len(bars_by_symbol),
        first_open_time=first_open,
        last_close_time=last_close,
        interval=next(iter(intervals)) if intervals else "",
        symbols=tuple(sorted(bars_by_symbol)),
    )


def find_gaps(
    bars_by_symbol: Mapping[str, Sequence[MarketBar]], interval: str
) -> tuple[DataGap, ...]:
    """Every stretch of missing bars, per symbol.

    Calendar-month intervals are skipped: month widths vary, so a month-to-month
    step cannot be checked arithmetically.
    """
    if not interval:
        return ()
    try:
        width = interval_to_ms(interval)
    except ValueError:
        return ()

    gaps: list[DataGap] = []
    for symbol in sorted(bars_by_symbol):
        bars = bars_by_symbol[symbol]
        for earlier, later in zip(bars, bars[1:]):
            delta = later.open_time - earlier.open_time
            if delta > width:
                gaps.append(
                    DataGap(
                        symbol=symbol,
                        after_open_time=earlier.open_time,
                        before_open_time=later.open_time,
                        missing_intervals=delta // width - 1,
                    )
                )
    return tuple(gaps)


@dataclass(slots=True)
class MarketDataSet:
    """All market data one backtest run may see, plus its content hash."""

    bars_by_symbol: dict[str, list[MarketBar]]
    quotes_by_symbol: dict[str, list[MarketQuote]] = field(default_factory=dict)
    version: DatasetVersion | None = None
    gaps: tuple[DataGap, ...] = ()

    def __post_init__(self) -> None:
        for symbol, bars in self.bars_by_symbol.items():
            ordered = sorted(bars, key=lambda bar: (bar.open_time, bar.close_time))
            if any(
                a.open_time == b.open_time for a, b in zip(ordered, ordered[1:])
            ):
                raise ConfigError(f"{symbol}: duplicate bar open_time in dataset")
            self.bars_by_symbol[symbol] = ordered
        for symbol, quotes in self.quotes_by_symbol.items():
            self.quotes_by_symbol[symbol] = sorted(
                quotes, key=lambda quote: quote.observable_at
            )
        if self.version is None:
            self.version = build_dataset_version(self.bars_by_symbol, self.quotes_by_symbol)
        if not self.gaps:
            self.gaps = find_gaps(self.bars_by_symbol, self.version.interval)

    def largest_gap(self) -> DataGap | None:
        if not self.gaps:
            return None
        return max(self.gaps, key=lambda gap: gap.missing_intervals)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.bars_by_symbol))

    @property
    def interval(self) -> str:
        assert self.version is not None
        return self.version.interval

    def all_bars(self) -> Iterable[MarketBar]:
        for symbol in sorted(self.bars_by_symbol):
            yield from self.bars_by_symbol[symbol]

    def time_bounds(self) -> tuple[int, int]:
        """(earliest observable time, latest observable time) across all data."""
        times = [bar.observable_at for bar in self.all_bars()]
        for quotes in self.quotes_by_symbol.values():
            times.extend(quote.observable_at for quote in quotes)
        if not times:
            raise ConfigError("dataset is empty")
        return (min(times), max(times))
