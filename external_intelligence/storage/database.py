"""The external-intelligence store: its own SQLite file, never the market store.

Binance.US bars and third-party events are different kinds of evidence with
different trust, and the engine's design keeps external reference data out of
the Binance.US store. This file follows suit: a separate database, refused if
pointed at the market store, and append-only by construction (rows are inserted
or ignored, never updated or removed).

It reuses the engine's connection handling (WAL, row factory, transaction
context manager) by subclassing ``Database`` and replacing only the schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from crypto_research.errors import ConfigError
from crypto_research.storage.database import Database

SCHEMA_VERSION = 1
DEFAULT_PATH = "data/external_intelligence.sqlite"
MARKET_STORE_NAME = "market.sqlite"

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # Every payload exactly as received, keyed by its own hash.
    """
    CREATE TABLE IF NOT EXISTS raw_payloads (
        payload_id  TEXT PRIMARY KEY,
        provider    TEXT    NOT NULL,
        source_kind TEXT    NOT NULL,
        source_ref  TEXT    NOT NULL,
        received_at INTEGER NOT NULL,
        byte_length INTEGER NOT NULL,
        body        TEXT    NOT NULL
    )
    """,
    # Decimals are TEXT, as in the market store: REAL would round them.
    """
    CREATE TABLE IF NOT EXISTS external_events (
        event_id                 TEXT PRIMARY KEY,
        provider                 TEXT    NOT NULL,
        provider_event_id        TEXT    NOT NULL,
        event_type               TEXT    NOT NULL,
        event_class              TEXT    NOT NULL,
        asset                    TEXT    NOT NULL,
        quote_asset              TEXT,
        blockchain               TEXT,
        exchange                 TEXT,
        event_time               INTEGER NOT NULL,
        provider_publish_time    INTEGER,
        system_received_time     INTEGER,
        estimated_received_time  INTEGER,
        available_time           INTEGER,
        received_time_basis      TEXT    NOT NULL,
        assumed_delay_ms         INTEGER,
        received_time_assumption TEXT,
        amount_asset             TEXT,
        amount_usd               TEXT,
        value                    TEXT,
        value_unit               TEXT,
        source_address           TEXT,
        destination_address      TEXT,
        source_entity            TEXT,
        destination_entity       TEXT,
        source_entity_type       TEXT,
        destination_entity_type  TEXT,
        transaction_hash         TEXT,
        market_symbol            TEXT,
        side                     TEXT,
        metadata                 TEXT    NOT NULL,
        raw_payload_reference    TEXT,
        schema_version           TEXT    NOT NULL,
        inserted_at              INTEGER NOT NULL,
        UNIQUE (provider, provider_event_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_asset_time ON external_events (asset, event_time)",
    "CREATE INDEX IF NOT EXISTS idx_events_type_time ON external_events (event_type, event_time)",
    "CREATE INDEX IF NOT EXISTS idx_events_available ON external_events (available_time)",
    "CREATE INDEX IF NOT EXISTS idx_events_hash ON external_events (transaction_hash)",
    # A history of claims: rows are only ever added.
    """
    CREATE TABLE IF NOT EXISTS entity_attributions (
        attribution_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        provider            TEXT    NOT NULL,
        blockchain          TEXT,
        address             TEXT    NOT NULL,
        raw_label           TEXT,
        raw_entity_type     TEXT,
        entity_type         TEXT    NOT NULL,
        attributed_at       INTEGER NOT NULL,
        time_basis          TEXT    NOT NULL,
        provider_confidence TEXT,
        raw_payload_reference TEXT,
        recorded_at         INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_attr_key ON entity_attributions (provider, blockchain, address, attributed_at)",
    # Records we refused, with the reason. Nothing is silently dropped.
    """
    CREATE TABLE IF NOT EXISTS quarantine (
        quarantine_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        provider              TEXT    NOT NULL,
        reason                TEXT    NOT NULL,
        detail                TEXT,
        raw_payload_reference TEXT,
        recorded_at           INTEGER NOT NULL,
        record_json           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coverage (
        coverage_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        provider     TEXT    NOT NULL,
        family       TEXT    NOT NULL,
        covered_from INTEGER NOT NULL,
        covered_to   INTEGER NOT NULL,
        basis        TEXT    NOT NULL,
        source_ref   TEXT,
        receipts     TEXT    NOT NULL DEFAULT 'observed',
        known_from   INTEGER,
        assets       TEXT,
        recorded_at  INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_health_snapshots (
        snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        provider      TEXT    NOT NULL,
        recorded_at   INTEGER NOT NULL,
        snapshot_json TEXT    NOT NULL
    )
    """,
    # Original provider records are never merged away. A canonical identity is a
    # separate, additive claim that a group of them describe the same thing.
    """
    CREATE TABLE IF NOT EXISTS canonical_events (
        canonical_id     TEXT    NOT NULL,
        event_id         TEXT    NOT NULL,
        rule             TEXT    NOT NULL,
        confidence_basis TEXT    NOT NULL,
        recorded_at      INTEGER NOT NULL,
        PRIMARY KEY (canonical_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dedup_ambiguities (
        ambiguity_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id            TEXT    NOT NULL,
        candidate_event_ids TEXT    NOT NULL,
        rule                TEXT    NOT NULL,
        recorded_at         INTEGER NOT NULL,
        UNIQUE (event_id, candidate_event_ids, rule)
    )
    """,
    # A poller's resume point (for example, the last Binance.US trade id it has
    # fully scanned for one symbol). Append-only like everything else here: a new
    # value is a new row, and "the cursor" is whichever row was recorded last.
    """
    CREATE TABLE IF NOT EXISTS provider_cursors (
        cursor_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        provider    TEXT    NOT NULL,
        key         TEXT    NOT NULL,
        value       TEXT    NOT NULL,
        recorded_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cursors_lookup ON provider_cursors (provider, key, cursor_id)",
)


class IntelligenceDatabase(Database):
    """``Database`` with the external-intelligence schema instead of the market one."""

    def _migrate(self) -> None:
        with self.connection:
            for statement in SCHEMA_STATEMENTS:
                self.connection.execute(statement)
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES ('zone', 'external-intelligence') "
                "ON CONFLICT(key) DO NOTHING"
            )


def open_intelligence_database(path: str | Path = DEFAULT_PATH) -> IntelligenceDatabase:
    """Open (creating if needed) the external-intelligence store.

    ``:memory:`` is accepted for tests. The market store is refused by name, so
    third-party events cannot end up beside Binance.US bars by a slip of the
    path argument.
    """
    text = str(path)
    if text != ":memory:":
        if Path(text).name.lower() == MARKET_STORE_NAME:
            raise ConfigError(
                "the external-intelligence store must not be the Binance.US market store"
            )
        Path(text).parent.mkdir(parents=True, exist_ok=True)
    return IntelligenceDatabase(sqlite3.connect(text), text)
