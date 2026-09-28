"""Executed market activity: trades that happened, forced liquidations, derivatives readings.

This is a different evidentiary class from a wallet transfer. A transfer says
value moved between addresses; an executed trade says someone actually bought or
sold on a market. They are kept as different event types and never mixed.

The record format read here (``executed_activity_flat_v1``) is defined by this
project, not by any provider -- no provider we looked at documents a response
schema we could rely on. A user-provided export from a source they are entitled
to use can be converted to it. One record per line (JSONL), or a JSON array, or
CSV with these columns:

``kind``            ``whale_trade`` | ``large_trade`` | ``liquidation`` | ``funding`` |
                    ``open_interest`` (required)
``id``              the source's own id (optional; derived when absent)
``time_ms`` or ``time_s``   when it happened (required, one of the two)
``symbol``          base asset, e.g. ``BTC`` (required); ``quote`` e.g. ``USDT``
``exchange``        the venue *as the source names it* (kept as text, never mapped)
``market_symbol``   the venue's own symbol, e.g. ``BTCUSDT`` (optional)
``side``            ``buy`` | ``sell`` -- only when the source explicitly reports the
                    executed side. ``whale_trade`` requires it.
``notional_usd``, ``quantity``, ``price``       trade size
``liquidated_side`` ``long`` | ``short`` (required for ``liquidation``)
``funding_rate``    a ratio per funding interval (required for ``funding``)
``interval_hours``  the funding interval (optional)
``open_interest_usd``, ``open_interest``       (one required for ``open_interest``)
``provider_publish_time_ms``, ``system_received_time_ms``   optional timing

Nothing here labels an event bullish or bearish, and the venue named by the source
is never treated as Binance.US.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from crypto_research.decimals import dec

from ..events import EventType, ExternalEvent, Side, make_event_id
from ..sanitize import clean_text, sanitize_metadata
from .coverage import FAMILY_DERIVATIVES, FAMILY_EXECUTED, FAMILY_LIQUIDATIONS
from .provider import NormalizeContext, NormalizedRecord, Provider

FUNDING_UNIT = "funding_rate_per_interval"


class ExecutedActivityAdapter(Provider):
    """Reads ``executed_activity_flat_v1`` records under a configurable provider name."""

    name = "executed_activity"
    schema_version = "1"
    adapter_version = "1"
    families = (FAMILY_EXECUTED, FAMILY_LIQUIDATIONS, FAMILY_DERIVATIVES)
    envelope_keys = ("records", "data", "events", "trades")

    def normalize_record(self, record: Any, ctx: NormalizeContext) -> NormalizedRecord:
        if not isinstance(record, Mapping):
            return NormalizedRecord.rejected("record_not_an_object", type(record).__name__)
        kind = (clean_text(record.get("kind"), max_length=30) or "").lower()
        try:
            event_time = _event_time(record)
            asset = _required_text(record, "symbol").upper()
            publish = _optional_int(record.get("provider_publish_time_ms"))
            receipt = ctx.policy.apply(
                event_time=event_time, provider_publish_time=publish,
                record_receipt_ms=_optional_int(record.get("system_received_time_ms")),
                ingest_time_ms=ctx.ingest_time_ms,
            )
            event_type, side, fields = self._classify(kind, record)
        except ValueError as exc:
            reason = "unsupported_kind" if kind not in _KINDS else "invalid_record"
            return NormalizedRecord.rejected(reason, str(exc))

        provider_event_id = clean_text(record.get("id"), max_length=200) or _derived_id(self.name, record)
        event = ExternalEvent(
            event_id=make_event_id(self.name, provider_event_id, self.schema_version),
            provider=self.name,
            provider_event_id=provider_event_id,
            event_type=event_type,
            asset=asset,
            event_time=event_time,
            system_received_time=receipt.system_received_time,
            schema_version=self.schema_version,
            quote_asset=_upper(record.get("quote")),
            exchange=clean_text(record.get("exchange"), max_length=100),
            provider_publish_time=publish,
            market_symbol=clean_text(record.get("market_symbol"), max_length=50),
            side=side,
            metadata=sanitize_metadata({"kind": kind, **fields.pop("metadata", {})}),
            raw_payload_reference=ctx.raw_reference,
            estimated_received_time=receipt.estimated_received_time,
            received_time_basis=receipt.basis,
            assumed_delay_ms=receipt.assumed_delay_ms,
            received_time_assumption=receipt.assumption,
            **fields,
        )
        return NormalizedRecord(events=[event])

    def _classify(
        self, kind: str, record: Mapping[str, Any]
    ) -> tuple[EventType, Side | None, dict[str, Any]]:
        if kind in ("whale_trade", "large_trade"):
            side_text = (clean_text(record.get("side"), max_length=10) or "").lower()
            side = {"buy": Side.BUY, "sell": Side.SELL}.get(side_text)
            if side_text and side is None:
                raise ValueError(f"unrecognized side {side_text!r}")
            amounts = {
                "amount_usd": _decimal(record.get("notional_usd"), "notional_usd"),
                "amount_asset": _decimal(record.get("quantity"), "quantity"),
                "metadata": {"price": _text(_decimal(record.get("price"), "price"))},
            }
            if kind == "whale_trade":
                if side is None:
                    raise ValueError("a whale_trade needs an explicit side")
                return (
                    EventType.WHALE_TRADE_BUY if side is Side.BUY else EventType.WHALE_TRADE_SELL,
                    side, amounts,
                )
            return EventType.LARGE_EXECUTED_TRADE, side, amounts
        if kind == "liquidation":
            liquidated = (clean_text(record.get("liquidated_side"), max_length=10) or "").lower()
            if liquidated not in ("long", "short"):
                raise ValueError("a liquidation needs liquidated_side of long or short")
            return (
                EventType.LONG_LIQUIDATION if liquidated == "long" else EventType.SHORT_LIQUIDATION,
                None,
                {
                    "amount_usd": _decimal(record.get("notional_usd"), "notional_usd"),
                    "amount_asset": _decimal(record.get("quantity"), "quantity"),
                    "metadata": {},
                },
            )
        if kind == "funding":
            rate = _signed_decimal(record.get("funding_rate"), "funding_rate")
            if rate is None:
                raise ValueError("a funding record needs funding_rate")
            interval = _decimal(record.get("interval_hours"), "interval_hours")
            return (
                EventType.FUNDING_UPDATE, None,
                {"value": rate, "value_unit": FUNDING_UNIT,
                 "metadata": {"interval_hours": _text(interval)}},
            )
        if kind == "open_interest":
            usd = _decimal(record.get("open_interest_usd"), "open_interest_usd")
            quantity = _decimal(record.get("open_interest"), "open_interest")
            if usd is None and quantity is None:
                raise ValueError("an open_interest record needs open_interest_usd or open_interest")
            return EventType.OPEN_INTEREST_UPDATE, None, {"amount_usd": usd, "amount_asset": quantity, "metadata": {}}
        raise ValueError(f"unsupported kind {kind!r}")


_KINDS = frozenset({"whale_trade", "large_trade", "liquidation", "funding", "open_interest"})


def _event_time(record: Mapping[str, Any]) -> int:
    if record.get("time_ms") not in (None, ""):
        value = _decimal(record.get("time_ms"), "time_ms")
        return int(value) if value is not None else _fail("time_ms is missing")
    if record.get("time_s") not in (None, ""):
        value = _decimal(record.get("time_s"), "time_s")
        if value is None or value == 0:
            _fail("time_s is missing")
        if value >= Decimal("1e11"):
            _fail("time_s looks like milliseconds; use time_ms")
        return int(value * 1000)
    return _fail("no time_ms or time_s")


def _fail(message: str) -> int:
    raise ValueError(message)


def _required_text(record: Mapping[str, Any], key: str) -> str:
    text = clean_text(record.get(key), max_length=50)
    if not text:
        raise ValueError(f"{key} is missing")
    return text


def _upper(value: Any) -> str | None:
    text = clean_text(value, max_length=50)
    return None if text is None else text.upper()


def _decimal(value: Any, name: str) -> Decimal | None:
    number = _signed_decimal(value, name)
    if number is not None and number < 0:
        raise ValueError(f"{name} must not be negative")
    return number


def _signed_decimal(value: Any, name: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} is not a number")
    try:
        number = dec(value)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name} is not a number") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _optional_int(value: Any) -> int | None:
    number = _decimal(value, "time")
    return None if number is None else int(number)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _derived_id(provider: str, record: Mapping[str, Any]) -> str:
    fields = ("kind", "time_ms", "time_s", "symbol", "exchange", "market_symbol", "side",
              "notional_usd", "quantity", "price", "liquidated_side", "funding_rate",
              "open_interest_usd", "open_interest")
    key = provider + "|" + "|".join(str(record.get(field, "")) for field in fields)
    return "derived:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
