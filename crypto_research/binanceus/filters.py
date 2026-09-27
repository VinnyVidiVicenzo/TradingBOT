"""Enforcement of Binance.US symbol filters.

The backtester routes every hypothetical order through :class:`SymbolRules`.
If a research result depends on buying 0.0000004 BTC, or on a 12-cent order
that the exchange would have refused, that result is wrong -- so rounding and
rejection happen in the simulator exactly as they would at the venue.

All limits come from an :class:`~crypto_research.models.ExchangeRuleSnapshot`,
which in turn comes from exchangeInfo. Nothing here is hard-coded per symbol.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping

from ..decimals import ZERO, floor_to_step, mul, step_to_places
from ..errors import FilterParseError
from ..models import ExchangeRuleSnapshot
from .constants import STATUS_TRADING


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class RuleViolation(str, Enum):
    """Why the exchange would have refused an order."""

    SYMBOL_UNKNOWN = "SYMBOL_UNKNOWN"
    SYMBOL_NOT_TRADING = "SYMBOL_NOT_TRADING"
    SPOT_NOT_ALLOWED = "SPOT_NOT_ALLOWED"
    ORDER_TYPE_NOT_SUPPORTED = "ORDER_TYPE_NOT_SUPPORTED"
    PRICE_BELOW_MIN = "PRICE_BELOW_MIN"
    PRICE_ABOVE_MAX = "PRICE_ABOVE_MAX"
    PRICE_NOT_ON_TICK = "PRICE_NOT_ON_TICK"
    QUANTITY_NOT_POSITIVE = "QUANTITY_NOT_POSITIVE"
    QUANTITY_BELOW_MIN = "QUANTITY_BELOW_MIN"
    QUANTITY_ABOVE_MAX = "QUANTITY_ABOVE_MAX"
    QUANTITY_NOT_ON_STEP = "QUANTITY_NOT_ON_STEP"
    MARKET_QUANTITY_BELOW_MIN = "MARKET_QUANTITY_BELOW_MIN"
    MARKET_QUANTITY_ABOVE_MAX = "MARKET_QUANTITY_ABOVE_MAX"
    MARKET_QUANTITY_NOT_ON_STEP = "MARKET_QUANTITY_NOT_ON_STEP"
    NOTIONAL_BELOW_MIN = "NOTIONAL_BELOW_MIN"
    NOTIONAL_ABOVE_MAX = "NOTIONAL_ABOVE_MAX"


@dataclass(frozen=True, slots=True)
class RuleCheckResult:
    ok: bool
    violations: tuple[RuleViolation, ...] = ()
    notional: Decimal = ZERO

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok


@dataclass(frozen=True, slots=True)
class SymbolRules:
    """Filter enforcement for one symbol, backed by one exchangeInfo snapshot."""

    snapshot: ExchangeRuleSnapshot

    # -- identity ----------------------------------------------------------

    @property
    def symbol(self) -> str:
        return self.snapshot.symbol

    @property
    def base_asset(self) -> str:
        return self.snapshot.base_asset

    @property
    def quote_asset(self) -> str:
        return self.snapshot.quote_asset

    @property
    def price_places(self) -> int:
        return step_to_places(self.snapshot.price_tick or ZERO)

    @property
    def quantity_places(self) -> int:
        return step_to_places(self.snapshot.quantity_step or ZERO)

    # -- rounding ----------------------------------------------------------

    def round_price(self, price: Decimal) -> Decimal:
        """Round a price DOWN onto the PRICE_FILTER tick grid.

        Rounding down rather than to nearest is deliberate: for a buy limit it
        is the conservative direction (you bid less, so you are less likely to
        fill), and a backtest should never flatter itself by rounding into a
        better fill than the venue would have allowed.
        """
        tick = self.snapshot.price_tick
        if tick is None:
            return price
        return floor_to_step(price, tick)

    def round_quantity(self, quantity: Decimal, order_type: OrderType) -> Decimal:
        """Round a quantity DOWN onto the applicable LOT_SIZE step grid.

        MARKET orders must satisfy LOT_SIZE *and* MARKET_LOT_SIZE. Binance.US
        commonly returns a MARKET_LOT_SIZE stepSize of 0, which means
        unconstrained rather than "round everything to zero"; floor_to_step
        handles that case.
        """
        result = quantity
        step = self.snapshot.quantity_step
        if step is not None:
            result = floor_to_step(result, step)
        if order_type is OrderType.MARKET:
            market_step = self.snapshot.market_quantity_step
            if market_step is not None:
                result = floor_to_step(result, market_step)
        return result

    def max_affordable_quantity(
        self, budget_in_quote: Decimal, price: Decimal, order_type: OrderType
    ) -> Decimal:
        """Largest step-valid quantity buyable with ``budget_in_quote``."""
        if price <= 0:
            raise ValueError(f"{self.symbol}: price must be positive")
        return self.round_quantity(budget_in_quote / price, order_type)

    # -- validation --------------------------------------------------------

    def check(
        self,
        *,
        price: Decimal,
        quantity: Decimal,
        order_type: OrderType,
    ) -> RuleCheckResult:
        """Check a fully-rounded hypothetical order against every known filter."""
        snap = self.snapshot
        violations: list[RuleViolation] = []

        if snap.status != STATUS_TRADING:
            violations.append(RuleViolation.SYMBOL_NOT_TRADING)
        if not snap.is_spot_trading_allowed:
            violations.append(RuleViolation.SPOT_NOT_ALLOWED)
        if snap.order_types and order_type.value not in snap.order_types:
            violations.append(RuleViolation.ORDER_TYPE_NOT_SUPPORTED)

        # PRICE_FILTER constrains the price *field* of an order. A MARKET
        # order has no price field, so the venue does not apply it; only the
        # notional check below uses the fill price in that case.
        if price <= 0:
            violations.append(RuleViolation.PRICE_BELOW_MIN)
        elif order_type is OrderType.LIMIT:
            if snap.min_price is not None and snap.min_price > 0 and price < snap.min_price:
                violations.append(RuleViolation.PRICE_BELOW_MIN)
            if snap.max_price is not None and snap.max_price > 0 and price > snap.max_price:
                violations.append(RuleViolation.PRICE_ABOVE_MAX)
            if snap.price_tick and _off_grid(price, snap.price_tick):
                violations.append(RuleViolation.PRICE_NOT_ON_TICK)

        if quantity <= 0:
            violations.append(RuleViolation.QUANTITY_NOT_POSITIVE)
        else:
            if snap.min_quantity is not None and quantity < snap.min_quantity:
                violations.append(RuleViolation.QUANTITY_BELOW_MIN)
            if (
                snap.max_quantity is not None
                and snap.max_quantity > 0
                and quantity > snap.max_quantity
            ):
                violations.append(RuleViolation.QUANTITY_ABOVE_MAX)
            if snap.quantity_step and _off_grid(quantity, snap.quantity_step):
                violations.append(RuleViolation.QUANTITY_NOT_ON_STEP)

            if order_type is OrderType.MARKET:
                if (
                    snap.market_min_quantity is not None
                    and snap.market_min_quantity > 0
                    and quantity < snap.market_min_quantity
                ):
                    violations.append(RuleViolation.MARKET_QUANTITY_BELOW_MIN)
                if (
                    snap.market_max_quantity is not None
                    and snap.market_max_quantity > 0
                    and quantity > snap.market_max_quantity
                ):
                    violations.append(RuleViolation.MARKET_QUANTITY_ABOVE_MAX)
                if snap.market_quantity_step and _off_grid(
                    quantity, snap.market_quantity_step
                ):
                    violations.append(RuleViolation.MARKET_QUANTITY_NOT_ON_STEP)

        notional = mul(price, quantity) if price > 0 and quantity > 0 else ZERO
        is_market = order_type is OrderType.MARKET
        if snap.min_notional is not None and (
            not is_market or snap.min_notional_applies_to_market
        ):
            if notional < snap.min_notional:
                violations.append(RuleViolation.NOTIONAL_BELOW_MIN)
        if snap.max_notional is not None and snap.max_notional > 0 and (
            not is_market or snap.max_notional_applies_to_market
        ):
            if notional > snap.max_notional:
                violations.append(RuleViolation.NOTIONAL_ABOVE_MAX)

        return RuleCheckResult(
            ok=not violations, violations=tuple(violations), notional=notional
        )


class SymbolRuleBook:
    """All symbol rules known at one point in time.

    A run pins one rule book. When rules change mid-backtest the honest answer
    is to re-run against the newer snapshot and compare, rather than to
    silently interpolate -- so this class does not attempt time-travel.
    """

    def __init__(self, snapshots: Iterable[ExchangeRuleSnapshot]) -> None:
        self._by_symbol: dict[str, SymbolRules] = {}
        for snapshot in snapshots:
            self._by_symbol[snapshot.symbol] = SymbolRules(snapshot)

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._by_symbol

    def __len__(self) -> int:
        return len(self._by_symbol)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_symbol))

    def get(self, symbol: str) -> SymbolRules:
        try:
            return self._by_symbol[symbol]
        except KeyError as exc:
            raise FilterParseError(
                f"no exchangeInfo snapshot for {symbol!r}; download it before "
                "running a backtest that trades it"
            ) from exc

    def maybe(self, symbol: str) -> SymbolRules | None:
        return self._by_symbol.get(symbol)

    def tradable(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                symbol
                for symbol, rules in self._by_symbol.items()
                if rules.snapshot.is_trading and rules.snapshot.is_spot_trading_allowed
            )
        )

    def as_mapping(self) -> Mapping[str, SymbolRules]:
        return dict(self._by_symbol)


def _off_grid(value: Decimal, step: Decimal) -> bool:
    """True when ``value`` is not an exact multiple of ``step``."""
    if step <= 0:
        return False
    return (value % step) != 0
