"""Transaction cost, spread, slippage and latency models.

Every number in here is an **assumption** until it is replaced with a measured
one. They are therefore all configurable, all recorded in the experiment
record, and all labeled with where they came from.

On fees specifically: Binance.US publishes a tiered fee schedule, and the rate
that applies to a given account depends on 30-day volume, on any fee
promotions in force at the time, and on whether fees are paid in BNB. There is
no single universal Binance.US trading fee, so this module does not pretend
there is one. The defaults below are a *placeholder* to be overridden per
experiment.

A later authenticated version of this system should read the account's actual
commission rates from Binance.US rather than assuming them. That is out of
scope for V1, which holds no credentials -- see docs/trading-bot/SAFETY.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Mapping, Protocol, Sequence

from ..decimals import ZERO, dec, div, format_decimal, mul
from ..errors import ConfigError
from .orders import OrderIntent, OrderSide

BPS = Decimal("0.0001")


class CommissionAssetPolicy(str, Enum):
    """Which asset the commission is taken from.

    RECEIVED_ASSET matches Binance spot behaviour for an account not paying
    fees in BNB: a BUY is charged in the base asset it receives. QUOTE_ASSET
    is the simpler convention some research uses, and is offered because
    switching it changes accumulation results and the difference should be
    visible rather than baked in.
    """

    RECEIVED_ASSET = "RECEIVED_ASSET"
    QUOTE_ASSET = "QUOTE_ASSET"


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """A maker/taker pair with provenance."""

    maker_rate: Decimal
    taker_rate: Decimal
    source: str = "ASSUMPTION"
    note: str = ""

    def __post_init__(self) -> None:
        for name, rate in (("maker_rate", self.maker_rate), ("taker_rate", self.taker_rate)):
            if rate < 0 or rate >= 1:
                raise ConfigError(f"{name} must be in [0, 1), got {rate}")

    def to_dict(self) -> dict[str, object]:
        return {
            "maker_rate": format_decimal(self.maker_rate),
            "taker_rate": format_decimal(self.taker_rate),
            "source": self.source,
            "note": self.note,
        }


# Placeholder only. Replace per experiment with the rates that actually
# applied to the account over the backtest window.
DEFAULT_FEE_SCHEDULE = FeeSchedule(
    maker_rate=dec("0.0040"),
    taker_rate=dec("0.0060"),
    source="ASSUMPTION",
    note=(
        "Unverified placeholder standing in for a Binance.US spot fee tier. "
        "Not a quoted rate, not tied to any account, and not valid for any "
        "particular date. Override per experiment."
    ),
)


class FeeModel(Protocol):
    """Returns the commission rate that applies to one hypothetical order."""

    def rate_for(self, intent: OrderIntent, *, is_maker: bool) -> Decimal: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(slots=True)
class FlatFeeModel:
    """One maker/taker pair for everything, with optional per-symbol overrides."""

    schedule: FeeSchedule = DEFAULT_FEE_SCHEDULE
    per_symbol: Mapping[str, FeeSchedule] = field(default_factory=dict)
    commission_asset_policy: CommissionAssetPolicy = CommissionAssetPolicy.RECEIVED_ASSET

    def rate_for(self, intent: OrderIntent, *, is_maker: bool) -> Decimal:
        schedule = self.per_symbol.get(intent.symbol, self.schedule)
        return schedule.maker_rate if is_maker else schedule.taker_rate

    def describe(self) -> dict[str, object]:
        return {
            "model": "FlatFeeModel",
            "default": self.schedule.to_dict(),
            "per_symbol": {
                symbol: sched.to_dict() for symbol, sched in sorted(self.per_symbol.items())
            },
            "commission_asset_policy": self.commission_asset_policy.value,
        }


class SpreadModel(Protocol):
    """Half-spread as a fraction of the reference price."""

    def half_spread_fraction(self, symbol: str, *, observed: Decimal | None) -> Decimal: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(slots=True)
class FixedSpreadModel:
    """Constant half-spread in basis points, optionally per symbol.

    When a real top-of-book quote is available the simulator passes it in as
    ``observed`` and ``prefer_observed`` makes it win. On a bar-only dataset
    there is no observed spread, and the configured assumption is used.
    """

    default_bps: Decimal = dec("5")
    per_symbol_bps: Mapping[str, Decimal] = field(default_factory=dict)
    prefer_observed: bool = True
    note: str = "ASSUMPTION: flat half-spread, not measured from order-book history."

    def half_spread_fraction(self, symbol: str, *, observed: Decimal | None) -> Decimal:
        if self.prefer_observed and observed is not None and observed >= 0:
            return observed
        bps = self.per_symbol_bps.get(symbol, self.default_bps)
        return mul(bps, BPS) / 2

    def describe(self) -> dict[str, object]:
        return {
            "model": "FixedSpreadModel",
            "default_bps": format_decimal(self.default_bps),
            "per_symbol_bps": {
                symbol: format_decimal(value)
                for symbol, value in sorted(self.per_symbol_bps.items())
            },
            "prefer_observed": self.prefer_observed,
            "note": self.note,
        }


class SlippageModel(Protocol):
    """Extra adverse price movement, as a fraction of the reference price."""

    def slippage_fraction(
        self, intent: OrderIntent, *, notional: Decimal, bar_quote_volume: Decimal | None
    ) -> Decimal: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(slots=True)
class FixedSlippageModel:
    """Constant slippage in basis points."""

    bps: Decimal = dec("5")
    per_symbol_bps: Mapping[str, Decimal] = field(default_factory=dict)
    note: str = "ASSUMPTION: flat slippage, independent of order size."

    def slippage_fraction(
        self, intent: OrderIntent, *, notional: Decimal, bar_quote_volume: Decimal | None
    ) -> Decimal:
        return mul(self.per_symbol_bps.get(intent.symbol, self.bps), BPS)

    def describe(self) -> dict[str, object]:
        return {
            "model": "FixedSlippageModel",
            "bps": format_decimal(self.bps),
            "per_symbol_bps": {
                symbol: format_decimal(value)
                for symbol, value in sorted(self.per_symbol_bps.items())
            },
            "note": self.note,
        }


@dataclass(slots=True)
class ParticipationSlippageModel:
    """Slippage that grows with the share of bar volume the order consumes.

    ``fraction = base + coefficient * sqrt(participation)``. Square-root
    impact is a conventional starting shape, not a calibrated one; at the
    order sizes this project is concerned with (tens of dollars against
    liquid pairs) participation is effectively nil and this reduces to the
    base term.
    """

    base_bps: Decimal = dec("2")
    coefficient_bps: Decimal = dec("50")
    note: str = "ASSUMPTION: square-root impact shape, uncalibrated."

    def slippage_fraction(
        self, intent: OrderIntent, *, notional: Decimal, bar_quote_volume: Decimal | None
    ) -> Decimal:
        base = mul(self.base_bps, BPS)
        if not bar_quote_volume or bar_quote_volume <= 0 or notional <= 0:
            return base
        participation = div(notional, bar_quote_volume)
        return base + mul(mul(self.coefficient_bps, BPS), participation.sqrt())

    def describe(self) -> dict[str, object]:
        return {
            "model": "ParticipationSlippageModel",
            "base_bps": format_decimal(self.base_bps),
            "coefficient_bps": format_decimal(self.coefficient_bps),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class LatencyModel:
    """Delay between a decision and the order reaching the venue.

    Constant in V1. A jittered model would need a random source, and any
    random source has to be seeded and recorded before it may enter a run.
    """

    decision_to_submit_ms: int = 500
    note: str = "ASSUMPTION: constant decision-to-venue latency."

    def __post_init__(self) -> None:
        if self.decision_to_submit_ms < 0:
            raise ConfigError("latency cannot be negative")

    def submit_time(self, decision_time: int) -> int:
        return decision_time + self.decision_to_submit_ms

    def describe(self) -> dict[str, object]:
        return {
            "model": "LatencyModel",
            "decision_to_submit_ms": self.decision_to_submit_ms,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class CostModels:
    """The full cost configuration for one run."""

    fees: FeeModel = field(default_factory=FlatFeeModel)
    spread: SpreadModel = field(default_factory=FixedSpreadModel)
    slippage: SlippageModel = field(default_factory=FixedSlippageModel)
    latency: LatencyModel = field(default_factory=LatencyModel)

    def describe(self) -> dict[str, object]:
        return {
            "fees": self.fees.describe(),
            "spread": self.spread.describe(),
            "slippage": self.slippage.describe(),
            "latency": self.latency.describe(),
        }


def apply_adverse_price(
    reference_price: Decimal,
    side: OrderSide,
    *,
    half_spread_fraction: Decimal,
    slippage_fraction: Decimal,
) -> Decimal:
    """Move the reference price against the trader.

    A buy crosses the spread upward and then slips further up; a sell does
    the mirror. Both costs always push the wrong way -- a model that let
    slippage help would be a model that manufactures returns.
    """
    adjustment = half_spread_fraction + slippage_fraction
    if adjustment < 0:
        raise ConfigError("spread and slippage must not be negative in aggregate")
    if side is OrderSide.BUY:
        return mul(reference_price, Decimal(1) + adjustment)
    return mul(reference_price, Decimal(1) - adjustment)


def fee_amount(notional_or_quantity: Decimal, rate: Decimal) -> Decimal:
    return mul(notional_or_quantity, rate)


__all__: Sequence[str] = (
    "BPS",
    "CommissionAssetPolicy",
    "CostModels",
    "DEFAULT_FEE_SCHEDULE",
    "FeeModel",
    "FeeSchedule",
    "FixedSlippageModel",
    "FixedSpreadModel",
    "FlatFeeModel",
    "LatencyModel",
    "ParticipationSlippageModel",
    "SlippageModel",
    "SpreadModel",
    "apply_adverse_price",
    "fee_amount",
)
