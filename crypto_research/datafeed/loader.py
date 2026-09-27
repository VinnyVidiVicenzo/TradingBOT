"""Load a run-scoped dataset out of the local store."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ..errors import ConfigError
from ..models import EXCHANGE_BINANCE_US
from ..storage.database import Database
from ..storage.repositories import BarRepository, QuoteRepository
from ..timeutil import to_iso
from .dataset import MarketDataSet


def load_dataset(
    db: Database,
    symbols: Sequence[str],
    interval: str,
    *,
    start_time: int,
    end_time: int,
    include_quotes: bool = False,
    exchange: str = EXCHANGE_BINANCE_US,
    require_all_symbols: bool = True,
    max_gap_intervals: int = 3,
    feed_delay_ms: int = 0,
) -> MarketDataSet:
    """Build the immutable dataset a single backtest run will see.

    A missing symbol is an error by default. Quietly running a three-asset
    experiment on two assets because the third failed to download is exactly
    the kind of silent scope change that makes research results wrong.
    """
    if not symbols:
        raise ConfigError("dataset needs at least one symbol")
    bars_repo = BarRepository(db)
    quotes_repo = QuoteRepository(db)

    bars_by_symbol = {}
    missing = []
    for symbol in symbols:
        bars = bars_repo.load(
            symbol,
            interval,
            start_time=start_time,
            end_time=end_time,
            exchange=exchange,
        )
        if not bars:
            missing.append(symbol)
            continue
        if feed_delay_ms:
            bars = [replace(bar, feed_delay_ms=feed_delay_ms) for bar in bars]
        bars_by_symbol[symbol] = bars

    if missing and require_all_symbols:
        raise ConfigError(
            f"no {interval} bars stored for {', '.join(missing)} between "
            f"{to_iso(start_time)} and {to_iso(end_time)}; download them first"
        )
    if not bars_by_symbol:
        raise ConfigError("dataset would be empty")

    quotes_by_symbol = {}
    if include_quotes:
        for symbol in bars_by_symbol:
            quotes = quotes_repo.load(
                symbol, start_time=start_time, end_time=end_time, exchange=exchange
            )
            if quotes:
                quotes_by_symbol[symbol] = quotes

    dataset = MarketDataSet(
        bars_by_symbol=bars_by_symbol, quotes_by_symbol=quotes_by_symbol
    )

    # A hole in the history is not a small inaccuracy. Binance.US USD pairs are
    # missing 586 days across the 2023-2025 USD trading suspension, and a run
    # that steps over that hole reports a result for a market that was closed.
    oversized = [
        gap for gap in dataset.gaps if gap.missing_intervals > max_gap_intervals
    ]
    if oversized:
        worst = max(oversized, key=lambda gap: gap.missing_intervals)
        raise ConfigError(
            f"market data has {len(oversized)} gap(s) wider than "
            f"{max_gap_intervals} {interval} bar(s); the largest is "
            f"{worst.describe()}. Binance.US suspended USD trading between "
            f"2023-07-14 and 2025-02-19, so USD-quoted pairs have no history "
            f"there -- choose a window on one side of it, use the USDT-quoted "
            f"pairs instead, or raise max_gap_intervals deliberately if the gap "
            f"is genuinely acceptable for this experiment."
        )

    return dataset
