"""The normalized event model.

Every provider adapter turns its own payloads into :class:`ExternalEvent` and
into nothing else. An event describes something a provider reported. It does not
say what the something *means*: there is no ``bullish`` field, no score and no
direction on a transfer.

Three timestamps stay separate, always:

``event_time``
    when the underlying thing happened (a block time, a trade time).
``provider_publish_time``
    when the provider says it exposed the event, if it says so.
``system_received_time``
    when *our system* actually obtained it -- an observation, not an inference.

A historical import usually has no genuine ``system_received_time``. The event
then carries an explicit ``estimated_received_time`` together with the delay
assumed and the stated reason, and ``received_time_basis`` says which kind of
timing this is. Blockchain time is never silently used as the time we could have
acted, and an event with no observed and no estimated receipt time is simply
never available to a simulation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from crypto_research.decimals import dec, format_decimal

from .attribution import EntityType
from .errors import EventValidationError

SCHEMA_VERSION = "1"


class EventType(str, Enum):
    # -- on-chain transfers: something moved between addresses ---------------
    LARGE_TRANSFER = "LARGE_TRANSFER"
    EXCHANGE_INFLOW = "EXCHANGE_INFLOW"
    EXCHANGE_OUTFLOW = "EXCHANGE_OUTFLOW"
    EXCHANGE_TO_EXCHANGE = "EXCHANGE_TO_EXCHANGE"
    STABLECOIN_MINT = "STABLECOIN_MINT"
    STABLECOIN_BURN = "STABLECOIN_BURN"
    TOKEN_MINT = "TOKEN_MINT"
    TOKEN_BURN = "TOKEN_BURN"
    TREASURY_TRANSFER = "TREASURY_TRANSFER"
    UNKNOWN_TRANSFER = "UNKNOWN_TRANSFER"
    # -- executed market activity: a trade actually happened -----------------
    WHALE_TRADE_BUY = "WHALE_TRADE_BUY"
    WHALE_TRADE_SELL = "WHALE_TRADE_SELL"
    LARGE_EXECUTED_TRADE = "LARGE_EXECUTED_TRADE"
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    SHORT_LIQUIDATION = "SHORT_LIQUIDATION"
    # -- derivatives state: a reading, not an action --------------------------
    FUNDING_UPDATE = "FUNDING_UPDATE"
    OPEN_INTEREST_UPDATE = "OPEN_INTEREST_UPDATE"


class EventClass(str, Enum):
    """The evidentiary family. Transfers and executed trades never share one."""

    ON_CHAIN_TRANSFER = "ON_CHAIN_TRANSFER"
    EXECUTED_TRADE = "EXECUTED_TRADE"
    LIQUIDATION = "LIQUIDATION"
    DERIVATIVES_STATE = "DERIVATIVES_STATE"


_CLASS_BY_TYPE: dict[EventType, EventClass] = {
    EventType.LARGE_TRANSFER: EventClass.ON_CHAIN_TRANSFER,
    EventType.EXCHANGE_INFLOW: EventClass.ON_CHAIN_TRANSFER,
    EventType.EXCHANGE_OUTFLOW: EventClass.ON_CHAIN_TRANSFER,
    EventType.EXCHANGE_TO_EXCHANGE: EventClass.ON_CHAIN_TRANSFER,
    EventType.STABLECOIN_MINT: EventClass.ON_CHAIN_TRANSFER,
    EventType.STABLECOIN_BURN: EventClass.ON_CHAIN_TRANSFER,
    EventType.TOKEN_MINT: EventClass.ON_CHAIN_TRANSFER,
    EventType.TOKEN_BURN: EventClass.ON_CHAIN_TRANSFER,
    EventType.TREASURY_TRANSFER: EventClass.ON_CHAIN_TRANSFER,
    EventType.UNKNOWN_TRANSFER: EventClass.ON_CHAIN_TRANSFER,
    EventType.WHALE_TRADE_BUY: EventClass.EXECUTED_TRADE,
    EventType.WHALE_TRADE_SELL: EventClass.EXECUTED_TRADE,
    EventType.LARGE_EXECUTED_TRADE: EventClass.EXECUTED_TRADE,
    EventType.LONG_LIQUIDATION: EventClass.LIQUIDATION,
    EventType.SHORT_LIQUIDATION: EventClass.LIQUIDATION,
    EventType.FUNDING_UPDATE: EventClass.DERIVATIVES_STATE,
    EventType.OPEN_INTEREST_UPDATE: EventClass.DERIVATIVES_STATE,
}

# Transfers between holders. Issuer actions (mint/burn) are deliberately not in
# this set: they are counted by their own features, not as "whale movement".
HOLDER_TRANSFER_TYPES = frozenset(
    {
        EventType.LARGE_TRANSFER,
        EventType.EXCHANGE_INFLOW,
        EventType.EXCHANGE_OUTFLOW,
        EventType.EXCHANGE_TO_EXCHANGE,
        EventType.TREASURY_TRANSFER,
        EventType.UNKNOWN_TRANSFER,
    }
)
EXECUTED_TRADE_TYPES = frozenset(
    {EventType.WHALE_TRADE_BUY, EventType.WHALE_TRADE_SELL, EventType.LARGE_EXECUTED_TRADE}
)
LIQUIDATION_TYPES = frozenset({EventType.LONG_LIQUIDATION, EventType.SHORT_LIQUIDATION})


class Side(str, Enum):
    """Set ONLY when a provider explicitly identifies an executed buy or sell."""

    BUY = "BUY"
    SELL = "SELL"


class ReceiptBasis(str, Enum):
    OBSERVED = "OBSERVED"  # our system recorded the moment it received the event
    ASSUMED_DELAY = "ASSUMED_DELAY"  # derived under an explicit, recorded assumption
    UNKNOWN = "UNKNOWN"  # no receipt time of any kind: never usable in a simulation


def make_event_id(provider: str, provider_event_id: str, schema_version: str = SCHEMA_VERSION) -> str:
    """A stable identity for one provider's record of one event."""
    blob = f"{provider}\x1f{provider_event_id}\x1f{schema_version}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _is_ms(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1


def _coerce_enum(enum_cls: type[Enum], value: Any, name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise EventValidationError(f"{name}: {value!r} is not a valid {enum_cls.__name__}") from exc


def _check_decimal(name: str, value: Any, *, non_negative: bool) -> Decimal | None:
    if value is None:
        return None
    try:
        number = dec(value)
    except (ValueError, InvalidOperation) as exc:
        raise EventValidationError(f"{name}: {value!r} is not a number") from exc
    if not number.is_finite():
        raise EventValidationError(f"{name}: must be finite")
    if non_negative and number < 0:
        raise EventValidationError(f"{name}: must not be negative")
    return number


@dataclass(frozen=True, slots=True)
class ExternalEvent:
    event_id: str
    provider: str
    provider_event_id: str
    event_type: EventType
    asset: str
    event_time: int
    system_received_time: int | None = None
    schema_version: str = SCHEMA_VERSION
    quote_asset: str | None = None
    blockchain: str | None = None
    exchange: str | None = None
    provider_publish_time: int | None = None
    amount_asset: Decimal | None = None
    amount_usd: Decimal | None = None
    # A scalar reading that is not an amount of something: a funding rate. Its
    # meaning is stated in ``value_unit``.
    value: Decimal | None = None
    value_unit: str | None = None
    source_address: str | None = None
    destination_address: str | None = None
    source_entity: str | None = None
    destination_entity: str | None = None
    source_entity_type: EntityType | None = None
    destination_entity_type: EntityType | None = None
    transaction_hash: str | None = None
    market_symbol: str | None = None
    side: Side | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)
    raw_payload_reference: str | None = None
    # -- timing we could not observe ------------------------------------------
    estimated_received_time: int | None = None
    received_time_basis: ReceiptBasis = ReceiptBasis.UNKNOWN
    assumed_delay_ms: int | None = None
    received_time_assumption: str | None = None

    def __post_init__(self) -> None:
        put = object.__setattr__
        for name in ("event_id", "provider", "provider_event_id", "asset"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EventValidationError(f"{name} must be a non-empty string")
        put(self, "event_type", _coerce_enum(EventType, self.event_type, "event_type"))
        put(self, "received_time_basis", _coerce_enum(ReceiptBasis, self.received_time_basis, "received_time_basis"))
        if self.side is not None:
            put(self, "side", _coerce_enum(Side, self.side, "side"))
        for name in ("source_entity_type", "destination_entity_type"):
            value = getattr(self, name)
            if value is not None:
                put(self, name, _coerce_enum(EntityType, value, name))

        if not _is_ms(self.event_time):
            raise EventValidationError("event_time must be integer epoch milliseconds")
        for name in ("provider_publish_time", "system_received_time", "estimated_received_time"):
            value = getattr(self, name)
            if value is not None and not _is_ms(value):
                raise EventValidationError(f"{name} must be integer epoch milliseconds or None")

        put(self, "amount_asset", _check_decimal("amount_asset", self.amount_asset, non_negative=True))
        put(self, "amount_usd", _check_decimal("amount_usd", self.amount_usd, non_negative=True))
        put(self, "value", _check_decimal("value", self.value, non_negative=False))

        self._check_receipt_consistency()
        self._check_side_consistency()
        self._check_type_requirements()

    def _check_receipt_consistency(self) -> None:
        basis = self.received_time_basis
        if basis is ReceiptBasis.OBSERVED:
            if (
                self.system_received_time is None
                or self.estimated_received_time is not None
                or self.assumed_delay_ms is not None
            ):
                raise EventValidationError(
                    "an OBSERVED receipt needs system_received_time and no estimate"
                )
        elif basis is ReceiptBasis.ASSUMED_DELAY:
            if (
                self.system_received_time is not None
                or self.estimated_received_time is None
                or not _is_ms(self.assumed_delay_ms)
                or not (self.received_time_assumption or "").strip()
            ):
                raise EventValidationError(
                    "an ASSUMED_DELAY receipt needs estimated_received_time, "
                    "assumed_delay_ms and a stated assumption, and no observed time"
                )
            if self.estimated_received_time < self.event_time:
                raise EventValidationError("an estimated receipt cannot precede the event")
        else:
            if self.system_received_time is not None or self.estimated_received_time is not None:
                raise EventValidationError("an UNKNOWN receipt basis cannot carry a receipt time")

    def _check_side_consistency(self) -> None:
        if self.event_type is EventType.WHALE_TRADE_BUY and self.side is not Side.BUY:
            raise EventValidationError("WHALE_TRADE_BUY requires an explicit BUY side")
        if self.event_type is EventType.WHALE_TRADE_SELL and self.side is not Side.SELL:
            raise EventValidationError("WHALE_TRADE_SELL requires an explicit SELL side")
        if self.side is not None and self.event_type not in EXECUTED_TRADE_TYPES:
            raise EventValidationError(
                f"side is only for executed trades; {self.event_type.value} must not carry one"
            )

    def _check_type_requirements(self) -> None:
        if self.event_type is EventType.FUNDING_UPDATE and self.value is None:
            raise EventValidationError("FUNDING_UPDATE needs a value")
        if (
            self.event_type is EventType.OPEN_INTEREST_UPDATE
            and self.amount_usd is None
            and self.amount_asset is None
        ):
            raise EventValidationError("OPEN_INTEREST_UPDATE needs amount_usd or amount_asset")

    # -- derived views -------------------------------------------------------

    @property
    def event_class(self) -> EventClass:
        return _CLASS_BY_TYPE[self.event_type]

    @property
    def available_time(self) -> int | None:
        """When a simulation may first use this event, or ``None`` if never."""
        if self.received_time_basis is ReceiptBasis.OBSERVED:
            return self.system_received_time
        if self.received_time_basis is ReceiptBasis.ASSUMED_DELAY:
            return self.estimated_received_time
        return None

    def is_available_at(self, as_of: int, *, allow_estimated: bool = False) -> bool:
        """May a decision made at ``as_of`` use this event?

        Observed receipts qualify when ``system_received_time <= as_of``.
        Estimated receipts qualify only when the caller explicitly allows an
        assumption. An event with no receipt time never qualifies.
        """
        if self.received_time_basis is ReceiptBasis.OBSERVED:
            return self.system_received_time is not None and self.system_received_time <= as_of
        if self.received_time_basis is ReceiptBasis.ASSUMED_DELAY and allow_estimated:
            return self.estimated_received_time is not None and self.estimated_received_time <= as_of
        return False

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def money(value: Decimal | None) -> str | None:
            return None if value is None else format_decimal(value)

        return {
            "event_id": self.event_id,
            "provider": self.provider,
            "provider_event_id": self.provider_event_id,
            "event_type": self.event_type.value,
            "asset": self.asset,
            "event_time": self.event_time,
            "system_received_time": self.system_received_time,
            "schema_version": self.schema_version,
            "quote_asset": self.quote_asset,
            "blockchain": self.blockchain,
            "exchange": self.exchange,
            "provider_publish_time": self.provider_publish_time,
            "amount_asset": money(self.amount_asset),
            "amount_usd": money(self.amount_usd),
            "value": money(self.value),
            "value_unit": self.value_unit,
            "source_address": self.source_address,
            "destination_address": self.destination_address,
            "source_entity": self.source_entity,
            "destination_entity": self.destination_entity,
            "source_entity_type": None if self.source_entity_type is None else self.source_entity_type.value,
            "destination_entity_type": (
                None if self.destination_entity_type is None else self.destination_entity_type.value
            ),
            "transaction_hash": self.transaction_hash,
            "market_symbol": self.market_symbol,
            "side": None if self.side is None else self.side.value,
            "metadata": dict(self.metadata),
            "raw_payload_reference": self.raw_payload_reference,
            "estimated_received_time": self.estimated_received_time,
            "received_time_basis": self.received_time_basis.value,
            "assumed_delay_ms": self.assumed_delay_ms,
            "received_time_assumption": self.received_time_assumption,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExternalEvent":
        data = dict(payload)
        return cls(**data)


def timestamp_problems(
    event: ExternalEvent,
    *,
    ingest_time_ms: int,
    tolerance_ms: int = 5_000,
    max_staleness_ms: int | None = None,
) -> list[str]:
    """Reasons an event's timestamps are not acceptable, empty when they are.

    ``tolerance_ms`` absorbs ordinary clock skew between a provider and us; it
    is not a licence for an event to come from the future. Historical imports
    pass ``max_staleness_ms=None``; live ingestion passes a limit so an old
    event replayed by a provider is not treated as fresh news.
    """
    problems: list[str] = []
    limit = ingest_time_ms + tolerance_ms
    if event.event_time > limit:
        problems.append("future_event_time")
    if event.provider_publish_time is not None:
        if event.provider_publish_time > limit:
            problems.append("future_publish_time")
        if event.provider_publish_time < event.event_time - tolerance_ms:
            problems.append("publish_before_event")
    if event.received_time_basis is ReceiptBasis.OBSERVED and event.system_received_time is not None:
        received = event.system_received_time
        if received > limit:
            problems.append("future_received_time")
        if received < event.event_time - tolerance_ms:
            problems.append("received_before_event")
        if event.provider_publish_time is not None and received < event.provider_publish_time - tolerance_ms:
            problems.append("received_before_publish")
    if max_staleness_ms is not None and ingest_time_ms - event.event_time > max_staleness_ms:
        problems.append("stale_at_ingest")
    return problems
