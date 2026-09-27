"""Backfill public Binance.US data into the local store.

Downloads are resumable and idempotent: re-running the same command tops up
from the last stored bar instead of refetching the range.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..binanceus.public_client import BinanceUSPublicClient
from ..errors import ConfigError
from ..models import EXCHANGE_BINANCE_US
from ..storage.database import Database
from ..storage.repositories import (
    BarRepository,
    QuoteRepository,
    RawSnapshotRepository,
    RuleSnapshotRepository,
)
from ..timeutil import interval_to_ms, now_ms, to_iso


@dataclass(slots=True)
class DownloadReport:
    """What a download actually did, for printing and for the run record."""

    symbols: tuple[str, ...] = ()
    interval: str = ""
    bars_written: int = 0
    quotes_written: int = 0
    rule_snapshots_written: int = 0
    raw_snapshot_hash: str = ""
    per_symbol: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"interval        : {self.interval or '-'}",
            f"symbols         : {', '.join(self.symbols) or '-'}",
            f"bars written    : {self.bars_written}",
            f"quotes written  : {self.quotes_written}",
            f"rule snapshots  : {self.rule_snapshots_written}",
            f"exchangeInfo sha: {self.raw_snapshot_hash[:16] or '-'}",
        ]
        for symbol, count in sorted(self.per_symbol.items()):
            lines.append(f"  {symbol:<12} {count} bars")
        for symbol, reason in sorted(self.skipped.items()):
            lines.append(f"  {symbol:<12} SKIPPED: {reason}")
        return "\n".join(lines)


class Downloader:
    """Pulls public data and records it, keeping raw payloads for audit."""

    def __init__(
        self,
        client: BinanceUSPublicClient,
        db: Database,
        *,
        exchange: str = EXCHANGE_BINANCE_US,
    ) -> None:
        self._client = client
        self._db = db
        self._exchange = exchange
        self._bars = BarRepository(db)
        self._quotes = QuoteRepository(db)
        self._rules = RuleSnapshotRepository(db)
        self._raw = RawSnapshotRepository(db)

    # -- exchange rules ----------------------------------------------------

    def refresh_exchange_info(
        self, symbols: Sequence[str] | None = None
    ) -> tuple[int, str]:
        """Fetch exchangeInfo, store the raw payload and the parsed rules.

        The raw payload is written first and never overwritten, so the audit
        trail survives even if parsing later turns out to be wrong.
        """
        result = self._client.exchange_info(symbols)
        digest = self._raw.record(
            result.raw_payload,
            fetched_at=result.received_at,
            server_time=result.server_time,
            exchange=self._exchange,
        )
        written = self._rules.upsert_many(result.snapshots)
        return written, digest

    # -- candlesticks ------------------------------------------------------

    def backfill_klines(
        self,
        symbols: Sequence[str],
        interval: str,
        *,
        start_time: int,
        end_time: int | None = None,
        resume: bool = True,
    ) -> DownloadReport:
        """Download candlesticks for every symbol in the range.

        Only *closed* bars are stored. The currently-forming bar is excluded,
        because a partially-formed candle that later changes would make a
        backtest irreproducible and would leak information that did not exist
        at decision time.
        """
        if not symbols:
            raise ConfigError("no symbols requested")
        step = interval_to_ms(interval)
        wall_now = now_ms()
        # Last fully closed bar: anything whose close_time is in the future is
        # still forming.
        horizon = end_time if end_time is not None else wall_now
        horizon = min(horizon, wall_now)

        report = DownloadReport(symbols=tuple(symbols), interval=interval)
        rule_written, digest = self.refresh_exchange_info(symbols)
        report.rule_snapshots_written = rule_written
        report.raw_snapshot_hash = digest

        known = {s.symbol: s for s in self._rules.latest(exchange=self._exchange)}
        for symbol in symbols:
            snapshot = known.get(symbol)
            if snapshot is None:
                report.skipped[symbol] = "not listed on Binance.US"
                continue

            cursor = start_time
            if resume:
                count, _, last_open = self._bars.coverage(
                    symbol, interval, exchange=self._exchange
                )
                if count:
                    cursor = max(cursor, last_open + step)

            written = 0
            batch = []
            for bar in self._client.iter_klines(
                symbol, interval, start_time=cursor, end_time=horizon
            ):
                if bar.close_time > wall_now:
                    continue  # still forming
                batch.append(bar)
                if len(batch) >= 1000:
                    written += self._bars.upsert_many(batch)
                    batch.clear()
            if batch:
                written += self._bars.upsert_many(batch)

            report.per_symbol[symbol] = written
            report.bars_written += written

        return report

    # -- quotes ------------------------------------------------------------

    def snapshot_book_tickers(self, symbols: Sequence[str]) -> int:
        """Record one top-of-book observation per symbol, stamped on arrival.

        Binance.US bookTicker carries no exchange event time, so the stored
        ``source_received_at`` is the only timestamp available. The execution
        simulator treats quotes older than a configured age as stale, which is
        why the arrival stamp matters.
        """
        quotes = self._client.book_tickers(symbols)
        return self._quotes.insert_many(quotes)

    # -- reporting ---------------------------------------------------------

    def coverage_report(self, symbols: Sequence[str], interval: str) -> str:
        lines = []
        for symbol in symbols:
            count, lo, hi = self._bars.coverage(symbol, interval, exchange=self._exchange)
            if count == 0:
                lines.append(f"{symbol:<12} {interval:<4} no data")
            else:
                lines.append(
                    f"{symbol:<12} {interval:<4} {count:>7} bars  "
                    f"{to_iso(lo)} .. {to_iso(hi)}"
                )
        return "\n".join(lines)
