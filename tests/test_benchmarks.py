"""The three V1 benchmarks, and the separation between benchmarks 1 and 2."""

from __future__ import annotations

import unittest

from crypto_research.backtest.engine import BacktestConfig, Backtester
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.backtest.metrics import compute_metrics
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError
from crypto_research.models import (
    CashSource,
    ContributionType,
    ExternalContribution,
    PortfolioHolding,
)
from crypto_research.strategies import build_strategy
from crypto_research.strategies.base import AssetUniverse
from crypto_research.timeutil import DAY_MS

from .helpers import EPOCH_2024, default_rule_book, make_bars
from .test_execution import zero_cost_models

DAYS = 30


def dataset(price: str = "100") -> MarketDataSet:
    return MarketDataSet(
        bars_by_symbol={
            "BTCUSD": make_bars("BTCUSD", [price] * DAYS),
            "ETHUSD": make_bars("ETHUSD", [price] * DAYS),
        }
    )


def run(strategy, *, initial_cash="0", contributions=(), holdings=()):
    config = BacktestConfig(
        start_time=EPOCH_2024,
        end_time=EPOCH_2024 + DAYS * DAY_MS,
        valuation_asset="USD",
        initial_cash={"USD": dec(initial_cash)} if dec(initial_cash) > 0 else {},
        starting_holdings=holdings,
        external_contributions=contributions,
        costs=zero_cost_models(),
        execution=ExecutionConfig(max_reference_age_ms=90_000),
    )
    result = Backtester(
        dataset=dataset(), rule_book=default_rule_book(), config=config
    ).run(strategy)
    return result, compute_metrics(result)


def payday(day: int, amount: str = "50") -> ExternalContribution:
    return ExternalContribution(
        amount=dec(amount),
        asset="USD",
        availability_time=EPOCH_2024 + day * DAY_MS - 1,
        contribution_type=ContributionType.RECURRING_DEPOSIT,
        label=f"day-{day}",
    )


class Benchmark0Tests(unittest.TestCase):
    def test_places_no_orders(self) -> None:
        strategy = build_strategy("benchmark0_hold")
        result, metrics = run(
            strategy,
            initial_cash="200",
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))],
        )
        self.assertEqual(result.orders, [])
        self.assertEqual(metrics.attempted_orders, 0)
        self.assertEqual(metrics.total_fees_in_quote, dec("0"))

    def test_leaves_cash_uninvested(self) -> None:
        _, metrics = run(build_strategy("benchmark0_hold"), initial_cash="200")
        self.assertEqual(metrics.uninvested_cash_by_asset, {"USD": dec("200")})
        self.assertEqual(metrics.uninvested_cash_fraction, dec("1"))
        self.assertEqual(metrics.time_weighted_exposure, dec("0"))

    def test_marks_starting_holdings_to_market(self) -> None:
        _, metrics = run(
            build_strategy("benchmark0_hold"),
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("2"))],
        )
        self.assertEqual(metrics.ending_portfolio_value, dec("200"))
        self.assertEqual(metrics.net_return, dec("0"))
        self.assertEqual(metrics.time_weighted_exposure, dec("1"))


class Benchmark1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = build_strategy(
            "benchmark1_staged_deployment",
            {
                "symbols": ["BTCUSD", "ETHUSD"],
                "tranche_count": 4,
                "cadence_days": 7,
            },
        )

    def test_deploys_the_pool_in_the_requested_number_of_tranches(self) -> None:
        result, metrics = run(self.strategy, initial_cash="200")
        tranche_tags = sorted({o.intent.tag for o in result.accepted_orders})
        self.assertEqual(tranche_tags, ["tranche=0", "tranche=1", "tranche=2", "tranche=3"])
        self.assertEqual(metrics.accepted_orders, 8, "4 tranches x 2 symbols")

    def test_spends_the_whole_pool_and_no_more(self) -> None:
        result, metrics = run(self.strategy, initial_cash="200")
        self.assertEqual(metrics.total_purchased_capital, dec("200"))
        self.assertEqual(result.portfolio.cash.available("USD"), dec("0"))

    def test_tranches_land_on_the_configured_cadence(self) -> None:
        result, _ = run(self.strategy, initial_cash="200")
        times = sorted({o.decision_time for o in result.accepted_orders})
        gaps = {later - earlier for earlier, later in zip(times, times[1:])}
        self.assertEqual(gaps, {7 * DAY_MS})

    def test_splits_each_tranche_by_weight(self) -> None:
        strategy = build_strategy(
            "benchmark1_staged_deployment",
            {
                "symbols": ["BTCUSD", "ETHUSD"],
                "weights": {"BTCUSD": "3", "ETHUSD": "1"},
                "tranche_count": 2,
                "cadence_days": 7,
            },
        )
        result, _ = run(strategy, initial_cash="200")
        spent = {"BTCUSD": dec("0"), "ETHUSD": dec("0")}
        for order in result.accepted_orders:
            spent[order.intent.symbol] += order.filled_quote_quantity
        self.assertEqual(spent["BTCUSD"], dec("150"))
        self.assertEqual(spent["ETHUSD"], dec("50"))

    def test_will_not_spend_contributed_money(self) -> None:
        # The pool is measured at the first decision, so a later deposit can
        # never enlarge it, and the funding source blocks it outright.
        result, _ = run(self.strategy, initial_cash="200", contributions=[payday(10, "500")])
        self.assertEqual(
            result.portfolio.cash.available("USD", CashSource.EXTERNAL_CONTRIBUTION),
            dec("500"),
            "contributed cash must be untouched by a staged-deployment plan",
        )
        for order in result.accepted_orders:
            self.assertEqual(order.intent.funding_source, CashSource.INITIAL)

    def test_a_pool_too_small_for_its_tranche_count_produces_rejections(self) -> None:
        # 2 USD across 4 tranches and 2 symbols is 25 cents per order, below
        # the 1 USD minimum notional. The rejections are the finding.
        result, metrics = run(self.strategy, initial_cash="2")
        self.assertEqual(metrics.accepted_orders, 0)
        self.assertEqual(metrics.rejected_orders, 8)
        self.assertEqual(
            set(metrics.rejections_by_reason), {"EXCHANGE_FILTER"}
        )

    def test_a_single_tranche_is_a_lump_sum(self) -> None:
        strategy = build_strategy(
            "benchmark1_staged_deployment",
            {"symbols": ["BTCUSD"], "tranche_count": 1, "cadence_days": 0},
        )
        result, metrics = run(strategy, initial_cash="200")
        self.assertEqual(metrics.accepted_orders, 1)
        self.assertEqual(result.orders[0].decision_time, result.equity_curve[0].time)

    def test_invalid_tranche_count_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            build_strategy(
                "benchmark1_staged_deployment",
                {"symbols": ["BTCUSD"], "tranche_count": 0},
            )


class Benchmark2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = build_strategy(
            "benchmark2_contribution_dca", {"symbols": ["BTCUSD", "ETHUSD"]}
        )

    def test_buys_only_when_a_contribution_has_arrived(self) -> None:
        result, metrics = run(
            self.strategy, contributions=[payday(5), payday(12)]
        )
        decision_times = sorted({o.decision_time for o in result.accepted_orders})
        self.assertEqual(
            decision_times,
            [EPOCH_2024 + 5 * DAY_MS - 1, EPOCH_2024 + 12 * DAY_MS - 1],
        )
        self.assertEqual(metrics.total_purchased_capital, dec("100"))

    def test_never_touches_the_initial_pool(self) -> None:
        result, _ = run(
            self.strategy, initial_cash="200", contributions=[payday(5)]
        )
        self.assertEqual(
            result.portfolio.cash.available("USD", CashSource.INITIAL),
            dec("200"),
            "a contribution-funded plan must leave pre-existing cash alone",
        )
        for order in result.accepted_orders:
            self.assertEqual(
                order.intent.funding_source, CashSource.EXTERNAL_CONTRIBUTION
            )

    def test_does_nothing_at_all_without_contributions(self) -> None:
        result, metrics = run(self.strategy, initial_cash="200")
        self.assertEqual(result.orders, [])
        self.assertEqual(metrics.attempted_orders, 0)

    def test_a_cadence_holds_contributed_cash_until_the_next_buy_date(self) -> None:
        strategy = build_strategy(
            "benchmark2_contribution_dca",
            {"symbols": ["BTCUSD"], "cadence_days": 14},
        )
        result, _ = run(strategy, contributions=[payday(2, "30"), payday(4, "30")])
        # The buy grid is day 1, day 15, day 29. Day 1 has no cash yet, so the
        # two deposits sit in the contributed bucket and are both deployed on
        # day 15 as a single purchase.
        purchases = list(result.accepted_orders)
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0].filled_quote_quantity, dec("60"))
        self.assertEqual(purchases[0].decision_time, EPOCH_2024 + 15 * DAY_MS - 1)

    def test_leftover_dust_is_not_re_offered_on_every_tick(self) -> None:
        # Rounding leaves a few cents in the contributed bucket. Retrying it
        # forever would bury the real rejections under thousands of fake ones.
        strategy = build_strategy(
            "benchmark2_contribution_dca", {"symbols": ["BTCUSD"]}
        )
        result, metrics = run(strategy, contributions=[payday(5, "50.0007")])
        self.assertEqual(metrics.accepted_orders, 1)
        # One deposit, one deployment attempt, and at most one further attempt
        # once the balance changes to dust -- not one per remaining day.
        self.assertLessEqual(metrics.attempted_orders, 3)
        self.assertGreater(
            result.portfolio.cash.available("USD", CashSource.EXTERNAL_CONTRIBUTION),
            dec("0"),
            "dust should remain, and should simply sit there",
        )

    def test_new_money_always_gets_a_fresh_attempt(self) -> None:
        # The dust guard must not swallow a later deposit.
        strategy = build_strategy(
            "benchmark2_contribution_dca", {"symbols": ["BTCUSD"]}
        )
        result, metrics = run(
            strategy, contributions=[payday(5, "50"), payday(12, "50")]
        )
        self.assertEqual(metrics.accepted_orders, 2)
        self.assertEqual(metrics.total_purchased_capital, dec("100"))

    def test_min_deploy_amount_suppresses_sub_minimum_orders(self) -> None:
        strategy = build_strategy(
            "benchmark2_contribution_dca",
            {"symbols": ["BTCUSD"], "min_deploy_amount": "25"},
        )
        result, metrics = run(strategy, contributions=[payday(5, "10"), payday(6, "20")])
        # 10 alone is below the floor; 10+20 clears it on day 6.
        self.assertEqual(metrics.attempted_orders, 1)
        self.assertEqual(result.accepted_orders[0].filled_quote_quantity, dec("30"))


class BenchmarkSeparationTests(unittest.TestCase):
    def test_the_two_plans_spend_disjoint_money_in_the_same_window(self) -> None:
        staged, _ = run(
            build_strategy(
                "benchmark1_staged_deployment",
                {"symbols": ["BTCUSD"], "tranche_count": 2, "cadence_days": 7},
            ),
            initial_cash="100",
            contributions=[payday(5, "60")],
        )
        dca, _ = run(
            build_strategy("benchmark2_contribution_dca", {"symbols": ["BTCUSD"]}),
            initial_cash="100",
            contributions=[payday(5, "60")],
        )
        staged_spend = sum(
            (o.filled_quote_quantity for o in staged.accepted_orders), dec("0")
        )
        dca_spend = sum((o.filled_quote_quantity for o in dca.accepted_orders), dec("0"))
        self.assertEqual(staged_spend, dec("100"), "staged plan spends only the pool")
        self.assertEqual(dca_spend, dec("60"), "dca plan spends only the deposit")


class AssetUniverseTests(unittest.TestCase):
    def test_equal_weighting_is_the_fallback_not_a_requirement(self) -> None:
        universe = AssetUniverse(symbols=("A", "B", "C", "D"))
        self.assertEqual(universe.weight("A"), dec("0.25"))

    def test_weights_are_normalized(self) -> None:
        universe = AssetUniverse.from_config(["A", "B"], {"A": "3", "B": "1"})
        self.assertEqual(universe.weight("A"), dec("0.75"))
        self.assertEqual(universe.weight("B"), dec("0.25"))

    def test_missing_weight_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            AssetUniverse.from_config(["A", "B"], {"A": "1"})

    def test_weight_for_an_unlisted_symbol_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            AssetUniverse.from_config(["A"], {"A": "1", "Z": "1"})

    def test_duplicate_symbols_are_refused(self) -> None:
        with self.assertRaises(ConfigError):
            AssetUniverse(symbols=("A", "A"))

    def test_an_empty_universe_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            AssetUniverse(symbols=())

    def test_the_asset_list_is_not_hard_coded_anywhere(self) -> None:
        # Any three symbols must work; nothing presumes BTC/ETH/BNB.
        universe = AssetUniverse.from_config(["SOLUSD", "LINKUSD", "DOTUSD"])
        self.assertEqual(
            sorted(universe.weights), ["DOTUSD", "LINKUSD", "SOLUSD"]
        )


class StrategyRegistryTests(unittest.TestCase):
    def test_unknown_strategy_name_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            build_strategy("benchmark9_magic")

    def test_registered_names(self) -> None:
        from crypto_research.strategies import STRATEGY_BUILDERS

        self.assertEqual(
            sorted(STRATEGY_BUILDERS),
            [
                "benchmark0_hold",
                "benchmark1_staged_deployment",
                "benchmark2_contribution_dca",
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
