"""SQLite connection handling and schema migration."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

# Decimals are stored as TEXT in canonical form. SQLite REAL would silently
# round 8-decimal crypto quantities, and the whole point of the Decimal
# discipline elsewhere is lost if the store undoes it.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_bars (
        exchange           TEXT    NOT NULL,
        symbol             TEXT    NOT NULL,
        interval           TEXT    NOT NULL,
        open_time          INTEGER NOT NULL,
        close_time         INTEGER NOT NULL,
        open               TEXT    NOT NULL,
        high               TEXT    NOT NULL,
        low                TEXT    NOT NULL,
        close              TEXT    NOT NULL,
        volume             TEXT    NOT NULL,
        quote_volume       TEXT,
        trade_count        INTEGER,
        source_received_at INTEGER NOT NULL,
        PRIMARY KEY (exchange, symbol, interval, open_time)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bars_scan ON market_bars (exchange, symbol, interval, close_time)",
    """
    CREATE TABLE IF NOT EXISTS market_quotes (
        exchange            TEXT    NOT NULL,
        symbol              TEXT    NOT NULL,
        source_received_at  INTEGER NOT NULL,
        exchange_event_time INTEGER,
        bid                 TEXT    NOT NULL,
        bid_size            TEXT,
        ask                 TEXT    NOT NULL,
        ask_size            TEXT,
        PRIMARY KEY (exchange, symbol, source_received_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS exchange_rule_snapshots (
        exchange                       TEXT    NOT NULL,
        symbol                         TEXT    NOT NULL,
        source_received_at             INTEGER NOT NULL,
        valid_from                     INTEGER NOT NULL,
        status                         TEXT    NOT NULL,
        base_asset                     TEXT    NOT NULL,
        quote_asset                    TEXT    NOT NULL,
        price_tick                     TEXT,
        min_price                      TEXT,
        max_price                      TEXT,
        min_quantity                   TEXT,
        max_quantity                   TEXT,
        quantity_step                  TEXT,
        market_min_quantity            TEXT,
        market_max_quantity            TEXT,
        market_quantity_step           TEXT,
        min_notional                   TEXT,
        max_notional                   TEXT,
        min_notional_applies_to_market INTEGER NOT NULL,
        max_notional_applies_to_market INTEGER NOT NULL,
        notional_avg_price_mins        INTEGER,
        base_asset_precision           INTEGER,
        quote_asset_precision          INTEGER,
        is_spot_trading_allowed        INTEGER NOT NULL,
        order_types                    TEXT    NOT NULL,
        max_num_orders                 INTEGER,
        max_num_algo_orders            INTEGER,
        raw_payload                    TEXT    NOT NULL,
        PRIMARY KEY (exchange, symbol, source_received_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_exchange_info_snapshots (
        exchange       TEXT    NOT NULL,
        fetched_at     INTEGER NOT NULL,
        server_time    INTEGER NOT NULL,
        payload_sha256 TEXT    NOT NULL,
        symbol_count   INTEGER NOT NULL,
        payload        TEXT    NOT NULL,
        PRIMARY KEY (exchange, fetched_at)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_info_hash ON raw_exchange_info_snapshots (payload_sha256)",
)


class Database:
    """Thin wrapper over a sqlite3 connection with the schema applied."""

    def __init__(self, connection: sqlite3.Connection, path: str) -> None:
        self.connection = connection
        self.path = path
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self.connection:
            for statement in SCHEMA_STATEMENTS:
                self.connection.execute(statement)
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection:
            yield self.connection

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_database(path: str | Path = "data/market.sqlite") -> Database:
    """Open (creating if needed) the local market-data store.

    ``:memory:`` is accepted for tests.
    """
    text = str(path)
    if text != ":memory:":
        Path(text).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(text)
    return Database(connection, text)
