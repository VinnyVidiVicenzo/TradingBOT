"""Execution simulation.

Turns an :class:`~crypto_research.backtest.orders.OrderIntent` into a
:class:`~crypto_research.backtest.orders.SimulatedOrder`, applying, in order:

1. duplicate client-order-ID rejection,
2. the sell gate (V1 never sells),
3. reference-price availability and staleness,
4. spread and slippage, always against the trader,
5. exchange lot/tick/notional filters from live exchangeInfo,
6. the cash-bucket balance check,
7. the fill model,
8. commission.

Anything that fails is recorded as a rejected order with a reason, never
dropped. The number of rejections a plan generates is itself a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, Sequence

from ..binanceus.filters import OrderType, SymbolRuleBook
from ..decimals import ZERO, dec, div, format_decimal, mul
from ..errors import ConfigError
from ..models import CashSource
from .costs import CommissionAssetPolicy, CostModels, FlatFeeModel, apply_adverse_price
from .orders import (
    ClientOrderIdRegistry,
    Fill,
    OrderIntent,
    OrderSide,
    OrderStatus,
    RejectionReason,
    SimulatedOrder,
)
from .portfolio import InsufficientCash, Portfolio


class ExecutionMarketView(Protocol):
    """What the execution simulator is allowed to know at submit time."""

    def reference_price(self, symbol: str, at: int) -> tuple[Decimal | None, int]: ...

    def observed_half_spread_fraction(self, symbol: str, at: int) -> Decimal | None: ...

    def bar_quote_volume(self, symbol: str, at: int) -> Decimal | None: ...


# --------------------------------------------------------------------------
# Fill models
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FillSlice:
    """A portion of an order that executes, at a price relative to the effective price."""

    fraction: Decimal
    price_multiplier: Decimal = Decimal(1)
    is_maker: bool = False

    def __post_init__(self) -> None:
        if not (0 < self.fraction <= 1):
            raise ConfigError(f"fill fraction must be in (0, 1], got {self.fraction}")
        if self.price_multiplier <= 0:
            raise ConfigError("fill price multiplier must be positive")


@dataclass(frozen=True, slots=True)
class FillRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    base_quantity: Decimal
    effective_price: Decimal
    bar_quote_volume: Decimal | None
    submit_time: int


class FillModel(Protocol):
    def plan(self, request: FillRequest) -> Sequence[FillSlice]: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(slots=True)
class FullFillModel:
    """Fill the whole order at the effective price.

    The conservative default for V1. At the order sizes involved -- tens of
    dollars against the most liquid Binance.US pairs -- assuming a complete
    taker fill is realistic, and the spread and slippage models already carry
    the cost of crossing.
    """

    is_maker: bool = False

    def plan(self, request: FillRequest) -> Sequence[FillSlice]:
        return (FillSlice(fraction=Decimal(1), is_maker=self.is_maker),)

    def describe(self) -> dict[str, object]:
        return {"model": "FullFillModel", "is_maker": self.is_maker}


@dataclass(slots=True)
class FixedFractionFillModel:
    """Fill fixed fractions, each at a configurable price multiple.

    Fractions summing to less than 1 leave the order partially filled, which
    is the point: it exercises the partial-fill path without needing
    order-book replay.
    """

    fractions: Sequence[Decimal] = (Decimal(1),)
    price_multipliers: Sequence[Decimal] = ()
    is_maker: bool = False

    def plan(self, request: FillRequest) -> Sequence[FillSlice]:
        multipliers = list(self.price_multipliers) or [Decimal(1)] * len(self.fractions)
        if len(multipliers) != len(self.fractions):
            raise ConfigError("price_multipliers must match fractions in length")
        return tuple(
            FillSlice(fraction=f, price_multiplier=m, is_maker=self.is_maker)
            for f, m in zip(self.fractions, multipliers)
        )

    def describe(self) -> dict[str, object]:
        return {
            "model": "FixedFractionFillModel",
            "fractions": [format_decimal(f) for f in self.fractions],
            "price_multipliers": [format_decimal(m) for m in self.price_multipliers],
            "is_maker": self.is_maker,
        }


@dataclass(slots=True)
class VolumeCapFillModel:
    """Cap the fill at a share of the bar quote volume.

    Bars with no volume data fall back to a full fill; the alternative would
    be to invent a liquidity constraint from missing data.
    """

    max_participation: Decimal = dec("0.01")
    is_maker: bool = False

    def plan(self, request: FillRequest) -> Sequence[FillSlice]:
        volume = request.bar_quote_volume
        notional = mul(request.base_quantity, request.effective_price)
        if not volume or volume <= 0 or notional <= 0:
            return (FillSlice(fraction=Decimal(1), is_maker=self.is_maker),)
        cap = mul(volume, self.max_participation)
        if notional <= cap:
            return (FillSlice(fraction=Decimal(1), is_maker=self.is_maker),)
        return (FillSlice(fraction=div(cap, notional), is_maker=self.is_maker),)

    def describe(self) -> dict[str, object]:
        return {
            "model": "VolumeCapFillModel",
            "max_participation": format_decimal(self.max_participation),
            "is_maker": self.is_maker,
        }


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Execution-side knobs that are not costs."""

    # A reference price older than this at submit time is refused. Guards the
    # backtester against trading off a price that a live system would have
    # considered stale, and makes gappy history visible as rejections.
    max_reference_age_ms: int = 90_000
    # V1 never sells. Flipping this is a deliberate, recorded act.
    allow_sell: bool = False
    # Default funding bucket when an intent does not name one.
    default_funding_source: CashSource | None = None

    def describe(self) -> dict[str, object]:
        return {
            "max_reference_age_ms": self.max_reference_age_ms,
            "allow_sell": self.allow_sell,
            "default_funding_source": (
                self.default_funding_source.value if self.default_funding_source else None
            ),
        }


@dataclass(slots=True)
class ExecutionSimulator:
    rule_book: SymbolRuleBook
    costs: CostModels
    portfolio: Portfolio
    fill_model: FillModel = field(default_factory=FullFillModel)
    config: ExecutionConfig = field(default_factory=ExecutionConfig)
    order_ids: ClientOrderIdRegistry = field(default_factory=ClientOrderIdRegistry)

    def execute(
        self,
        intent: OrderIntent,
        *,
        decision_time: int,
        submit_time: int,
        market: ExecutionMarketView,
    ) -> SimulatedOrder:
        order = SimulatedOrder(
            intent=intent,
            decision_time=decision_time,
            submit_time=submit_time,
            status=OrderStatus.REJECTED,
        )

        if not self.order_ids.register(intent.client_order_id):
            return _reject(order, RejectionReason.DUPLICATE_CLIENT_ORDER_ID,
                           f"client order id {intent.client_order_id} already used")

        if intent.side is OrderSide.SELL and not self.config.allow_sell:
            return _reject(order, RejectionReason.SELL_NOT_PERMITTED,
                           "V1 does not sell; enable allow_sell deliberately to model exits")

        rules = self.rule_book.maybe(intent.symbol)
        if rules is None:
            return _reject(order, RejectionReason.UNKNOWN_SYMBOL,
                           f"{intent.symbol} has no exchangeInfo snapshot in this run")

        reference_price, observed_at = market.reference_price(intent.symbol, submit_time)
        if reference_price is None or reference_price <= 0:
            return _reject(order, RejectionReason.NO_REFERENCE_PRICE,
                           f"no visible price for {intent.symbol} at {submit_time}")
        order.reference_price = reference_price

        age = submit_time - observed_at
        if age > self.config.max_reference_age_ms:
            return _reject(
                order,
                RejectionReason.STALE_QUOTE,
                f"reference price for {intent.symbol} is {age}ms old, limit is "
                f"{self.config.max_reference_age_ms}ms",
            )

        half_spread = self.costs.spread.half_spread_fraction(
            intent.symbol,
            observed=market.observed_half_spread_fraction(intent.symbol, submit_time),
        )
        bar_volume = market.bar_quote_volume(intent.symbol, submit_time)
        provisional_notional = intent.quote_quantity or mul(
            intent.base_quantity or ZERO, reference_price
        )
        slippage = self.costs.slippage.slippage_fraction(
            intent, notional=provisional_notional, bar_quote_volume=bar_volume
        )
        effective_price = apply_adverse_price(
            reference_price,
            intent.side,
            half_spread_fraction=half_spread,
            slippage_fraction=slippage,
        )

        if intent.order_type is OrderType.LIMIT:
            limit = rules.round_price(intent.limit_price or ZERO)
            marketable = (
                effective_price <= limit if intent.side is OrderSide.BUY
                else effective_price >= limit
            )
            if not marketable:
                order.effective_price = effective_price
                return _reject(order, RejectionReason.LIMIT_NOT_MARKETABLE,
                               f"limit {format_decimal(limit)} not reachable at "
                               f"{format_decimal(effective_price)}")
            effective_price = limit

        order.effective_price = effective_price

        fee_policy = getattr(
            self.costs.fees, "commission_asset_policy", CommissionAssetPolicy.RECEIVED_ASSET
        )
        fee_rate = self.costs.fees.rate_for(intent, is_maker=False)

        quantity = self._target_quantity(
            intent, rules, effective_price, fee_rate, fee_policy
        )
        order.requested_base_quantity = quantity
        if quantity <= 0:
            return _reject(
                order,
                RejectionReason.ZERO_QUANTITY_AFTER_ROUNDING,
                f"budget rounds to zero at step {format_decimal(rules.snapshot.quantity_step or ZERO)}",
            )

        check = rules.check(
            price=effective_price, quantity=quantity, order_type=intent.order_type
        )
        if not check.ok:
            order.filter_violations = check.violations
            return _reject(
                order,
                RejectionReason.EXCHANGE_FILTER,
                "rejected by " + ", ".join(v.value for v in check.violations),
            )

        # Balance / holding check on the full requested size, before any fill.
        funding = intent.funding_source or self.config.default_funding_source
        if intent.side is OrderSide.BUY:
            required = mul(effective_price, quantity)
            if fee_policy is CommissionAssetPolicy.QUOTE_ASSET:
                required += mul(required, fee_rate)
            available = self.portfolio.cash.available(rules.quote_asset, funding)
            if available < required:
                return _reject(
                    order,
                    RejectionReason.INSUFFICIENT_BALANCE,
                    f"needs {format_decimal(required)} {rules.quote_asset} from "
                    f"{funding.value if funding else 'any bucket'}, has "
                    f"{format_decimal(available)}",
                )
        else:
            if self.portfolio.quantity(rules.base_asset) < quantity:
                return _reject(
                    order,
                    RejectionReason.INSUFFICIENT_HOLDING,
                    f"needs {format_decimal(quantity)} {rules.base_asset}, has "
                    f"{format_decimal(self.portfolio.quantity(rules.base_asset))}",
                )

        slices = self.fill_model.plan(
            FillRequest(
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                base_quantity=quantity,
                effective_price=effective_price,
                bar_quote_volume=bar_volume,
                submit_time=submit_time,
            )
        )
        self._apply_fills(
            order,
            slices=slices,
            rules=rules,
            quantity=quantity,
            effective_price=effective_price,
            reference_price=reference_price,
            half_spread=half_spread,
            funding=funding,
            fee_policy=fee_policy,
            submit_time=submit_time,
        )
        return order

    # -- internals ---------------------------------------------------------

    def _target_quantity(
        self,
        intent: OrderIntent,
        rules,
        effective_price: Decimal,
        fee_rate: Decimal,
        fee_policy: CommissionAssetPolicy,
    ) -> Decimal:
        if intent.base_quantity is not None:
            return rules.round_quantity(intent.base_quantity, intent.order_type)
        budget = intent.quote_quantity or ZERO
        if fee_policy is CommissionAssetPolicy.QUOTE_ASSET:
            # The commission comes out of the same pocket as the purchase, so
            # the affordable notional is the budget net of it.
            budget = div(budget, Decimal(1) + fee_rate)
        return rules.max_affordable_quantity(budget, effective_price, intent.order_type)

    def _apply_fills(
        self,
        order: SimulatedOrder,
        *,
        slices: Sequence[FillSlice],
        rules,
        quantity: Decimal,
        effective_price: Decimal,
        reference_price: Decimal,
        half_spread: Decimal,
        funding: CashSource | None,
        fee_policy: CommissionAssetPolicy,
        submit_time: int,
    ) -> None:
        remaining = quantity
        sequence = 0
        for slice_ in slices:
            if remaining <= 0:
                break
            raw = mul(quantity, slice_.fraction)
            slice_qty = rules.round_quantity(min(raw, remaining), order.intent.order_type)
            if slice_qty <= 0:
                continue
            price = mul(effective_price, slice_.price_multiplier)
            notional = mul(price, slice_qty)
            fee_rate = self.costs.fees.rate_for(order.intent, is_maker=slice_.is_maker)

            try:
                self._settle(
                    order,
                    rules=rules,
                    price=price,
                    slice_qty=slice_qty,
                    notional=notional,
                    fee_rate=fee_rate,
                    fee_policy=fee_policy,
                    funding=funding,
                    is_maker=slice_.is_maker,
                    sequence=sequence,
                    submit_time=submit_time,
                )
            except InsufficientCash as exc:
                # The pre-check passed on the whole order, so this can only
                # happen if a fill model asked for more than was reserved.
                order.note = f"fill aborted: {exc}"
                break

            sequence += 1
            remaining -= slice_qty

        if order.filled_base_quantity <= 0:
            _reject(
                order,
                RejectionReason.ZERO_QUANTITY_AFTER_ROUNDING,
                "fill model produced no executable quantity",
            )
            return

        # Costs attributable to crossing, reported separately from the fee.
        order.spread_cost_quote = mul(
            mul(reference_price, half_spread), order.filled_base_quantity
        )
        gross = mul(reference_price, order.filled_base_quantity)
        order.slippage_cost_quote = (
            abs(order.filled_quote_quantity - gross) - order.spread_cost_quote
        )
        if order.slippage_cost_quote < 0:
            order.slippage_cost_quote = ZERO

        order.status = (
            OrderStatus.FILLED
            if order.filled_base_quantity >= quantity
            else OrderStatus.PARTIALLY_FILLED
        )

    def _settle(
        self,
        order: SimulatedOrder,
        *,
        rules,
        price: Decimal,
        slice_qty: Decimal,
        notional: Decimal,
        fee_rate: Decimal,
        fee_policy: CommissionAssetPolicy,
        funding: CashSource | None,
        is_maker: bool,
        sequence: int,
        submit_time: int,
    ) -> None:
        base_asset = rules.base_asset
        quote_asset = rules.quote_asset

        if order.intent.side is OrderSide.BUY:
            if fee_policy is CommissionAssetPolicy.RECEIVED_ASSET:
                fee_amount = mul(slice_qty, fee_rate)
                fee_asset = base_asset
                cash_out = notional
                received = slice_qty - fee_amount
            else:
                fee_amount = mul(notional, fee_rate)
                fee_asset = quote_asset
                cash_out = notional + fee_amount
                received = slice_qty

            self.portfolio.cash.withdraw(
                quote_asset,
                cash_out,
                funding,
                at=submit_time,
                reason=f"buy {order.intent.symbol} ({order.intent.client_order_id})",
            )
            self.portfolio.credit_asset(
                base_asset, received, cost=cash_out, quote_asset=quote_asset
            )
        else:
            if fee_policy is CommissionAssetPolicy.RECEIVED_ASSET:
                fee_amount = mul(notional, fee_rate)
                fee_asset = quote_asset
                cash_in = notional - fee_amount
            else:
                fee_amount = mul(notional, fee_rate)
                fee_asset = quote_asset
                cash_in = notional - fee_amount

            position = self.portfolio.positions[base_asset]
            position.quantity -= slice_qty
            self.portfolio.cash.deposit(
                quote_asset,
                cash_in,
                CashSource.SALE_PROCEEDS,
                at=submit_time,
                reason=f"sell {order.intent.symbol} ({order.intent.client_order_id})",
            )
            cash_out = -cash_in

        order.filled_base_quantity += slice_qty
        order.filled_quote_quantity += notional
        order.fees_by_asset[fee_asset] = order.fees_by_asset.get(fee_asset, ZERO) + fee_amount
        if fee_asset == quote_asset:
            order.fee_in_quote += fee_amount
        else:
            order.fee_in_quote += mul(fee_amount, price)
        order.fills.append(
            Fill(
                sequence=sequence,
                time=submit_time,
                price=price,
                base_quantity=slice_qty,
                quote_quantity=notional,
                fee_amount=fee_amount,
                fee_asset=fee_asset,
                is_maker=is_maker,
            )
        )


def _reject(
    order: SimulatedOrder, reason: RejectionReason, note: str
) -> SimulatedOrder:
    order.status = OrderStatus.REJECTED
    order.rejection_reason = reason
    order.note = note
    return order


def default_cost_models() -> CostModels:
    return CostModels(fees=FlatFeeModel())
