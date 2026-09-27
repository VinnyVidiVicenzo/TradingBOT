"""Engine guarantees: ordering, look-ahead, contribution timing, determinism."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Sequence

from crypto_research.backtest.context import StrategyContext
from crypto_research.backtest.engine import BacktestConfig, Backtester
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.backtest.market import MarketState, MarketView
from crypto_research.backtest.metrics import compute_metrics
from crypto_research.backtest.orders import OrderIntent, OrderSide
from crypto_research.binanceus.filters import OrderType
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError, LookaheadError
from crypto_research.models import (
    CashSource,
    ContributionType,
    ExternalContribution,
    PortfolioHolding,
)
from crypto_research.strategies.base import BaseStrategy
from crypto_research.timeutil import DAY_MS

from .helpers import EPOCH_2024, default_rule_book, make_bar, make_bars
from .test_execution import zero_cost_models


class RecordingStrategy(BaseStrategy):
    """Records what it was shown at each decision, and optionally buys."""

    name = "recording"
    version = "1"

    def __init__(self, *, buy_quote: str | None = None, symbol: str = "BTCUSD") -> None:
        self.observations: list[dict[str, object]] = []
        self._buy_quote = buy_quote
        self._symbol = symbol

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        bar = ctx.market.last_bar(self._symbol)
        self.observations.append(
            {
                "now": ctx.now,
                "bar_open_time": None if bar is None else bar.open_time,
                "bar_close": None if bar is None else bar.close,
                "history_len": len(ctx.market.history(self._symbol, 1000)),
                "initial_cash": ctx.portfolio.available_cash("USD", CashSource.INITIAL),
                "contributed_cash": ctx.portfolio.available_cash(
                    "USD", CashSource.EXTERNAL_CONTRIBUTION
                ),
                "holding": ctx.portfolio.holding("BTC"),
            }
        )
        if self._buy_quote is None:
            return ()
        return [
            OrderIntent(
                symbol=self._symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                client_order_id=ctx.client_order_id(self.name, self._symbol),
                quote_quantity=dec(self._buy_quote),
                funding_source=CashSource.INITIAL,
            )
        ]


def build_backtester(
    *,
    closes: Sequence[str] = ("100", "110", "120", "130", "140"),
    initial_cash: str = "0",
    holdings=(),
    contributions=(),
    days: int | None = None,
) -> Backtester:
    bars = make_bars("BTCUSD", list(closes))
    dataset = MarketDataSet(bars_by_symbol={"BTCUSD": bars})
    span = (days if days is not None else len(closes)) * DAY_MS
    config = BacktestConfig(
        start_time=EPOCH_2024,
        end_time=EPOCH_2024 + span,
        valuation_asset="USD",
        initial_cash={"USD": dec(initial_cash)} if dec(initial_cash) > 0 else {},
        starting_holdings=holdings,
        external_contributions=contributions,
        costs=zero_cost_models(),
        execution=ExecutionConfig(max_reference_age_ms=90_000),
    )
    return Backtester(dataset=dataset, rule_book=default_rule_book(), config=config)


class DecisionOrderingTests(unittest.TestCase):
    def test_a_decision_at_a_bar_close_sees_that_bar_and_nothing_later(self) -> None:
        strategy = RecordingStrategy()
        build_backtester().run(strategy)
        self.assertEqual(len(strategy.observations), 5)
        for index, observation in enumerate(strategy.observations):
            expected_open = EPOCH_2024 + index * DAY_MS
            self.assertEqual(observation["bar_open_time"], expected_open)
            self.assertEqual(observation["history_len"], index + 1,
                             "history must grow by exactly one bar per day")

    def test_the_first_decision_cannot_see_the_second_bar(self) -> None:
        strategy = RecordingStrategy()
        build_backtester(closes=("100", "999")).run(strategy)
        self.assertEqual(strategy.observations[0]["bar_close"], dec("100"))
        self.assertEqual(strategy.observations[1]["bar_close"], dec("999"))

    def test_decision_times_are_strictly_increasing(self) -> None:
        strategy = RecordingStrategy()
        build_backtester().run(strategy)
        times = [observation["now"] for observation in strategy.observations]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(set(times)), len(times))

    def test_an_empty_decision_grid_is_an_error_not_a_silent_no_op(self) -> None:
        bars = make_bars("BTCUSD", ["100"])
        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": bars})
        config = BacktestConfig(
            start_time=EPOCH_2024 + 500 * DAY_MS,
            end_time=EPOCH_2024 + 600 * DAY_MS,
            costs=zero_cost_models(),
        )
        backtester = Backtester(
            dataset=dataset, rule_book=default_rule_book(), config=config
        )
        with self.assertRaises(ConfigError):
            backtester.run(RecordingStrategy())


class LookaheadGuardTests(unittest.TestCase):
    def test_market_view_refuses_to_expose_a_future_bar(self) -> None:
        state = MarketState()
        future_bar = make_bar("BTCUSD", EPOCH_2024 + 10 * DAY_MS, "500")
        state.ingest_bar(future_bar)
        view = MarketView(state, now=EPOCH_2024)
        with self.assertRaises(LookaheadError):
            view.last_bar("BTCUSD")

    def test_market_state_refuses_an_out_of_order_ingest(self) -> None:
        state = MarketState()
        state.advance_clock(EPOCH_2024 + 5 * DAY_MS)
        with self.assertRaises(LookaheadError):
            state.ingest_bar(make_bar("BTCUSD", EPOCH_2024, "100"))

    def test_the_clock_cannot_move_backwards(self) -> None:
        state = MarketState()
        state.advance_clock(1_000)
        with self.assertRaises(LookaheadError):
            state.advance_clock(999)

    def test_download_time_does_not_affect_when_a_bar_is_usable(self) -> None:
        # History downloaded two years late was still knowable at its close.
        # Folding the download time into observability would push every
        # historical bar to today and empty the run window.
        late = make_bar("BTCUSD", EPOCH_2024, "100", received_offset_ms=700 * DAY_MS)
        self.assertEqual(late.observable_at, late.close_time)
        self.assertGreater(late.source_received_at, late.observable_at)

    def test_a_configured_feed_delay_pushes_observability_later(self) -> None:
        # The honest way to model a consumer lagging the venue: explicit,
        # configured, and recorded -- not inferred from scheduling.
        from dataclasses import replace

        bar = make_bar("BTCUSD", EPOCH_2024, "100")
        delayed = replace(bar, feed_delay_ms=2_000)
        self.assertEqual(delayed.observable_at, bar.close_time + 2_000)

    def test_a_feed_delay_defers_the_decision_that_can_see_a_bar(self) -> None:
        from dataclasses import replace

        bars = [
            replace(bar, feed_delay_ms=DAY_MS)
            for bar in make_bars("BTCUSD", ["100", "200", "300"])
        ]
        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": bars})
        config = BacktestConfig(
            start_time=EPOCH_2024,
            end_time=EPOCH_2024 + 6 * DAY_MS,
            costs=zero_cost_models(),
            execution=ExecutionConfig(max_reference_age_ms=2 * DAY_MS),
        )
        strategy = RecordingStrategy()
        Backtester(dataset=dataset, rule_book=default_rule_book(), config=config).run(
            strategy
        )
        first = strategy.observations[0]
        self.assertEqual(first["now"], bars[0].close_time + DAY_MS)
        self.assertEqual(first["bar_close"], dec("100"))


class StartingHoldingsTests(unittest.TestCase):
    def test_starting_holdings_are_never_offered_as_cash(self) -> None:
        strategy = RecordingStrategy()
        build_backtester(
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("0.01"))]
        ).run(strategy)
        for observation in strategy.observations:
            self.assertEqual(observation["initial_cash"], dec("0"))
            self.assertEqual(observation["contributed_cash"], dec("0"))
            self.assertEqual(observation["holding"], dec("0.01"))

    def test_holdings_are_marked_to_market(self) -> None:
        result = build_backtester(
            closes=("100", "200"),
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))],
        ).run(RecordingStrategy())
        self.assertEqual(result.equity_curve[0].total_value, dec("100"))
        self.assertEqual(result.equity_curve[-1].total_value, dec("200"))
        self.assertEqual(result.equity_curve[-1].cash_value, dec("0"))


class ContributionTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.third_close = EPOCH_2024 + 3 * DAY_MS - 1
        self.contribution = ExternalContribution(
            amount=dec("50"),
            asset="USD",
            availability_time=self.third_close,
            contribution_type=ContributionType.RECURRING_DEPOSIT,
            label="payday",
        )

    def test_contributed_cash_does_not_exist_before_its_availability_time(self) -> None:
        strategy = RecordingStrategy()
        build_backtester(contributions=[self.contribution]).run(strategy)
        by_time = {o["now"]: o["contributed_cash"] for o in strategy.observations}
        earlier = [t for t in by_time if t < self.third_close]
        self.assertTrue(earlier)
        for moment in earlier:
            self.assertEqual(by_time[moment], dec("0"), f"cash existed early at {moment}")

    def test_contributed_cash_is_spendable_at_its_availability_time(self) -> None:
        strategy = RecordingStrategy()
        build_backtester(contributions=[self.contribution]).run(strategy)
        by_time = {o["now"]: o["contributed_cash"] for o in strategy.observations}
        self.assertEqual(by_time[self.third_close], dec("50"))

    def test_a_contribution_outside_the_window_is_ignored_with_a_warning(self) -> None:
        late = ExternalContribution(
            amount=dec("50"), asset="USD",
            availability_time=EPOCH_2024 + 500 * DAY_MS,
        )
        result = build_backtester(contributions=[late]).run(RecordingStrategy())
        self.assertTrue(any("after the run end" in w for w in result.warnings))

    def test_contributions_land_in_their_own_bucket(self) -> None:
        result = build_backtester(
            initial_cash="100", contributions=[self.contribution]
        ).run(RecordingStrategy())
        ledger = result.portfolio.cash
        self.assertEqual(ledger.available("USD", CashSource.INITIAL), dec("100"))
        self.assertEqual(
            ledger.available("USD", CashSource.EXTERNAL_CONTRIBUTION), dec("50")
        )

    def test_contribution_inflow_is_recorded_on_the_equity_curve(self) -> None:
        result = build_backtester(
            initial_cash="100", contributions=[self.contribution]
        ).run(RecordingStrategy())
        inflows = {p.time: p.contribution_inflow for p in result.equity_curve}
        self.assertEqual(inflows[self.third_close], dec("50"))
        self.assertEqual(
            sum(inflows.values()), dec("50"), "the deposit is counted exactly once"
        )


class DeterminismTests(unittest.TestCase):
    def _run(self):
        strategy = RecordingStrategy(buy_quote="20")
        result = build_backtester(initial_cash="200").run(strategy)
        return result, compute_metrics(result)

    def test_two_identical_runs_produce_identical_orders(self) -> None:
        first, _ = self._run()
        second, _ = self._run()
        self.assertEqual(
            [order.to_dict() for order in first.orders],
            [order.to_dict() for order in second.orders],
        )

    def test_two_identical_runs_produce_identical_metrics(self) -> None:
        _, first = self._run()
        _, second = self._run()
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_two_identical_runs_produce_identical_equity_curves(self) -> None:
        first, _ = self._run()
        second, _ = self._run()
        self.assertEqual(
            [(p.time, p.total_value, p.nav_per_unit) for p in first.equity_curve],
            [(p.time, p.total_value, p.nav_per_unit) for p in second.equity_curve],
        )

    def test_client_order_ids_do_not_depend_on_the_run_id(self) -> None:
        strategy_a = RecordingStrategy(buy_quote="20")
        strategy_b = RecordingStrategy(buy_quote="20")
        run_a = build_backtester(initial_cash="200").run(strategy_a, run_id="run-A")
        run_b = build_backtester(initial_cash="200").run(strategy_b, run_id="run-B")
        self.assertEqual(
            [o.intent.client_order_id for o in run_a.orders],
            [o.intent.client_order_id for o in run_b.orders],
        )

    def test_dataset_hash_is_stable_across_rebuilds(self) -> None:
        first = MarketDataSet(bars_by_symbol={"BTCUSD": make_bars("BTCUSD", ["1", "2"])})
        second = MarketDataSet(bars_by_symbol={"BTCUSD": make_bars("BTCUSD", ["1", "2"])})
        self.assertEqual(first.version.sha256, second.version.sha256)

    def test_dataset_hash_changes_when_a_price_changes(self) -> None:
        first = MarketDataSet(bars_by_symbol={"BTCUSD": make_bars("BTCUSD", ["1", "2"])})
        second = MarketDataSet(bars_by_symbol={"BTCUSD": make_bars("BTCUSD", ["1", "3"])})
        self.assertNotEqual(first.version.sha256, second.version.sha256)

    def test_dataset_hash_ignores_when_the_data_was_downloaded(self) -> None:
        early = make_bars("BTCUSD", ["1", "2"])
        late = [
            make_bar("BTCUSD", bar.open_time, str(bar.close), received_offset_ms=99_999)
            for bar in early
        ]
        self.assertEqual(
            MarketDataSet(bars_by_symbol={"BTCUSD": early}).version.sha256,
            MarketDataSet(bars_by_symbol={"BTCUSD": late}).version.sha256,
        )


class DuplicateOrderIdTests(unittest.TestCase):
    def test_a_strategy_that_reuses_an_id_gets_a_rejection_not_a_double_buy(self) -> None:
        class CollidingStrategy(BaseStrategy):
            name = "colliding"
            version = "1"

            def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
                # A fixed ID: the sort of thing that happens when an ID is
                # derived from a date and the schedule fires twice.
                return [
                    OrderIntent(
                        symbol="BTCUSD", side=OrderSide.BUY,
                        order_type=OrderType.MARKET, client_order_id="fixed-id",
                        quote_quantity=dec("10"), funding_source=CashSource.INITIAL,
                    )
                ]

        result = build_backtester(initial_cash="500").run(CollidingStrategy())
        self.assertEqual(len(result.accepted_orders), 1)
        self.assertEqual(len(result.rejected_orders), 4)
        reasons = {o.rejection_reason.value for o in result.rejected_orders}
        self.assertEqual(reasons, {"DUPLICATE_CLIENT_ORDER_ID"})
        self.assertEqual(result.portfolio.cash.available("USD"), dec("490"))


class LatencyTests(unittest.TestCase):
    def test_an_order_reaches_the_venue_after_the_configured_latency(self) -> None:
        from crypto_research.backtest.costs import LatencyModel

        bars = make_bars("BTCUSD", ["100", "110"])
        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": bars})
        config = BacktestConfig(
            start_time=EPOCH_2024,
            end_time=EPOCH_2024 + 3 * DAY_MS,
            initial_cash={"USD": dec("500")},
            costs=zero_cost_models(latency=LatencyModel(decision_to_submit_ms=1_500)),
        )
        result = Backtester(
            dataset=dataset, rule_book=default_rule_book(), config=config
        ).run(RecordingStrategy(buy_quote="20"))
        for order in result.orders:
            self.assertEqual(order.submit_time, order.decision_time + 1_500)
            self.assertEqual(order.status.value, "FILLED")

    def test_latency_beyond_the_staleness_window_causes_rejection(self) -> None:
        from crypto_research.backtest.costs import LatencyModel

        bars = make_bars("BTCUSD", ["100", "110"])
        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": bars})
        config = BacktestConfig(
            start_time=EPOCH_2024,
            end_time=EPOCH_2024 + 3 * DAY_MS,
            initial_cash={"USD": dec("500")},
            costs=zero_cost_models(latency=LatencyModel(decision_to_submit_ms=120_000)),
            execution=ExecutionConfig(max_reference_age_ms=60_000),
        )
        result = Backtester(
            dataset=dataset, rule_book=default_rule_book(), config=config
        ).run(RecordingStrategy(buy_quote="20"))
        self.assertTrue(result.orders)
        self.assertTrue(all(o.rejected for o in result.orders))
        self.assertEqual(
            {o.rejection_reason.value for o in result.orders}, {"STALE_QUOTE"}
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
