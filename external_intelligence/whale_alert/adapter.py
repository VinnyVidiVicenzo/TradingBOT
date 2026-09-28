"""Whale Alert: large on-chain transfers, normalized without being interpreted.

What can be read
----------------
There is no free Whale Alert data API (checked 2026-09-28 at
developer.whale-alert.io: an Alerts API on a paid plan with a 7-day trial, and an
Enterprise REST API on a paid plan; both authenticate with an API key). This
adapter therefore reads **user-provided exports only**, in either of two shapes:

* the documented Enterprise transaction: ``hash``, ``timestamp`` (Unix seconds),
  and ``sub_transactions``, each with ``symbol``, ``transaction_type``, and
  ``inputs`` / ``outputs`` lists of ``{amount, address, owner, owner_type, ...}``;
* a flat record defined by this project (``whale_alert_flat_v1``): ``id``,
  ``timestamp`` (seconds) or ``timestamp_ms``, ``symbol``, ``blockchain``,
  ``transaction_type``, ``hash``, ``amount``, ``amount_usd``, and the two ends as
  ``from`` / ``to`` (an object, or a plain label) or ``from_address`` /
  ``from_owner`` / ``from_owner_type`` and the same for ``to``.

The field names above come from the provider's documentation page. **No live
payload was available to check them against**, so this adapter validates strictly
and quarantines what it does not recognise rather than guessing.

What is never done
------------------
* A transfer is never called a sale or a purchase. ``EXCHANGE_INFLOW`` means "value
  moved to an address the provider explicitly typed as an exchange". Nothing more.
* An exchange is recognised only from the provider's own ``owner_type`` value.
  A name such as "binance" in ``owner`` is not evidence of an exchange here.
* Mixed or missing attribution is kept as mixed or missing, never resolved.
* A missing USD amount stays missing; it is never made zero.
* Amounts are never converted between USD and a stablecoin: ``USDT`` stays
  ``USDT``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from crypto_research.decimals import dec

from ..attribution import EntityType, build_attribution, normalize_entity_type
from ..base.coverage import FAMILY_TRANSFERS
from ..base.provider import NormalizeContext, NormalizedRecord, Provider, Rejection
from ..base.receipt import ReceiptFields
from ..events import EventType, ExternalEvent, make_event_id
from ..sanitize import clean_text, sanitize_metadata

PROVIDER = "whale_alert"
ADAPTER_VERSION = "1"
SCHEMA = "1"

# A fixed, documented list. Assets stay distinct everywhere: this decides only
# whether a mint/burn is filed as a stablecoin mint/burn or a token mint/burn.
STABLECOINS = frozenset(
    {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "FDUSD", "PYUSD", "GUSD", "USDD", "LUSD", "FRAX"}
)
_SUPPORTED_TYPES = frozenset({"transfer", "mint", "burn"})
# Any of these marks a record as the flat shape (a CSV may carry only owner columns).
_FLAT_END_KEYS = (
    "from", "to", "from_address", "to_address",
    "from_owner", "to_owner", "from_owner_type", "to_owner_type",
)

LIVE_STATUS = (
    "off: Whale Alert has no free data API (its Alerts API is a paid plan with a 7-day "
    "trial and its Enterprise REST API is a paid plan; both use an API key, which this "
    "zone cannot use). Historical research uses user-provided exports only."
)
TERMS_NOTE = (
    "Use an export only within the licence it came with. Do not redistribute. "
    "Social-media scraping of the public feed is not supported."
)


@dataclass(frozen=True, slots=True)
class _Party:
    """One end of a transfer, exactly as the provider described it."""

    address: str | None
    owner: str | None
    owner_type_raw: str | None
    entity_type: EntityType
    amount: Decimal | None = None
    amount_usd: Decimal | None = None
    amount_usd_complete: bool = False


class WhaleAlertAdapter(Provider):
    name = PROVIDER
    schema_version = SCHEMA
    adapter_version = ADAPTER_VERSION
    families = (FAMILY_TRANSFERS,)
    envelope_keys = ("transactions", "result", "data", "records")
    live_ingestion_status = LIVE_STATUS
    terms_note = TERMS_NOTE

    def normalize_record(self, record: Any, ctx: NormalizeContext) -> NormalizedRecord:
        if not isinstance(record, Mapping):
            return NormalizedRecord.rejected("record_not_an_object", type(record).__name__)
        if "sub_transactions" in record:
            return self._enterprise(record, ctx)
        if any(key in record for key in _FLAT_END_KEYS):
            return self._flat(record, ctx)
        return NormalizedRecord.rejected("unrecognized_shape", "neither sub_transactions nor from/to present")

    # -- documented Enterprise shape -----------------------------------------

    def _enterprise(self, rec: Mapping[str, Any], ctx: NormalizeContext) -> NormalizedRecord:
        try:
            event_time = _seconds_to_ms(rec.get("timestamp"))
        except ValueError as exc:
            return NormalizedRecord.rejected("invalid_timestamp", str(exc))
        tx_hash = clean_text(rec.get("hash"), max_length=200)
        if not tx_hash:
            return NormalizedRecord.rejected("missing_transaction_hash")
        subs = rec.get("sub_transactions")
        if not isinstance(subs, list) or not subs:
            return NormalizedRecord.rejected("no_sub_transactions")
        try:
            receipt = ctx.policy.apply(
                event_time=event_time,
                provider_publish_time=None,
                record_receipt_ms=_optional_ms(rec.get("system_received_time_ms")),
                ingest_time_ms=ctx.ingest_time_ms,
            )
        except ValueError as exc:
            return NormalizedRecord.rejected("invalid_receipt_time", str(exc))
        blockchain = _lower(rec.get("blockchain"))
        out = NormalizedRecord()
        for index, sub in enumerate(subs):
            if not isinstance(sub, Mapping):
                out.skipped.append(Rejection("invalid_sub_transaction", f"sub_transactions[{index}] is not an object"))
                continue
            try:
                self._enterprise_sub(out, rec, sub, index, tx_hash, event_time, blockchain, receipt, ctx)
            except (ValueError, InvalidOperation) as exc:
                out.skipped.append(Rejection("invalid_sub_transaction", f"sub_transactions[{index}]: {exc}"))
        if not out.events:
            first = out.skipped[0] if out.skipped else Rejection("no_events", "")
            return NormalizedRecord.rejected(first.reason, first.detail)
        return out

    def _enterprise_sub(
        self, out: NormalizedRecord, rec: Mapping[str, Any], sub: Mapping[str, Any], index: int,
        tx_hash: str, event_time: int, blockchain: str | None, receipt: ReceiptFields,
        ctx: NormalizeContext,
    ) -> None:
        symbol = clean_text(sub.get("symbol"), max_length=30)
        if not symbol:
            raise ValueError("sub-transaction has no symbol")
        asset = symbol.upper()
        kind = (clean_text(sub.get("transaction_type"), max_length=30) or "").lower()
        if kind not in _SUPPORTED_TYPES:
            out.skipped.append(Rejection("unsupported_transaction_type", kind or "missing"))
            return
        inputs = _group([_party_from_mapping(item) for item in _as_list(sub.get("inputs"))])
        outputs = _group([_party_from_mapping(item) for item in _as_list(sub.get("outputs"))])
        unit_price = _first_decimal(sub.get("unit_price_usd"), rec.get("unit_price_usd"))
        common = dict(
            tx_hash=tx_hash, event_time=event_time, asset=asset, blockchain=blockchain,
            receipt=receipt, ctx=ctx, shape="enterprise", unit_price=unit_price,
            extra={"sub_index": index, "height": rec.get("height"), "index_in_block": rec.get("index_in_block")},
        )
        if kind == "transfer":
            source_addresses = {party.address for party in inputs}
            destinations = [party for party in outputs if party.address not in source_addresses]
            if not destinations:
                out.skipped.append(Rejection("no_external_destination", "every output returns to an input address"))
                return
            for party in destinations:
                self._emit(out, kind="transfer", sources=inputs, destination=party, **common)
        elif kind == "mint":
            if not outputs:
                out.skipped.append(Rejection("mint_without_recipient", ""))
                return
            for party in outputs:
                self._emit(out, kind="mint", sources=[], destination=party, **common)
        else:
            if not inputs:
                out.skipped.append(Rejection("burn_without_holder", ""))
                return
            for party in inputs:
                self._emit(out, kind="burn", sources=[party], destination=None, **common)

    # -- flat shape ----------------------------------------------------------

    def _flat(self, rec: Mapping[str, Any], ctx: NormalizeContext) -> NormalizedRecord:
        try:
            if rec.get("timestamp_ms") not in (None, ""):
                event_time = _int_ms(rec.get("timestamp_ms"), "timestamp_ms")
            else:
                event_time = _seconds_to_ms(rec.get("timestamp"))
            symbol = clean_text(rec.get("symbol"), max_length=30)
            if not symbol:
                return NormalizedRecord.rejected("missing_symbol")
            kind = (clean_text(rec.get("transaction_type"), max_length=30) or "transfer").lower()
            if kind not in _SUPPORTED_TYPES:
                return NormalizedRecord.rejected("unsupported_transaction_type", kind)
            amount = _to_decimal(rec.get("amount"), "amount")
            amount_usd = _to_decimal(rec.get("amount_usd"), "amount_usd")
            publish = _optional_ms(rec.get("provider_publish_time_ms"))
            receipt = ctx.policy.apply(
                event_time=event_time, provider_publish_time=publish,
                record_receipt_ms=_optional_ms(rec.get("system_received_time_ms")),
                ingest_time_ms=ctx.ingest_time_ms,
            )
        except ValueError as exc:
            return NormalizedRecord.rejected("invalid_record", str(exc))
        source = _flat_party(rec, "from")
        destination = _flat_party(rec, "to")
        tx_hash = clean_text(rec.get("hash"), max_length=200)
        provider_id = clean_text(rec.get("id"), max_length=200) or _derived_id(rec)
        out = NormalizedRecord()
        self._emit(
            out, kind=kind, sources=[source] if source else [], destination=destination,
            tx_hash=tx_hash, event_time=event_time, asset=symbol.upper(),
            blockchain=_lower(rec.get("blockchain")), receipt=receipt, ctx=ctx,
            shape="flat", unit_price=None, extra={}, flat_amount=amount, flat_amount_usd=amount_usd,
            flat_publish=publish, flat_provider_id=provider_id,
        )
        return out

    # -- shared event construction --------------------------------------------

    def _emit(
        self, out: NormalizedRecord, *, kind: str, sources: Sequence[_Party],
        destination: _Party | None, tx_hash: str | None, event_time: int, asset: str,
        blockchain: str | None, receipt: ReceiptFields, ctx: NormalizeContext, shape: str,
        unit_price: Decimal | None, extra: Mapping[str, Any],
        flat_amount: Decimal | None = None, flat_amount_usd: Decimal | None = None,
        flat_publish: int | None = None, flat_provider_id: str | None = None,
    ) -> None:
        source_type: EntityType | None = _aggregate_type(sources)
        note: str | None = None
        if kind == "transfer":
            event_type, note = classify_transfer(
                source_type, destination.entity_type if destination else EntityType.UNKNOWN
            )
        elif kind == "mint":
            event_type = EventType.STABLECOIN_MINT if asset in STABLECOINS else EventType.TOKEN_MINT
        else:
            event_type = EventType.STABLECOIN_BURN if asset in STABLECOINS else EventType.TOKEN_BURN

        # The party whose amount describes the event.
        subject = sources[0] if kind == "burn" and sources else destination
        if shape == "flat":
            amount, usd, basis = flat_amount, flat_amount_usd, ("provider_amount_usd" if flat_amount_usd is not None else "missing")
        else:
            amount = subject.amount if subject else None
            if subject is not None and subject.amount_usd_complete and subject.amount_usd is not None:
                usd, basis = subject.amount_usd, "provider_amount_usd"
            elif unit_price is not None and amount is not None:
                usd, basis = amount * unit_price, "derived_amount_x_unit_price_usd"
            else:
                usd, basis = None, "missing"

        single_source = sources[0] if len(sources) == 1 else None
        address_key = (subject.address if subject else None) or ""
        if shape == "flat":
            provider_event_id = flat_provider_id or ""
        else:
            provider_event_id = f"{tx_hash}:{extra['sub_index']}:{kind}:{address_key}"
        metadata: dict[str, Any] = {
            "shape": shape,
            "provider_transaction_type": kind,
            "amount_usd_basis": basis,
            "source_count": len(sources),
        }
        metadata.update({k: v for k, v in extra.items() if v is not None})
        if note:
            metadata["attribution_note"] = note
        if len(sources) > 1:
            metadata["source_addresses"] = [p.address for p in sources if p.address][:10]
        out.events.append(
            ExternalEvent(
                event_id=make_event_id(PROVIDER, provider_event_id, SCHEMA),
                provider=PROVIDER,
                provider_event_id=provider_event_id,
                event_type=event_type,
                asset=asset,
                event_time=event_time,
                system_received_time=receipt.system_received_time,
                schema_version=SCHEMA,
                blockchain=blockchain,
                provider_publish_time=flat_publish,
                amount_asset=amount,
                amount_usd=usd,
                source_address=single_source.address if single_source else None,
                destination_address=destination.address if destination else None,
                source_entity=single_source.owner if single_source else None,
                destination_entity=destination.owner if destination else None,
                source_entity_type=source_type if sources else None,
                destination_entity_type=destination.entity_type if destination else None,
                transaction_hash=tx_hash,
                metadata=sanitize_metadata(metadata),
                raw_payload_reference=ctx.raw_reference,
                estimated_received_time=receipt.estimated_received_time,
                received_time_basis=receipt.basis,
                assumed_delay_ms=receipt.assumed_delay_ms,
                received_time_assumption=receipt.assumption,
            )
        )
        attributed_at, time_basis = _attribution_time(event_time, receipt)
        for party in [*sources, *([destination] if destination else [])]:
            attribution = build_attribution(
                provider=PROVIDER, address=party.address, raw_label=party.owner,
                raw_entity_type=party.owner_type_raw, attributed_at=attributed_at,
                time_basis=time_basis, blockchain=blockchain, raw_payload_reference=ctx.raw_reference,
            )
            if attribution is not None:
                out.attributions.append(attribution)


def classify_transfer(source: EntityType | None, destination: EntityType) -> tuple[EventType, str | None]:
    """File a transfer by the provider's own attribution. Never by direction alone.

    ``source`` is ``None`` when several senders of *different* types were involved:
    an exchange flow cannot then be claimed, so the transfer stays a plain
    ``LARGE_TRANSFER`` and says why.
    """
    if source is None:
        return EventType.LARGE_TRANSFER, "mixed_source_types"
    if source is EntityType.EXCHANGE and destination is EntityType.EXCHANGE:
        return EventType.EXCHANGE_TO_EXCHANGE, None
    if destination is EntityType.EXCHANGE:
        return EventType.EXCHANGE_INFLOW, None
    if source is EntityType.EXCHANGE:
        return EventType.EXCHANGE_OUTFLOW, None
    if EntityType.TREASURY in (source, destination):
        return EventType.TREASURY_TRANSFER, None
    if source is EntityType.UNKNOWN and destination is EntityType.UNKNOWN:
        return EventType.UNKNOWN_TRANSFER, None
    return EventType.LARGE_TRANSFER, None


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("inputs/outputs must be a list")
    return value


def _party_from_mapping(item: Any) -> _Party:
    if not isinstance(item, Mapping):
        raise ValueError("an input/output is not an object")
    owner = clean_text(item.get("owner"))
    raw_type = clean_text(item.get("owner_type"), max_length=100)
    usd = _to_decimal(item.get("amount_usd"), "amount_usd")
    return _Party(
        address=clean_text(item.get("address"), max_length=200),
        owner=owner,
        owner_type_raw=raw_type,
        entity_type=normalize_entity_type(raw_type),
        amount=_to_decimal(item.get("amount"), "amount"),
        amount_usd=usd,
        amount_usd_complete=usd is not None,
    )


def _flat_party(rec: Mapping[str, Any], side: str) -> _Party | None:
    value = rec.get(side)
    if isinstance(value, Mapping):
        return _party_from_mapping(value)
    if isinstance(value, str) and clean_text(value):
        # A bare label ("unknown wallet", "Binance"). No type is given, so none is assumed.
        return _Party(None, clean_text(value), None, EntityType.UNKNOWN)
    address = clean_text(rec.get(f"{side}_address"), max_length=200)
    owner = clean_text(rec.get(f"{side}_owner"))
    raw_type = clean_text(rec.get(f"{side}_owner_type"), max_length=100)
    if address is None and owner is None and raw_type is None:
        return None
    return _Party(address, owner, raw_type, normalize_entity_type(raw_type))


def _group(parties: Sequence[_Party]) -> list[_Party]:
    """Merge parties that share an address, keeping first-seen order.

    Parties with no address cannot be identified as a counterparty and are left
    out of the grouping (an ``OP_RETURN``-style output, for instance).
    """
    merged: dict[str, _Party] = {}
    for party in parties:
        if party.address is None:
            continue
        current = merged.get(party.address)
        if current is None:
            merged[party.address] = party
            continue
        amount = _add(current.amount, party.amount)
        both_usd = current.amount_usd_complete and party.amount_usd_complete
        usd = _add(current.amount_usd, party.amount_usd) if both_usd else None
        merged[party.address] = _Party(
            current.address, current.owner or party.owner,
            current.owner_type_raw or party.owner_type_raw,
            current.entity_type if current.entity_type is not EntityType.UNKNOWN else party.entity_type,
            amount, usd, both_usd,
        )
    return list(merged.values())


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a + b


def _aggregate_type(parties: Sequence[_Party]) -> EntityType | None:
    if not parties:
        return EntityType.UNKNOWN
    kinds = {party.entity_type for party in parties}
    return next(iter(kinds)) if len(kinds) == 1 else None


def _attribution_time(event_time: int, receipt: ReceiptFields) -> tuple[int, str]:
    if receipt.system_received_time is not None:
        return receipt.system_received_time, "observed"
    if receipt.estimated_received_time is not None:
        return receipt.estimated_received_time, "assumed_delay"
    return event_time, "event_time_proxy"


def _lower(value: Any) -> str | None:
    text = clean_text(value, max_length=50)
    return None if text is None else text.lower()


def _to_decimal(value: Any, name: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} is not a number")
    try:
        number = dec(value)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name} is not a number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")
    return number


def _first_decimal(*values: Any) -> Decimal | None:
    for value in values:
        number = _to_decimal(value, "unit_price_usd")
        if number is not None:
            return number
    return None


def _seconds_to_ms(value: Any) -> int:
    number = _to_decimal(value, "timestamp")
    if number is None or number == 0:
        raise ValueError("timestamp is missing")
    if number >= Decimal("1e11"):
        raise ValueError("timestamp looks like milliseconds; Whale Alert timestamps are Unix seconds")
    return int(number * 1000)


def _int_ms(value: Any, name: str) -> int:
    number = _to_decimal(value, name)
    if number is None:
        raise ValueError(f"{name} is missing")
    return int(number)


def _optional_ms(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _int_ms(value, "time")


def _derived_id(rec: Mapping[str, Any]) -> str:
    """A deterministic id for a flat record that came without one."""
    key = "|".join(
        str(rec.get(field, "")) for field in
        ("hash", "timestamp", "timestamp_ms", "symbol", "amount", "from", "to",
         "from_address", "to_address")
    )
    return "flat:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
