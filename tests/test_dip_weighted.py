"""The buy-more-when-it-falls strategy."""

from __future__ import annotations

import unittest

from crypto_research.backtest.engine import BacktestConfig, Backtester
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.backtest.metrics import compute_metrics
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.decimals import ZERO, dec
from crypto_research.errors import ConfigError
from crypto_research.strategies import build_strategy
from crypto_research.timeutil import DAY_MS

from .helpers import EPOCH_2024, default_rule_book, make_bars
from .test_execution import zero_cost_models


def run(closes, params, *, initial_cash="200", symbol="BTCUSD"):
    bars = make_bars(symbol, list(closes))
    dataset = MarketDataSet(bars_by_symbol={symbol: bars})
    config = BacktestConfig(
        start_time=EPOCH_2024,
        end_time=EPOCH_2024 + len(closes) * DAY_MS,
        valuation_asset="USD",
        initial_cash={"USD": dec(initial_cash)},
        costs=zero_cost_models(),
        execution=ExecutionConfig(max_reference_age_ms=90_000),
    )
    merged = {"symbols": [symbol], "quote_asset": "USD"}
    merged.update(params)
    strategy = build_strategy("dip_weighted_deployment", merged)
    result = Backtester(
        dataset=dataset, rule_book=default_rule_book(), config=config
    ).run(strategy)
    return result, compute_metrics(result)


class EquivalenceTests(unittest.TestCase):
    """With the tilt off, this must be indistinguishable from steady buying.

    This is the safety net for every result the strategy produces: if it holds,
    any difference in outcome comes from the tilt and nothing else.
    """

    def test_zero_tilt_matches_benchmark1_order_for_order(self) -> None:
        closes = [str(100 + (i % 7) * 5) for i in range(40)]
        bars = make_bars("BTCUSD", closes)
        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": bars})
        config = BacktestConfig(
            start_time=EPOCH_2024,
            end_time=EPOCH_2024 + len(closes) * DAY_MS,
            valuation_asset="USD",
            initial_cash={"USD": dec("200")},
            costs=zero_cost_models(),
            execution=ExecutionConfig(max_reference_age_ms=90_000),
        )
        shared = {"symbols": ["BTCUSD"], "quote_asset": "USD",
                  "tranche_count": 4, "cadence_days": 7}

        steady = Backtester(dataset=dataset, rule_book=default_rule_book(), config=config).run(
            build_strategy("benchmark1_staged_deployment", shared)
        )
        tilted = Backtester(dataset=dataset, rule_book=default_rule_book(), config=config).run(
            build_strategy("dip_weighted_deployment", {**shared, "tilt_strength": "0"})
        )

        self.assertEqual(
            [(o.intent.symbol, o.filled_base_quantity, o.filled_quote_quantity)
             for o in steady.orders],
            [(o.intent.symbol, o.filled_base_quantity, o.filled_quote_quantity)
             for o in tilted.orders],
        )
        self.assertEqual(
            compute_metrics(steady).ending_portfolio_value,
            compute_metrics(tilted).ending_portfolio_value,
        )


class TiltBehaviourTests(unittest.TestCase):
    def test_a_fall_increases_the_amount_spent(self) -> None:
        # Flat, then a sharp fall. The buy after the fall must be larger.
        closes = ["100"] * 10 + ["50"] * 10
        result, _ = run(
            closes,
            {"tranche_count": 4, "cadence_days": 5, "lookback_days": 15,
             "tilt_strength": "2"},
        )
        spends = [(o.decision_time, o.filled_quote_quantity) for o in result.accepted_orders]
        self.assertGreaterEqual(len(spends), 3)
        before = spends[0][1]
        after = spends[-1][1]
        self.assertGreater(after, before, "a fall should pull spending forward")

    def test_a_rise_does_not_increase_spending(self) -> None:
        closes = [str(100 + i * 5) for i in range(20)]
        result, _ = run(
            closes,
            {"tranche_count": 4, "cadence_days": 5, "lookback_days": 15,
             "tilt_strength": "2", "min_tilt": "1", "max_tilt": "3"},
        )
        spends = [o.filled_quote_quantity for o in result.accepted_orders]
        # At a new high the fall is zero, so the tilt is exactly 1 throughout.
        self.assertTrue(all(s <= spends[0] * dec("1.01") for s in spends), spends)

    def test_fall_is_measured_against_the_visible_peak_only(self) -> None:
        # Price rises to 200 late. Early decisions must not see that peak, so
        # they must not treat the early price as a huge fall from it.
        closes = ["100"] * 5 + ["200"] * 5
        result, _ = run(
            closes,
            {"tranche_count": 5, "cadence_days": 1, "lookback_days": 90,
             "tilt_strength": "2"},
        )
        first = result.accepted_orders[0]
        self.assertIn("tilt=1", first.intent.tag,
                      "the first buy sits at its own high, so the tilt must be 1")

    def test_the_tilt_is_capped(self) -> None:
        closes = ["1000"] * 5 + ["1"] * 5     # a 99.9% collapse
        # The schedule has to run PAST the collapse for the tilt to see it --
        # eight daily dates over ten days, so dates 6-8 land after the fall.
        result, _ = run(
            closes,
            {"tranche_count": 8, "cadence_days": 1, "lookback_days": 90,
             "tilt_strength": "50", "max_tilt": "3"},
        )
        tags = [o.intent.tag for o in result.accepted_orders]
        self.assertTrue(any("tilt=3" in tag for tag in tags), tags)
        self.assertFalse(any("tilt=4" in tag or "tilt=5" in tag for tag in tags), tags)


class PoolDisciplineTests(unittest.TestCase):
    def test_the_pool_is_never_exceeded(self) -> None:
        closes = ["100"] * 5 + ["10"] * 25
        result, metrics = run(
            closes,
            {"tranche_count": 6, "cadence_days": 4, "lookback_days": 20,
             "tilt_strength": "4"},
        )
        self.assertLessEqual(metrics.total_purchased_capital, dec("200"))
        self.assertGreaterEqual(result.portfolio.cash.available("USD"), ZERO)

    def test_the_pool_is_fully_deployed_by_the_end(self) -> None:
        # Comparability with steady buying depends on this: both must finish
        # invested, or the difference is just cash drag.
        closes = ["100"] * 10 + ["60"] * 20
        result, metrics = run(
            closes,
            {"tranche_count": 5, "cadence_days": 5, "lookback_days": 20,
             "tilt_strength": "2"},
        )
        leftover = result.portfolio.cash.available("USD")
        self.assertLess(leftover, dec("1"), f"{leftover} left undeployed")

    def test_no_orders_after_the_schedule_ends(self) -> None:
        closes = ["100"] * 30
        result, _ = run(
            closes, {"tranche_count": 3, "cadence_days": 2, "lookback_days": 20}
        )
        dates = sorted({o.decision_time for o in result.orders})
        self.assertEqual(len(dates), 3)

    def test_it_only_spends_the_initial_pool(self) -> None:
        result, _ = run("100 100 100 100".split(), {"tranche_count": 2, "cadence_days": 1})
        for order in result.orders:
            self.assertEqual(order.intent.funding_source.value, "INITIAL")


class DeterminismTests(unittest.TestCase):
    def test_repeated_runs_are_identical(self) -> None:
        closes = ["100", "80", "120", "70", "90", "110", "60", "130"]
        params = {"tranche_count": 4, "cadence_days": 1, "lookback_days": 5,
                  "tilt_strength": "2"}
        first, m1 = run(closes, params)
        second, m2 = run(closes, params)
        self.assertEqual(
            [o.to_dict() for o in first.orders], [o.to_dict() for o in second.orders]
        )
        self.assertEqual(m1.to_dict(), m2.to_dict())


class ConfigValidationTests(unittest.TestCase):
    def test_bad_parameters_are_refused(self) -> None:
        cases = [
            {"tranche_count": 0},
            {"cadence_days": -1},
            {"lookback_days": 1},
            {"tilt_strength": "-1"},
            {"min_tilt": "0"},
            {"min_tilt": "4", "max_tilt": "2"},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    build_strategy(
                        "dip_weighted_deployment", {"symbols": ["BTCUSD"], **bad}
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
