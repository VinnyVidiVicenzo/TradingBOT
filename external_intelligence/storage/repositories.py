"""Repositories over the external-intelligence store.

Everything here is append-only. Events and payloads are inserted or ignored, an
attribution is added only when the provider's claim changed, and nothing is ever
updated or removed -- so "what did we know, and when" stays answerable.

The one query that matters for honesty is :meth:`EventRepository.load_available`:
it returns an event only if its receipt time is at or before the cut-off. An
event with no receipt time is never returned.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Iterator, Mapping, Sequence

from crypto_research.decimals import dec, format_decimal

from ..attribution import EntityAttribution, EntityType
from ..base.coverage import CoverageIndex, CoverageInterval
from ..events import EventClass, EventType, ExternalEvent, ReceiptBasis, Side
from .database import IntelligenceDatabase

MAX_RECORD_JSON = 20_000

_EVENT_COLUMNS: tuple[str, ...] = (
    "event_id", "provider", "provider_event_id", "event_type", "event_class", "asset",
    "quote_asset", "blockchain", "exchange", "event_time", "provider_publish_time",
    "system_received_time", "estimated_received_time", "available_time",
    "received_time_basis", "assumed_delay_ms", "received_time_assumption",
    "amount_asset", "amount_usd", "value", "value_unit", "source_address",
    "destination_address", "source_entity", "destination_entity", "source_entity_type",
    "destination_entity_type", "transaction_hash", "market_symbol", "side", "metadata",
    "raw_payload_reference", "schema_version", "inserted_at",
)
_INSERT_EVENT_SQL = (
    f"INSERT INTO external_events ({', '.join(_EVENT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _EVENT_COLUMNS)}) ON CONFLICT DO NOTHING"
)


def _opt_dec(value: Decimal | None) -> str | None:
    return None if value is None else format_decimal(value)


def _read_dec(value: Any) -> Decimal | None:
    return None if value is None else dec(value)


def _enum_value(value: Any) -> str | None:
    return None if value is None else value.value


class EventRepository:
    """Normalized events."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def insert(self, event: ExternalEvent, *, inserted_at: int) -> bool:
        """Store an event. ``False`` means it was already there (a duplicate)."""
        row = (
            event.event_id, event.provider, event.provider_event_id, event.event_type.value,
            event.event_class.value, event.asset, event.quote_asset, event.blockchain,
            event.exchange, event.event_time, event.provider_publish_time,
            event.system_received_time, event.estimated_received_time, event.available_time,
            event.received_time_basis.value, event.assumed_delay_ms,
            event.received_time_assumption, _opt_dec(event.amount_asset),
            _opt_dec(event.amount_usd), _opt_dec(event.value), event.value_unit,
            event.source_address, event.destination_address, event.source_entity,
            event.destination_entity, _enum_value(event.source_entity_type),
            _enum_value(event.destination_entity_type), event.transaction_hash,
            event.market_symbol, _enum_value(event.side),
            json.dumps(dict(event.metadata), sort_keys=True, separators=(",", ":")),
            event.raw_payload_reference, event.schema_version, inserted_at,
        )
        with self._db.transaction() as conn:
            cursor = conn.execute(_INSERT_EVENT_SQL, row)
        return cursor.rowcount == 1

    def load_available(
        self,
        *,
        as_of: int,
        assets: Sequence[str] | None = None,
        event_types: Sequence[EventType] | None = None,
        event_classes: Sequence[EventClass] | None = None,
        providers: Sequence[str] | None = None,
        exchange: str | None = None,
        min_event_time: int | None = None,
        max_event_time: int | None = None,
        allow_estimated: bool = False,
    ) -> list[ExternalEvent]:
        """Events a decision made at ``as_of`` was allowed to know, oldest first.

        ``as_of`` is the availability cut-off: observed events need
        ``system_received_time <= as_of``; estimated ones qualify only when
        ``allow_estimated`` is set and ``estimated_received_time <= as_of``.
        Events with no receipt time never qualify.
        """
        params: list[Any] = [as_of]
        availability = "(received_time_basis = 'OBSERVED' AND system_received_time <= ?)"
        if allow_estimated:
            availability = (
                f"({availability} OR (received_time_basis = 'ASSUMED_DELAY' "
                "AND estimated_received_time <= ?))"
            )
            params.append(as_of)
        clauses = [availability, "event_time <= ?"]
        params.append(as_of if max_event_time is None else max_event_time)
        if min_event_time is not None:
            clauses.append("event_time >= ?")
            params.append(min_event_time)
        _in(clauses, params, "asset", assets)
        _in(clauses, params, "event_type", [t.value for t in event_types] if event_types else None)
        _in(clauses, params, "event_class", [c.value for c in event_classes] if event_classes else None)
        _in(clauses, params, "provider", providers)
        if exchange is not None:
            clauses.append("exchange = ?")
            params.append(exchange)
        sql = (
            "SELECT * FROM external_events WHERE " + " AND ".join(clauses)
            + " ORDER BY event_time ASC, event_id ASC"
        )
        return [_row_to_event(row) for row in self._db.connection.execute(sql, params).fetchall()]

    def iter_all(self, *, providers: Sequence[str] | None = None) -> Iterator[ExternalEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        _in(clauses, params, "provider", providers)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = self._db.connection.execute(
            f"SELECT * FROM external_events{where} ORDER BY event_time ASC, event_id ASC", params
        )
        for row in cursor:
            yield _row_to_event(row)

    def get(self, event_id: str) -> ExternalEvent | None:
        row = self._db.connection.execute(
            "SELECT * FROM external_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return None if row is None else _row_to_event(row)

    def count(self, *, provider: str | None = None) -> int:
        if provider is None:
            row = self._db.connection.execute("SELECT COUNT(*) AS n FROM external_events").fetchone()
        else:
            row = self._db.connection.execute(
                "SELECT COUNT(*) AS n FROM external_events WHERE provider = ?", (provider,)
            ).fetchone()
        return int(row["n"])


class RawPayloadRepository:
    """Every payload exactly as received, so any event can be re-derived."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def record(
        self, *, provider: str, source_kind: str, source_ref: str, body: str, received_at: int
    ) -> str:
        payload_id = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO raw_payloads (payload_id, provider, source_kind, source_ref, "
                "received_at, byte_length, body) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(payload_id) DO NOTHING",
                (payload_id, provider, source_kind, source_ref, received_at,
                 len(body.encode("utf-8")), body),
            )
        return payload_id

    def load(self, payload_id: str) -> str | None:
        row = self._db.connection.execute(
            "SELECT body FROM raw_payloads WHERE payload_id = ?", (payload_id,)
        ).fetchone()
        return None if row is None else str(row["body"])

    def count(self) -> int:
        return int(self._db.connection.execute("SELECT COUNT(*) AS n FROM raw_payloads").fetchone()["n"])


class QuarantineRepository:
    """Records we refused to turn into events, with the reason."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def add(
        self,
        *,
        provider: str,
        reason: str,
        detail: str | None,
        raw_payload_reference: str | None,
        recorded_at: int,
        record: Any = None,
    ) -> None:
        text = None
        if record is not None:
            try:
                text = json.dumps(record, default=str, ensure_ascii=True)[:MAX_RECORD_JSON]
            except (TypeError, ValueError, RecursionError, OverflowError):
                text = json.dumps({"unserializable_record": type(record).__name__})
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO quarantine (provider, reason, detail, raw_payload_reference, "
                "recorded_at, record_json) VALUES (?,?,?,?,?,?)",
                (provider, reason, detail, raw_payload_reference, recorded_at, text),
            )

    def count(self, *, provider: str | None = None, reason: str | None = None) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if reason is not None:
            clauses.append("reason = ?")
            params.append(reason)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return int(
            self._db.connection.execute(f"SELECT COUNT(*) AS n FROM quarantine{where}", params).fetchone()["n"]
        )

    def reasons(self, *, provider: str | None = None) -> dict[str, int]:
        params: list[Any] = []
        where = ""
        if provider is not None:
            where = " WHERE provider = ?"
            params.append(provider)
        cursor = self._db.connection.execute(
            f"SELECT reason, COUNT(*) AS n FROM quarantine{where} GROUP BY reason ORDER BY reason", params
        )
        return {row["reason"]: int(row["n"]) for row in cursor.fetchall()}


class AttributionRepository:
    """A history of provider claims about addresses. A change adds a row."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def record(self, attribution: EntityAttribution, *, recorded_at: int) -> bool:
        """Store a claim unless it repeats the provider's latest one. ``True`` if stored."""
        history = self.history(attribution.provider, attribution.address, attribution.blockchain)
        if history and history[-1].signature() == attribution.signature():
            return False
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO entity_attributions (provider, blockchain, address, raw_label, "
                "raw_entity_type, entity_type, attributed_at, time_basis, provider_confidence, "
                "raw_payload_reference, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attribution.provider, attribution.blockchain, attribution.address,
                    attribution.raw_label, attribution.raw_entity_type,
                    attribution.entity_type.value, attribution.attributed_at,
                    attribution.time_basis, attribution.provider_confidence,
                    attribution.raw_payload_reference, recorded_at,
                ),
            )
        return True

    def history(
        self, provider: str, address: str, blockchain: str | None = None
    ) -> list[EntityAttribution]:
        cursor = self._db.connection.execute(
            "SELECT * FROM entity_attributions WHERE provider = ? AND address = ? "
            "AND blockchain IS ? ORDER BY attributed_at ASC, attribution_id ASC",
            (provider, address, blockchain),
        )
        return [_row_to_attribution(row) for row in cursor.fetchall()]

    def changed_later(self, provider: str, address: str, blockchain: str | None = None) -> bool:
        """Did this provider ever revise what it said about this address?"""
        return len(self.history(provider, address, blockchain)) > 1


class CoverageRepository:
    """Declared periods during which a provider feed was complete."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def record(self, interval: CoverageInterval, *, recorded_at: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO coverage (provider, family, covered_from, covered_to, basis, "
                "source_ref, receipts, known_from, assets, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (interval.provider, interval.family, interval.covered_from,
                 interval.covered_to, interval.basis, interval.source_ref,
                 interval.receipts, interval.known_from,
                 None if interval.assets is None else json.dumps(list(interval.assets)), recorded_at),
            )

    def index(self, *, provider: str | None = None) -> CoverageIndex:
        params: list[Any] = []
        where = ""
        if provider is not None:
            where = " WHERE provider = ?"
            params.append(provider)
        cursor = self._db.connection.execute(f"SELECT * FROM coverage{where}", params)
        return CoverageIndex(
            CoverageInterval(
                provider=row["provider"], family=row["family"],
                covered_from=int(row["covered_from"]), covered_to=int(row["covered_to"]),
                basis=row["basis"], source_ref=row["source_ref"], receipts=row["receipts"],
                known_from=None if row["known_from"] is None else int(row["known_from"]),
                assets=None if row["assets"] is None else tuple(json.loads(row["assets"])),
            )
            for row in cursor.fetchall()
        )


class HealthRepository:
    """Time-stamped provider health snapshots."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def record(self, snapshot: Mapping[str, Any], *, recorded_at: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_health_snapshots (provider, recorded_at, snapshot_json) "
                "VALUES (?,?,?)",
                (snapshot["provider"], recorded_at, json.dumps(dict(snapshot), sort_keys=True)),
            )

    def latest(self, provider: str) -> dict[str, Any] | None:
        row = self._db.connection.execute(
            "SELECT snapshot_json FROM provider_health_snapshots WHERE provider = ? "
            "ORDER BY snapshot_id DESC LIMIT 1", (provider,),
        ).fetchone()
        return None if row is None else json.loads(row["snapshot_json"])

    def history(self, provider: str) -> list[dict[str, Any]]:
        cursor = self._db.connection.execute(
            "SELECT snapshot_json FROM provider_health_snapshots WHERE provider = ? "
            "ORDER BY snapshot_id ASC", (provider,),
        )
        return [json.loads(row["snapshot_json"]) for row in cursor.fetchall()]


class CursorRepository:
    """A poller's resume point. Append-only: the current value is whichever row is newest."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def record(self, provider: str, key: str, value: str, *, recorded_at: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_cursors (provider, key, value, recorded_at) VALUES (?,?,?,?)",
                (provider, key, value, recorded_at),
            )

    def latest(self, provider: str, key: str) -> str | None:
        row = self._db.connection.execute(
            "SELECT value FROM provider_cursors WHERE provider = ? AND key = ? "
            "ORDER BY cursor_id DESC LIMIT 1",
            (provider, key),
        ).fetchone()
        return None if row is None else row["value"]

    def history(self, provider: str, key: str) -> list[tuple[str, int]]:
        cursor = self._db.connection.execute(
            "SELECT value, recorded_at FROM provider_cursors WHERE provider = ? AND key = ? "
            "ORDER BY cursor_id ASC",
            (provider, key),
        )
        return [(row["value"], int(row["recorded_at"])) for row in cursor.fetchall()]


class CanonicalRepository:
    """Additive claims that several provider records describe one real event."""

    def __init__(self, db: IntelligenceDatabase) -> None:
        self._db = db

    def record_group(
        self, canonical_id: str, event_ids: Sequence[str], *, rule: str,
        confidence_basis: str, recorded_at: int,
    ) -> None:
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO canonical_events (canonical_id, event_id, rule, confidence_basis, "
                "recorded_at) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                [(canonical_id, event_id, rule, confidence_basis, recorded_at) for event_id in event_ids],
            )

    def record_ambiguity(
        self, event_id: str, candidate_event_ids: Sequence[str], *, rule: str, recorded_at: int
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO dedup_ambiguities (event_id, candidate_event_ids, rule, recorded_at) "
                "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (event_id, json.dumps(sorted(candidate_event_ids)), rule, recorded_at),
            )

    def groups(self) -> dict[str, list[str]]:
        cursor = self._db.connection.execute(
            "SELECT canonical_id, event_id FROM canonical_events ORDER BY canonical_id, event_id"
        )
        out: dict[str, list[str]] = {}
        for row in cursor.fetchall():
            out.setdefault(row["canonical_id"], []).append(row["event_id"])
        return out

    def ambiguities(self) -> list[dict[str, Any]]:
        cursor = self._db.connection.execute(
            "SELECT event_id, candidate_event_ids, rule FROM dedup_ambiguities ORDER BY ambiguity_id"
        )
        return [
            {"event_id": r["event_id"], "candidates": json.loads(r["candidate_event_ids"]), "rule": r["rule"]}
            for r in cursor.fetchall()
        ]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _in(clauses: list[str], params: list[Any], column: str, values: Sequence[Any] | None) -> None:
    if values:
        clauses.append(f"{column} IN ({', '.join('?' for _ in values)})")
        params.extend(values)


def _row_to_event(row: Mapping[str, Any]) -> ExternalEvent:
    def entity(value: str | None) -> EntityType | None:
        return None if value is None else EntityType(value)

    return ExternalEvent(
        event_id=row["event_id"],
        provider=row["provider"],
        provider_event_id=row["provider_event_id"],
        event_type=EventType(row["event_type"]),
        asset=row["asset"],
        event_time=int(row["event_time"]),
        system_received_time=row["system_received_time"],
        schema_version=row["schema_version"],
        quote_asset=row["quote_asset"],
        blockchain=row["blockchain"],
        exchange=row["exchange"],
        provider_publish_time=row["provider_publish_time"],
        amount_asset=_read_dec(row["amount_asset"]),
        amount_usd=_read_dec(row["amount_usd"]),
        value=_read_dec(row["value"]),
        value_unit=row["value_unit"],
        source_address=row["source_address"],
        destination_address=row["destination_address"],
        source_entity=row["source_entity"],
        destination_entity=row["destination_entity"],
        source_entity_type=entity(row["source_entity_type"]),
        destination_entity_type=entity(row["destination_entity_type"]),
        transaction_hash=row["transaction_hash"],
        market_symbol=row["market_symbol"],
        side=None if row["side"] is None else Side(row["side"]),
        metadata=json.loads(row["metadata"]),
        raw_payload_reference=row["raw_payload_reference"],
        estimated_received_time=row["estimated_received_time"],
        received_time_basis=ReceiptBasis(row["received_time_basis"]),
        assumed_delay_ms=row["assumed_delay_ms"],
        received_time_assumption=row["received_time_assumption"],
    )


def _row_to_attribution(row: Mapping[str, Any]) -> EntityAttribution:
    return EntityAttribution(
        provider=row["provider"],
        address=row["address"],
        raw_label=row["raw_label"],
        raw_entity_type=row["raw_entity_type"],
        entity_type=EntityType(row["entity_type"]),
        attributed_at=int(row["attributed_at"]),
        time_basis=row["time_basis"],
        blockchain=row["blockchain"],
        provider_confidence=row["provider_confidence"],
        raw_payload_reference=row["raw_payload_reference"],
    )
