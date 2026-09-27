"""Read/write access to the local store.

Bars and rule snapshots are upserted rather than duplicated, so re-running a
download is idempotent. Raw exchangeInfo payloads are the exception: every
fetch is kept, because the point of keeping them is to be able to see when a
rule changed.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..decimals import dec, format_decimal
from ..models import (
    EXCHANGE_BINANCE_US,
    ExchangeRuleSnapshot,
    MarketBar,
    MarketQuote,
)
from .database import Database


class BarRepository:
    """Candlestick storage."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_many(self, bars: Iterable[MarketBar]) -> int:
        rows = [
            (
                bar.exchange,
                bar.symbol,
                bar.interval,
                bar.open_time,
                bar.close_time,
                format_decimal(bar.open),
                format_decimal(bar.high),
                format_decimal(bar.low),
                format_decimal(bar.close),
                format_decimal(bar.volume),
                None if bar.quote_volume is None else format_decimal(bar.quote_volume),
                bar.trade_count,
                bar.source_received_at,
            )
            for bar in bars
        ]
        if not rows:
            return 0
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO market_bars (
                    exchange, symbol, interval, open_time, close_time,
                    open, high, low, close, volume, quote_volume,
                    trade_count, source_received_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(exchange, symbol, interval, open_time) DO UPDATE SET
                    close_time=excluded.close_time,
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    quote_volume=excluded.quote_volume,
                    trade_count=excluded.trade_count,
                    source_received_at=excluded.source_received_at
                """,
                rows,
            )
        return len(rows)

    def load(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        exchange: str = EXCHANGE_BINANCE_US,
    ) -> list[MarketBar]:
        """Bars with ``open_time`` inside the range, ordered oldest first."""
        clauses = ["exchange = ?", "symbol = ?", "interval = ?"]
        params: list[Any] = [exchange, symbol, interval]
        if start_time is not None:
            clauses.append("open_time >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("open_time <= ?")
            params.append(end_time)
        sql = (
            "SELECT * FROM market_bars WHERE "
            + " AND ".join(clauses)
            + " ORDER BY open_time ASC"
        )
        cursor = self._db.connection.execute(sql, params)
        return [_row_to_bar(row) for row in cursor.fetchall()]

    def coverage(
        self, symbol: str, interval: str, *, exchange: str = EXCHANGE_BINANCE_US
    ) -> tuple[int, int, int]:
        """(row_count, first_open_time, last_open_time). Zeros when empty."""
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n, MIN(open_time) AS lo, MAX(open_time) AS hi "
            "FROM market_bars WHERE exchange=? AND symbol=? AND interval=?",
            (exchange, symbol, interval),
        ).fetchone()
        if row is None or row["n"] == 0:
            return (0, 0, 0)
        return (int(row["n"]), int(row["lo"]), int(row["hi"]))

    def symbols(self, *, exchange: str = EXCHANGE_BINANCE_US) -> list[tuple[str, str]]:
        cursor = self._db.connection.execute(
            "SELECT DISTINCT symbol, interval FROM market_bars WHERE exchange=? "
            "ORDER BY symbol, interval",
            (exchange,),
        )
        return [(row["symbol"], row["interval"]) for row in cursor.fetchall()]


class QuoteRepository:
    """Top-of-book snapshot storage."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_many(self, quotes: Iterable[MarketQuote]) -> int:
        rows = [
            (
                quote.exchange,
                quote.symbol,
                quote.source_received_at,
                quote.exchange_event_time,
                format_decimal(quote.bid),
                None if quote.bid_size is None else format_decimal(quote.bid_size),
                format_decimal(quote.ask),
                None if quote.ask_size is None else format_decimal(quote.ask_size),
            )
            for quote in quotes
        ]
        if not rows:
            return 0
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO market_quotes (
                    exchange, symbol, source_received_at, exchange_event_time,
                    bid, bid_size, ask, ask_size
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(exchange, symbol, source_received_at) DO NOTHING
                """,
                rows,
            )
        return len(rows)

    def load(
        self,
        symbol: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        exchange: str = EXCHANGE_BINANCE_US,
    ) -> list[MarketQuote]:
        clauses = ["exchange = ?", "symbol = ?"]
        params: list[Any] = [exchange, symbol]
        if start_time is not None:
            clauses.append("source_received_at >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("source_received_at <= ?")
            params.append(end_time)
        cursor = self._db.connection.execute(
            "SELECT * FROM market_quotes WHERE "
            + " AND ".join(clauses)
            + " ORDER BY source_received_at ASC",
            params,
        )
        return [_row_to_quote(row) for row in cursor.fetchall()]


class RuleSnapshotRepository:
    """Parsed per-symbol trading rules, one row per observation."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_many(self, snapshots: Iterable[ExchangeRuleSnapshot]) -> int:
        rows = [
            (
                snap.exchange,
                snap.symbol,
                snap.source_received_at,
                snap.valid_from,
                snap.status,
                snap.base_asset,
                snap.quote_asset,
                _opt_dec(snap.price_tick),
                _opt_dec(snap.min_price),
                _opt_dec(snap.max_price),
                _opt_dec(snap.min_quantity),
                _opt_dec(snap.max_quantity),
                _opt_dec(snap.quantity_step),
                _opt_dec(snap.market_min_quantity),
                _opt_dec(snap.market_max_quantity),
                _opt_dec(snap.market_quantity_step),
                _opt_dec(snap.min_notional),
                _opt_dec(snap.max_notional),
                int(snap.min_notional_applies_to_market),
                int(snap.max_notional_applies_to_market),
                snap.notional_avg_price_mins,
                snap.base_asset_precision,
                snap.quote_asset_precision,
                int(snap.is_spot_trading_allowed),
                json.dumps(list(snap.order_types)),
                snap.max_num_orders,
                snap.max_num_algo_orders,
                json.dumps(snap.raw_payload, sort_keys=True, separators=(",", ":")),
            )
            for snap in snapshots
        ]
        if not rows:
            return 0
        with self._db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO exchange_rule_snapshots (
                    exchange, symbol, source_received_at, valid_from, status,
                    base_asset, quote_asset, price_tick, min_price, max_price,
                    min_quantity, max_quantity, quantity_step,
                    market_min_quantity, market_max_quantity, market_quantity_step,
                    min_notional, max_notional,
                    min_notional_applies_to_market, max_notional_applies_to_market,
                    notional_avg_price_mins,
                    base_asset_precision, quote_asset_precision,
                    is_spot_trading_allowed, order_types, max_num_orders,
                    max_num_algo_orders, raw_payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(exchange, symbol, source_received_at) DO NOTHING
                """,
                rows,
            )
        return len(rows)

    def latest(
        self, *, at_or_before: int | None = None, exchange: str = EXCHANGE_BINANCE_US
    ) -> list[ExchangeRuleSnapshot]:
        """Most recent snapshot per symbol, optionally as of a point in time."""
        params: list[Any] = [exchange]
        time_clause = ""
        if at_or_before is not None:
            time_clause = " AND source_received_at <= ?"
            params.append(at_or_before)
        sql = (
            "SELECT s.* FROM exchange_rule_snapshots s "
            "JOIN (SELECT symbol, MAX(source_received_at) AS ts "
            "      FROM exchange_rule_snapshots WHERE exchange = ?" + time_clause +
            "      GROUP BY symbol) latest "
            "  ON s.symbol = latest.symbol AND s.source_received_at = latest.ts "
            "WHERE s.exchange = ? ORDER BY s.symbol"
        )
        cursor = self._db.connection.execute(sql, params + [exchange])
        return [_row_to_rule_snapshot(row) for row in cursor.fetchall()]

    def history(
        self, symbol: str, *, exchange: str = EXCHANGE_BINANCE_US
    ) -> list[ExchangeRuleSnapshot]:
        cursor = self._db.connection.execute(
            "SELECT * FROM exchange_rule_snapshots WHERE exchange=? AND symbol=? "
            "ORDER BY source_received_at ASC",
            (exchange, symbol),
        )
        return [_row_to_rule_snapshot(row) for row in cursor.fetchall()]


class RawSnapshotRepository:
    """Untouched exchangeInfo payloads, kept forever for auditing.

    Nothing overwrites a raw snapshot. If the exchange changes a minimum
    notional next month, the record of what it was this month has to still be
    here, or every experiment run before the change becomes unexplainable.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        payload: Mapping[str, Any],
        *,
        fetched_at: int,
        server_time: int,
        exchange: str = EXCHANGE_BINANCE_US,
    ) -> str:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        symbols = payload.get("symbols")
        count = len(symbols) if isinstance(symbols, list) else 0
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO raw_exchange_info_snapshots "
                "(exchange, fetched_at, server_time, payload_sha256, symbol_count, payload) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(exchange, fetched_at) DO NOTHING",
                (exchange, fetched_at, server_time, digest, count, blob),
            )
        return digest

    def list_snapshots(
        self, *, exchange: str = EXCHANGE_BINANCE_US
    ) -> list[dict[str, Any]]:
        cursor = self._db.connection.execute(
            "SELECT exchange, fetched_at, server_time, payload_sha256, symbol_count "
            "FROM raw_exchange_info_snapshots WHERE exchange=? ORDER BY fetched_at ASC",
            (exchange,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def load(
        self, fetched_at: int, *, exchange: str = EXCHANGE_BINANCE_US
    ) -> Mapping[str, Any] | None:
        row = self._db.connection.execute(
            "SELECT payload FROM raw_exchange_info_snapshots "
            "WHERE exchange=? AND fetched_at=?",
            (exchange, fetched_at),
        ).fetchone()
        return None if row is None else json.loads(row["payload"])

    def distinct_hashes(
        self, *, exchange: str = EXCHANGE_BINANCE_US
    ) -> list[tuple[str, int]]:
        """(payload_hash, first_seen_at) pairs -- one per distinct rule set."""
        cursor = self._db.connection.execute(
            "SELECT payload_sha256, MIN(fetched_at) AS first_seen "
            "FROM raw_exchange_info_snapshots WHERE exchange=? "
            "GROUP BY payload_sha256 ORDER BY first_seen ASC",
            (exchange,),
        )
        return [(row["payload_sha256"], int(row["first_seen"])) for row in cursor.fetchall()]


# --------------------------------------------------------------------------
# row mappers
# --------------------------------------------------------------------------


def _opt_dec(value: Decimal | None) -> str | None:
    return None if value is None else format_decimal(value)


def _read_dec(value: Any) -> Decimal | None:
    return None if value is None else dec(value)


def _row_to_bar(row: Mapping[str, Any]) -> MarketBar:
    return MarketBar(
        exchange=row["exchange"],
        symbol=row["symbol"],
        interval=row["interval"],
        open_time=int(row["open_time"]),
        close_time=int(row["close_time"]),
        open=dec(row["open"]),
        high=dec(row["high"]),
        low=dec(row["low"]),
        close=dec(row["close"]),
        volume=dec(row["volume"]),
        quote_volume=_read_dec(row["quote_volume"]),
        trade_count=row["trade_count"],
        source_received_at=int(row["source_received_at"]),
    )


def _row_to_quote(row: Mapping[str, Any]) -> MarketQuote:
    return MarketQuote(
        exchange=row["exchange"],
        symbol=row["symbol"],
        bid=dec(row["bid"]),
        ask=dec(row["ask"]),
        bid_size=_read_dec(row["bid_size"]),
        ask_size=_read_dec(row["ask_size"]),
        exchange_event_time=row["exchange_event_time"],
        source_received_at=int(row["source_received_at"]),
    )


def _row_to_rule_snapshot(row: Mapping[str, Any]) -> ExchangeRuleSnapshot:
    return ExchangeRuleSnapshot(
        exchange=row["exchange"],
        symbol=row["symbol"],
        status=row["status"],
        base_asset=row["base_asset"],
        quote_asset=row["quote_asset"],
        price_tick=_read_dec(row["price_tick"]),
        min_price=_read_dec(row["min_price"]),
        max_price=_read_dec(row["max_price"]),
        min_quantity=_read_dec(row["min_quantity"]),
        max_quantity=_read_dec(row["max_quantity"]),
        quantity_step=_read_dec(row["quantity_step"]),
        market_min_quantity=_read_dec(row["market_min_quantity"]),
        market_max_quantity=_read_dec(row["market_max_quantity"]),
        market_quantity_step=_read_dec(row["market_quantity_step"]),
        min_notional=_read_dec(row["min_notional"]),
        max_notional=_read_dec(row["max_notional"]),
        min_notional_applies_to_market=bool(row["min_notional_applies_to_market"]),
        max_notional_applies_to_market=bool(row["max_notional_applies_to_market"]),
        notional_avg_price_mins=row["notional_avg_price_mins"],
        base_asset_precision=row["base_asset_precision"],
        quote_asset_precision=row["quote_asset_precision"],
        is_spot_trading_allowed=bool(row["is_spot_trading_allowed"]),
        order_types=tuple(json.loads(row["order_types"])),
        max_num_orders=row["max_num_orders"],
        max_num_algo_orders=row["max_num_algo_orders"],
        raw_payload=json.loads(row["raw_payload"]),
        valid_from=int(row["valid_from"]),
        source_received_at=int(row["source_received_at"]),
    )


def iter_chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]
