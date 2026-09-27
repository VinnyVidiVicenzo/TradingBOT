"""Order execution simulation."""

from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_research.backtest.costs import (
    CommissionAssetPolicy,
    CostModels,
    FeeSchedule,
    FixedSlippageModel,
    FixedSpreadModel,
    FlatFeeModel,
    LatencyModel,
)
from crypto_research.backtest.execution import (
    ExecutionConfig,
    ExecutionSimulator,
    FixedFractionFillModel,
    FullFillModel,
    VolumeCapFillModel,
)
from crypto_research.backtest.orders import (
    OrderIntent,
    OrderSide,
    OrderStatus,
    RejectionReason,
)
from crypto_research.backtest.portfolio import Portfolio
from crypto_research.binanceus.filters import OrderType, RuleViolation
from crypto_research.decimals import dec
from crypto_research.models import CashSource, PortfolioHolding

from .helpers import default_rule_book


class StubMarket:
    """A market view with exactly one price, at a known observation time."""

    def __init__(
        self,
        price: str = "40000",
        *,
        observed_at: int = 0,
        half_spread: str | None = None,
        quote_volume: str | None = "1000000",
    ) -> None:
        self._price = dec(price)
        self._observed_at = observed_at
        self._half_spread = None if half_spread is None else dec(half_spread)
        self._quote_volume = None if quote_volume is None else dec(quote_volume)

    def reference_price(self, symbol: str, at: int) -> tuple[Decimal | None, int]:
        return (self._price, self._observed_at)

    def observed_half_spread_fraction(self, symbol: str, at: int) -> Decimal | None:
        return self._half_spread

    def bar_quote_volume(self, symbol: str, at: int) -> Decimal | None:
        return self._quote_volume


def zero_cost_models(**overrides) -> CostModels:
    """No fees, no spread, no slippage: isolates whatever a test is measuring."""
    defaults = dict(
        fees=FlatFeeModel(
            schedule=FeeSchedule(maker_rate=dec("0"), taker_rate=dec("0"), source="TEST")
        ),
        spread=FixedSpreadModel(default_bps=dec("0"), prefer_observed=False),
        slippage=FixedSlippageModel(bps=dec("0")),
        latency=LatencyModel(decision_to_submit_ms=0),
    )
    defaults.update(overrides)
    return CostModels(**defaults)


def make_simulator(
    *,
    costs: CostModels | None = None,
    usd: str = "1000",
    fill_model=None,
    config: ExecutionConfig | None = None,
    holdings=(),
) -> tuple[ExecutionSimulator, Portfolio]:
    portfolio = Portfolio(starting_holdings=holdings, valuation_asset="USD")
    if dec(usd) > 0:
        portfolio.cash.deposit("USD", dec(usd), CashSource.INITIAL, at=0)
    simulator = ExecutionSimulator(
        rule_book=default_rule_book(),
        costs=costs or zero_cost_models(),
        portfolio=portfolio,
        fill_model=fill_model or FullFillModel(),
        config=config or ExecutionConfig(max_reference_age_ms=90_000),
    )
    return simulator, portfolio


def buy(client_order_id: str = "o1", quote: str = "100", **kwargs) -> OrderIntent:
    params = dict(
        symbol="BTCUSD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        client_order_id=client_order_id,
        quote_quantity=dec(quote),
        funding_source=CashSource.INITIAL,
    )
    params.update(kwargs)
    return OrderIntent(**params)


class HappyPathTests(unittest.TestCase):
    def test_a_simple_buy_fills_and_moves_cash_into_the_position(self) -> None:
        simulator, portfolio = make_simulator()
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.effective_price, dec("40000"))
        self.assertEqual(order.filled_base_quantity, dec("0.0025"))
        self.assertEqual(order.filled_quote_quantity, dec("100"))
        self.assertEqual(portfolio.cash.available("USD"), dec("900"))
        self.assertEqual(portfolio.quantity("BTC"), dec("0.0025"))

    def test_budget_is_rounded_down_to_the_lot_step(self) -> None:
        simulator, portfolio = make_simulator()
        order = simulator.execute(
            buy(quote="99.99"), decision_time=0, submit_time=0, market=StubMarket()
        )
        # 99.99/40000 = 0.00249975 -> floor to 0.00001 step -> 0.00249
        self.assertEqual(order.filled_base_quantity, dec("0.00249"))
        self.assertLessEqual(order.filled_quote_quantity, dec("99.99"))


class RejectionTests(unittest.TestCase):
    def test_duplicate_client_order_id_is_rejected(self) -> None:
        simulator, _ = make_simulator()
        first = simulator.execute(
            buy("same-id"), decision_time=0, submit_time=0, market=StubMarket()
        )
        second = simulator.execute(
            buy("same-id"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(first.status, OrderStatus.FILLED)
        self.assertEqual(second.status, OrderStatus.REJECTED)
        self.assertEqual(
            second.rejection_reason, RejectionReason.DUPLICATE_CLIENT_ORDER_ID
        )

    def test_stale_reference_price_is_rejected(self) -> None:
        simulator, _ = make_simulator(
            config=ExecutionConfig(max_reference_age_ms=1_000)
        )
        order = simulator.execute(
            buy(),
            decision_time=0,
            submit_time=5_000,
            market=StubMarket(observed_at=0),
        )
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, RejectionReason.STALE_QUOTE)
        self.assertIn("5000ms old", order.note)

    def test_fresh_reference_price_within_the_window_is_accepted(self) -> None:
        simulator, _ = make_simulator(
            config=ExecutionConfig(max_reference_age_ms=10_000)
        )
        order = simulator.execute(
            buy(), decision_time=0, submit_time=5_000, market=StubMarket(observed_at=0)
        )
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_insufficient_balance_is_rejected(self) -> None:
        simulator, portfolio = make_simulator(usd="50")
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.rejection_reason, RejectionReason.INSUFFICIENT_BALANCE)
        self.assertEqual(portfolio.cash.available("USD"), dec("50"), "no cash moved")

    def test_cash_in_the_wrong_bucket_does_not_count(self) -> None:
        # There is plenty of USD, but it is contributed money and the order
        # asked to be funded from the initial pool.
        simulator, portfolio = make_simulator(usd="0")
        portfolio.cash.deposit(
            "USD", dec("1000"), CashSource.EXTERNAL_CONTRIBUTION, at=0
        )
        order = simulator.execute(
            buy(quote="100", funding_source=CashSource.INITIAL),
            decision_time=0, submit_time=0, market=StubMarket(),
        )
        self.assertEqual(order.rejection_reason, RejectionReason.INSUFFICIENT_BALANCE)
        self.assertEqual(portfolio.cash.available("USD"), dec("1000"))

    def test_below_minimum_notional_is_rejected(self) -> None:
        simulator, portfolio = make_simulator()
        order = simulator.execute(
            buy(quote="0.50"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, RejectionReason.EXCHANGE_FILTER)
        self.assertIn(RuleViolation.NOTIONAL_BELOW_MIN, order.filter_violations)
        self.assertEqual(portfolio.cash.available("USD"), dec("1000"))

    def test_budget_too_small_to_buy_one_step_is_rejected(self) -> None:
        simulator, _ = make_simulator()
        order = simulator.execute(
            buy(quote="0.0001"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(
            order.rejection_reason, RejectionReason.ZERO_QUANTITY_AFTER_ROUNDING
        )

    def test_unknown_symbol_is_rejected(self) -> None:
        simulator, _ = make_simulator()
        order = simulator.execute(
            buy(symbol="DOGEUSD"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.rejection_reason, RejectionReason.UNKNOWN_SYMBOL)

    def test_missing_reference_price_is_rejected(self) -> None:
        class Blind(StubMarket):
            def reference_price(self, symbol, at):
                return (None, 0)

        simulator, _ = make_simulator()
        order = simulator.execute(
            buy(), decision_time=0, submit_time=0, market=Blind()
        )
        self.assertEqual(order.rejection_reason, RejectionReason.NO_REFERENCE_PRICE)

    def test_v1_refuses_to_sell(self) -> None:
        simulator, _ = make_simulator(
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))]
        )
        order = simulator.execute(
            OrderIntent(
                symbol="BTCUSD",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                client_order_id="sell-1",
                base_quantity=dec("0.001"),
            ),
            decision_time=0, submit_time=0, market=StubMarket(),
        )
        self.assertEqual(order.rejection_reason, RejectionReason.SELL_NOT_PERMITTED)

    def test_unmarketable_limit_order_does_not_fill(self) -> None:
        simulator, _ = make_simulator()
        order = simulator.execute(
            buy(order_type=OrderType.LIMIT, limit_price=dec("39000")),
            decision_time=0, submit_time=0, market=StubMarket("40000"),
        )
        self.assertEqual(order.rejection_reason, RejectionReason.LIMIT_NOT_MARKETABLE)


class CostApplicationTests(unittest.TestCase):
    def test_taker_fee_is_charged_in_the_received_asset_by_default(self) -> None:
        costs = zero_cost_models(
            fees=FlatFeeModel(
                schedule=FeeSchedule(
                    maker_rate=dec("0.001"), taker_rate=dec("0.006"), source="TEST"
                )
            )
        )
        simulator, portfolio = make_simulator(costs=costs)
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0, market=StubMarket()
        )
        # Full 100 USD leaves the account; the commission comes out of the BTC.
        self.assertEqual(portfolio.cash.available("USD"), dec("900"))
        self.assertEqual(order.fees_by_asset["BTC"], dec("0.000015"))
        self.assertEqual(portfolio.quantity("BTC"), dec("0.0025") - dec("0.000015"))

    def test_quote_asset_fee_policy_reduces_the_affordable_quantity(self) -> None:
        costs = zero_cost_models(
            fees=FlatFeeModel(
                schedule=FeeSchedule(
                    maker_rate=dec("0.001"), taker_rate=dec("0.006"), source="TEST"
                ),
                commission_asset_policy=CommissionAssetPolicy.QUOTE_ASSET,
            )
        )
        simulator, portfolio = make_simulator(costs=costs)
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.fees_by_asset["USD"], order.fee_in_quote)
        spent = dec("1000") - portfolio.cash.available("USD")
        self.assertLessEqual(spent, dec("100"), "total outlay must respect the budget")
        self.assertGreater(spent, dec("99"))

    def test_maker_and_taker_rates_differ(self) -> None:
        schedule = FeeSchedule(maker_rate=dec("0.001"), taker_rate=dec("0.006"), source="TEST")
        costs = zero_cost_models(fees=FlatFeeModel(schedule=schedule))
        taker, _ = make_simulator(costs=costs)
        maker, _ = make_simulator(costs=costs, fill_model=FullFillModel(is_maker=True))
        taker_order = taker.execute(buy(), decision_time=0, submit_time=0, market=StubMarket())
        maker_order = maker.execute(buy(), decision_time=0, submit_time=0, market=StubMarket())
        self.assertGreater(taker_order.fees_by_asset["BTC"], maker_order.fees_by_asset["BTC"])

    def test_per_symbol_fee_override(self) -> None:
        costs = zero_cost_models(
            fees=FlatFeeModel(
                schedule=FeeSchedule(maker_rate=dec("0.004"), taker_rate=dec("0.006")),
                per_symbol={
                    "BTCUSD": FeeSchedule(maker_rate=dec("0"), taker_rate=dec("0"),
                                          source="promo")
                },
            )
        )
        simulator, _ = make_simulator(costs=costs)
        order = simulator.execute(buy(), decision_time=0, submit_time=0, market=StubMarket())
        self.assertEqual(order.fee_in_quote, dec("0"))

    def test_spread_and_slippage_always_move_the_price_against_a_buyer(self) -> None:
        costs = zero_cost_models(
            spread=FixedSpreadModel(default_bps=dec("10"), prefer_observed=False),
            slippage=FixedSlippageModel(bps=dec("5")),
        )
        simulator, _ = make_simulator(costs=costs)
        order = simulator.execute(buy(), decision_time=0, submit_time=0, market=StubMarket())
        self.assertGreater(order.effective_price, dec("40000"))
        # half of 10bps + 5bps = 10bps total
        self.assertEqual(order.effective_price, dec("40000") * dec("1.0010"))
        self.assertGreater(order.spread_cost_quote, dec("0"))
        self.assertGreater(order.slippage_cost_quote, dec("0"))

    def test_an_observed_quote_spread_overrides_the_assumption(self) -> None:
        costs = zero_cost_models(
            spread=FixedSpreadModel(default_bps=dec("100"), prefer_observed=True)
        )
        simulator, _ = make_simulator(costs=costs)
        order = simulator.execute(
            buy(), decision_time=0, submit_time=0,
            market=StubMarket(half_spread="0.0001"),
        )
        self.assertEqual(order.effective_price, dec("40000") * dec("1.0001"))


class PartialFillTests(unittest.TestCase):
    def test_a_half_fill_is_reported_as_partially_filled(self) -> None:
        simulator, portfolio = make_simulator(
            fill_model=FixedFractionFillModel(fractions=(dec("0.5"),))
        )
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(order.requested_base_quantity, dec("0.0025"))
        self.assertEqual(order.filled_base_quantity, dec("0.00125"))
        self.assertEqual(portfolio.cash.available("USD"), dec("950"),
                         "only the filled half is paid for")

    def test_multiple_slices_accumulate_to_a_full_fill(self) -> None:
        simulator, portfolio = make_simulator(
            fill_model=FixedFractionFillModel(
                fractions=(dec("0.5"), dec("0.5")),
                price_multipliers=(dec("1"), dec("1.01")),
            )
        )
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0, market=StubMarket()
        )
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(len(order.fills), 2)
        self.assertEqual(order.filled_base_quantity, dec("0.0025"))
        self.assertGreater(order.fills[1].price, order.fills[0].price)

    def test_volume_cap_limits_the_fill(self) -> None:
        simulator, _ = make_simulator(
            fill_model=VolumeCapFillModel(max_participation=dec("0.00001"))
        )
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0,
            market=StubMarket(quote_volume="1000000"),
        )
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertLess(order.filled_quote_quantity, dec("100"))

    def test_volume_cap_falls_back_to_a_full_fill_without_volume_data(self) -> None:
        simulator, _ = make_simulator(
            fill_model=VolumeCapFillModel(max_participation=dec("0.00001"))
        )
        order = simulator.execute(
            buy(quote="100"), decision_time=0, submit_time=0,
            market=StubMarket(quote_volume=None),
        )
        self.assertEqual(order.status, OrderStatus.FILLED)


class IntentValidationTests(unittest.TestCase):
    def test_both_sizings_at_once_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            OrderIntent(
                symbol="BTCUSD", side=OrderSide.BUY, order_type=OrderType.MARKET,
                client_order_id="x", quote_quantity=dec("10"), base_quantity=dec("1"),
            )

    def test_neither_sizing_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            OrderIntent(
                symbol="BTCUSD", side=OrderSide.BUY, order_type=OrderType.MARKET,
                client_order_id="x",
            )

    def test_limit_without_a_price_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            OrderIntent(
                symbol="BTCUSD", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                client_order_id="x", quote_quantity=dec("10"),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
