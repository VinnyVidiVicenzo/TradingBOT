"""Normalized records shared by the data layer, the backtester and the registry.

Design rules enforced here:

* Every monetary or quantity field is a Decimal.
* Every timestamp is epoch milliseconds UTC.
* ``source_received_at`` is recorded separately from any exchange-supplied
  timestamp. The gap between the two is the local observation latency, and the
  backtester uses the *received* time -- not the exchange time -- to decide
  what a strategy was allowed to know.
* Assets are opaque strings and are never aliased. ``USD`` and ``USDT`` are
  different assets everywhere in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from .decimals import ZERO, dec, format_decimal

EXCHANGE_BINANCE_US = "BINANCE_US"


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketBar:
    """One candlestick / kline.

    ``open_time`` identifies the bar (Binance klines are uniquely identified by
    their open time). ``close_time`` is when the bar became *knowable*, and is
    what the backtester orders on.
    """

    exchange: str
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None
    trade_count: int | None = None
    source_received_at: int = 0
    # Modelled delay between the exchange closing a bar and a consumer being
    # able to act on it. Zero by default and set from configuration, never
    # inferred from when the backfill happened to run.
    feed_delay_ms: int = 0

    def __post_init__(self) -> None:
        if self.close_time < self.open_time:
            raise ValueError(f"{self.symbol} bar close_time precedes open_time")
        if self.high < self.low:
            raise ValueError(f"{self.symbol} bar high < low at {self.open_time}")
        if self.feed_delay_ms < 0:
            raise ValueError(f"{self.symbol} bar feed_delay_ms is negative")

    @property
    def observable_at(self) -> int:
        """Earliest simulated time a strategy may use this bar.

        The exchange close time plus any configured feed delay.

        ``source_received_at`` is deliberately *not* part of this. It records
        when the backfill ran, which says nothing about when the bar became
        knowable: history downloaded today was knowable at its close, and
        folding the download time in would push every historical bar's
        observability to today and empty the run window. To model a real
        consumer lagging the venue, set ``feed_delay_ms`` -- an explicit,
        recorded modelling choice rather than an artifact of scheduling.
        """
        return self.close_time + self.feed_delay_ms


@dataclass(frozen=True, slots=True)
class MarketQuote:
    """Top-of-book snapshot (best bid / best ask)."""

    exchange: str
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    exchange_event_time: int | None = None
    source_received_at: int = 0

    def __post_init__(self) -> None:
        if self.bid > self.ask:
            raise ValueError(f"{self.symbol} crossed quote: bid {self.bid} > ask {self.ask}")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def observable_at(self) -> int:
        if self.exchange_event_time is None:
            return self.source_received_at
        return max(self.exchange_event_time, self.source_received_at)


@dataclass(frozen=True, slots=True)
class Ticker24h:
    """24-hour rolling statistics for a symbol."""

    exchange: str
    symbol: str
    last_price: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    price_change: Decimal
    price_change_percent: Decimal
    weighted_avg_price: Decimal
    volume: Decimal
    quote_volume: Decimal
    open_time: int
    close_time: int
    trade_count: int | None = None
    source_received_at: int = 0


# --------------------------------------------------------------------------
# Exchange rules
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExchangeRuleSnapshot:
    """Trading rules for one symbol, as of one observation.

    Precision and minimum sizes are *never* hard-coded anywhere in this
    package; they are read from these snapshots, which in turn come from
    Binance.US exchangeInfo. ``raw_payload`` keeps the untouched exchange
    response so a rule change can be audited after the fact.
    """

    exchange: str
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    price_tick: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    min_quantity: Decimal | None
    max_quantity: Decimal | None
    quantity_step: Decimal | None
    market_min_quantity: Decimal | None
    market_max_quantity: Decimal | None
    market_quantity_step: Decimal | None
    min_notional: Decimal | None
    max_notional: Decimal | None
    min_notional_applies_to_market: bool
    max_notional_applies_to_market: bool
    # When >0, Binance checks a MARKET order notional against the average
    # price over this many minutes rather than the last price. Recorded so the
    # simplification the simulator makes is visible rather than invisible.
    notional_avg_price_mins: int | None
    base_asset_precision: int | None
    quote_asset_precision: int | None
    is_spot_trading_allowed: bool
    order_types: tuple[str, ...]
    max_num_orders: int | None
    max_num_algo_orders: int | None
    raw_payload: Mapping[str, Any] = field(default_factory=dict, repr=False)
    valid_from: int = 0
    source_received_at: int = 0

    @property
    def is_trading(self) -> bool:
        return self.status == "TRADING"


# --------------------------------------------------------------------------
# Portfolio inputs
# --------------------------------------------------------------------------


class CashSource(str, Enum):
    """Where a unit of spendable cash came from.

    The whole point of separating these is Benchmark 1 vs Benchmark 2. A
    staged-deployment strategy may only spend ``INITIAL`` cash; a DCA strategy
    funded by new deposits may only spend ``EXTERNAL_CONTRIBUTION`` cash. The
    ledger tracks the buckets so the two can never be quietly merged.
    """

    INITIAL = "INITIAL"
    EXTERNAL_CONTRIBUTION = "EXTERNAL_CONTRIBUTION"
    SALE_PROCEEDS = "SALE_PROCEEDS"


class ContributionType(str, Enum):
    ONE_OFF_DEPOSIT = "ONE_OFF_DEPOSIT"
    RECURRING_DEPOSIT = "RECURRING_DEPOSIT"


@dataclass(frozen=True, slots=True)
class PortfolioHolding:
    """A non-cash position supplied as a starting condition.

    A holding is NOT cash and is never spendable. ``cost_basis_per_unit`` is
    optional and is only populated when the user explicitly supplies it; it is
    never inferred from market data, because an inferred basis would silently
    become a fabricated fact in the experiment record.
    """

    asset: str
    quantity: Decimal
    cost_basis_per_unit: Decimal | None = None
    cost_basis_asset: str | None = None
    source: str = "user_supplied"
    as_of: int = 0

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError(f"holding quantity for {self.asset} is negative")
        if self.cost_basis_per_unit is not None and self.cost_basis_asset is None:
            raise ValueError(
                f"cost basis for {self.asset} supplied without a cost_basis_asset; "
                "a bare number is ambiguous between USD and USDT"
            )


@dataclass(frozen=True, slots=True)
class CashLedgerEntry:
    """One spendable-cash movement.

    ``asset`` is a literal asset code. USD and USDT are distinct rows and are
    never netted together.
    """

    asset: str
    amount: Decimal
    source: CashSource
    as_of: int
    reason: str = ""

    def __post_init__(self) -> None:
        if self.asset != self.asset.upper():
            raise ValueError("cash ledger asset codes must be upper case")


@dataclass(frozen=True, slots=True)
class ExternalContribution:
    """New money arriving from outside the portfolio.

    Distinct from initial available cash: initial cash is money you already
    have and are choosing to deploy gradually, whereas a contribution does not
    exist -- and cannot be spent, reserved, or counted -- before
    ``availability_time``.
    """

    amount: Decimal
    asset: str
    availability_time: int
    contribution_type: ContributionType = ContributionType.ONE_OFF_DEPOSIT
    label: str = ""

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("external contribution amount must be positive")
        if self.asset != self.asset.upper():
            raise ValueError("contribution asset codes must be upper case")


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------


def decimal_fields_to_str(payload: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of a mapping, with Decimals in canonical string form."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, Decimal):
            out[key] = format_decimal(value)
        elif isinstance(value, Enum):
            out[key] = value.value
        elif isinstance(value, Mapping):
            out[key] = decimal_fields_to_str(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [
                decimal_fields_to_str(item)
                if isinstance(item, Mapping)
                else format_decimal(item)
                if isinstance(item, Decimal)
                else item.value
                if isinstance(item, Enum)
                else item
                for item in value
            ]
        else:
            out[key] = value
    return out


def holding_from_mapping(payload: Mapping[str, Any]) -> PortfolioHolding:
    basis = payload.get("cost_basis_per_unit")
    return PortfolioHolding(
        asset=str(payload["asset"]).upper(),
        quantity=dec(payload["quantity"]),
        cost_basis_per_unit=None if basis is None else dec(basis),
        cost_basis_asset=(
            str(payload["cost_basis_asset"]).upper()
            if payload.get("cost_basis_asset")
            else None
        ),
        source=str(payload.get("source", "user_supplied")),
        as_of=int(payload.get("as_of", 0)),
    )


def contribution_from_mapping(payload: Mapping[str, Any]) -> ExternalContribution:
    from .timeutil import parse_time

    return ExternalContribution(
        amount=dec(payload["amount"]),
        asset=str(payload["asset"]).upper(),
        availability_time=parse_time(payload["availability_time"]),
        contribution_type=ContributionType(
            str(payload.get("contribution_type", "ONE_OFF_DEPOSIT"))
        ),
        label=str(payload.get("label", "")),
    )


__all__ = [
    "EXCHANGE_BINANCE_US",
    "CashLedgerEntry",
    "CashSource",
    "ContributionType",
    "ExchangeRuleSnapshot",
    "ExternalContribution",
    "MarketBar",
    "MarketQuote",
    "PortfolioHolding",
    "Ticker24h",
    "ZERO",
    "contribution_from_mapping",
    "decimal_fields_to_str",
    "holding_from_mapping",
]
