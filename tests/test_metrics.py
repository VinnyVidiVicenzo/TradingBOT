"""Metric derivation."""

from __future__ import annotations

import unittest

from crypto_research.backtest.engine import BacktestConfig, Backtester, EquityPoint
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.backtest.metrics import (
    _max_drawdown,
    _time_weighted_exposure,
    compare,
    compute_metrics,
)
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.decimals import dec, div
from crypto_research.models import ContributionType, ExternalContribution, PortfolioHolding
from crypto_research.strategies import build_strategy
from crypto_research.timeutil import DAY_MS

from .helpers import EPOCH_2024, default_rule_book, make_bars
from .test_execution import zero_cost_models


def point(time: int, total: str, cash: str, units: str, nav: str, inflow: str = "0") -> EquityPoint:
    return EquityPoint(
        time=time,
        total_value=dec(total),
        cash_value=dec(cash),
        invested_value=dec(total) - dec(cash),
        units=dec(units),
        nav_per_unit=dec(nav),
        contribution_inflow=dec(inflow),
    )


# The first daily bar closes 1 ms before its day ends, and that close is the
# run's first mark. Events at one millisecond resolve contribution-before-mark,
# so a deposit at or before this offset is credited ahead of the first mark.
FIRST_MARK_OFFSET = DAY_MS - 1


def deposit(offset_ms: int, amount: str = "100") -> ExternalContribution:
    """A one-off USD deposit that becomes available ``offset_ms`` after the run start."""
    return ExternalContribution(
        amount=dec(amount),
        asset="USD",
        availability_time=EPOCH_2024 + offset_ms,
        contribution_type=ContributionType.ONE_OFF_DEPOSIT,
    )


class DrawdownTests(unittest.TestCase):
    def test_peak_to_trough_on_nav(self) -> None:
        curve = [
            point(0, "100", "0", "100", "1.00"),
            point(1, "120", "0", "100", "1.20"),
            point(2, "90", "0", "100", "0.90"),
            point(3, "110", "0", "100", "1.10"),
        ]
        worst, start, end = _max_drawdown(curve)
        self.assertEqual(worst, dec("0.25"))
        self.assertEqual((start, end), (1, 2))

    def test_a_deposit_is_not_mistaken_for_a_recovery(self) -> None:
        # Total value climbs from 90 to 190 only because 100 was deposited.
        # NAV per unit does not move, so the drawdown must not shrink.
        curve = [
            point(0, "100", "0", "100", "1.00"),
            point(1, "90", "0", "100", "0.90"),
            point(2, "190", "100", "211.11", "0.90", inflow="100"),
        ]
        worst, _, _ = _max_drawdown(curve)
        self.assertEqual(worst, dec("0.10"))

    def test_a_monotonic_curve_has_no_drawdown(self) -> None:
        curve = [point(i, str(100 + i), "0", "100", str(1 + i / 100)) for i in range(5)]
        self.assertEqual(_max_drawdown(curve)[0], dec("0"))


class ExposureTests(unittest.TestCase):
    def test_all_cash_is_zero_exposure(self) -> None:
        curve = [point(0, "100", "100", "100", "1"), point(DAY_MS, "100", "100", "100", "1")]
        self.assertEqual(_time_weighted_exposure(curve), dec("0"))

    def test_fully_invested_is_full_exposure(self) -> None:
        curve = [point(0, "100", "0", "100", "1"), point(DAY_MS, "100", "0", "100", "1")]
        self.assertEqual(_time_weighted_exposure(curve), dec("1"))

    def test_exposure_is_weighted_by_elapsed_time_not_by_sample_count(self) -> None:
        curve = [
            point(0, "100", "100", "100", "1"),          # all cash for 1 day
            point(DAY_MS, "100", "0", "100", "1"),        # fully invested for 3 days
            point(4 * DAY_MS, "100", "0", "100", "1"),
        ]
        self.assertEqual(_time_weighted_exposure(curve), dec("0.75"))


class EndToEndMetricTests(unittest.TestCase):
    def _run(self, *, closes, initial_cash="0", contributions=(), holdings=(), strategy=None):
        days = len(closes)
        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": make_bars("BTCUSD", closes)})
        config = BacktestConfig(
            start_time=EPOCH_2024,
            end_time=EPOCH_2024 + days * DAY_MS,
            initial_cash={"USD": dec(initial_cash)} if dec(initial_cash) > 0 else {},
            starting_holdings=holdings,
            external_contributions=contributions,
            costs=zero_cost_models(),
            execution=ExecutionConfig(max_reference_age_ms=90_000),
        )
        result = Backtester(
            dataset=dataset, rule_book=default_rule_book(), config=config
        ).run(strategy or build_strategy("benchmark0_hold"))
        return result, compute_metrics(result)

    def test_hold_a_doubling_asset_returns_100_percent(self) -> None:
        _, metrics = self._run(
            closes=["100", "200"],
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))],
        )
        self.assertEqual(metrics.starting_portfolio_value, dec("100"))
        self.assertEqual(metrics.ending_portfolio_value, dec("200"))
        self.assertEqual(metrics.net_return, dec("1"))
        self.assertEqual(metrics.time_weighted_return, dec("1"))

    def test_contributions_are_excluded_from_return(self) -> None:
        # 100 in, 100 deposited, flat prices: the return is zero, not 100%.
        _, metrics = self._run(
            closes=["100"] * 5,
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))],
            contributions=[
                ExternalContribution(
                    amount=dec("100"), asset="USD",
                    availability_time=EPOCH_2024 + 3 * DAY_MS - 1,
                    contribution_type=ContributionType.ONE_OFF_DEPOSIT,
                )
            ],
        )
        self.assertEqual(metrics.total_external_contributions, dec("100"))
        self.assertEqual(metrics.total_capital_in, dec("200"))
        self.assertEqual(metrics.ending_portfolio_value, dec("200"))
        self.assertEqual(metrics.net_return, dec("0"))

    def test_a_deposit_credited_before_the_first_mark_is_counted_once(self) -> None:
        # The deposit is already inside the first mark's total value and is also
        # carried on it as an inflow. Counting it again as starting value doubled
        # capital in, so a cash hold that lost nothing read as a 50% loss.
        for label, offset in (
            ("at the run start", 0),
            ("in the first mark's own millisecond", FIRST_MARK_OFFSET),
        ):
            with self.subTest(label):
                result, metrics = self._run(
                    closes=["100"] * 5, contributions=[deposit(offset)]
                )
                first = result.equity_curve[0]
                # The premise: the deposit really is attached to the first mark.
                self.assertEqual(first.time, EPOCH_2024 + FIRST_MARK_OFFSET)
                self.assertEqual(first.contribution_inflow, dec("100"))
                self.assertEqual(metrics.starting_portfolio_value, dec("0"))
                self.assertEqual(metrics.total_external_contributions, dec("100"))
                self.assertEqual(metrics.total_capital_in, dec("100"))
                self.assertEqual(metrics.ending_portfolio_value, dec("100"))
                self.assertEqual(metrics.net_profit, dec("0"))
                self.assertEqual(metrics.net_return, dec("0"))
                self.assertEqual(metrics.time_weighted_return, dec("0"))
                self.assertEqual(metrics.max_drawdown, dec("0"))

    def test_a_deposit_one_day_after_the_start_is_unchanged(self) -> None:
        # A deposit a full day after the start lands 1 ms past the first mark,
        # which therefore sees an empty portfolio: nothing to double count and
        # nothing to take back out.
        result, metrics = self._run(closes=["100"] * 5, contributions=[deposit(DAY_MS)])
        self.assertEqual(result.equity_curve[0].contribution_inflow, dec("0"))
        self.assertEqual(metrics.starting_portfolio_value, dec("0"))
        self.assertEqual(metrics.total_external_contributions, dec("100"))
        self.assertEqual(metrics.total_capital_in, dec("100"))
        self.assertEqual(metrics.ending_portfolio_value, dec("100"))
        self.assertEqual(metrics.net_return, dec("0"))

    def test_initial_cash_and_a_deposit_at_the_start_are_both_capital_in(self) -> None:
        # Only the deposit comes out of the first mark's value; the initial cash
        # stays as starting value.
        _, metrics = self._run(
            closes=["100"] * 5, initial_cash="50", contributions=[deposit(0)]
        )
        self.assertEqual(metrics.starting_portfolio_value, dec("50"))
        self.assertEqual(metrics.total_external_contributions, dec("100"))
        self.assertEqual(metrics.total_capital_in, dec("150"))
        self.assertEqual(metrics.ending_portfolio_value, dec("150"))
        self.assertEqual(metrics.net_profit, dec("0"))
        self.assertEqual(metrics.net_return, dec("0"))

    def test_money_and_time_weighted_returns_agree_for_a_deposit_at_the_start(self) -> None:
        # Deposit at the start, spent at the first decision, price then 100 -> 150.
        # With no other flows the two return measures must agree, and the NAV
        # series (which issues no units for initial cash) must not distort either
        # the return or the drawdown.
        for label, initial_cash, net_profit, net_return in (
            ("no initial cash", "0", "50", "0.5"),
            ("with initial cash", "100", "50", "0.25"),
        ):
            with self.subTest(label):
                strategy = build_strategy(
                    "benchmark2_contribution_dca", {"symbols": ["BTCUSD"]}
                )
                _, metrics = self._run(
                    closes=["100", "150"],
                    initial_cash=initial_cash,
                    contributions=[deposit(0)],
                    strategy=strategy,
                )
                self.assertEqual(metrics.total_capital_in, dec("100") + dec(initial_cash))
                self.assertEqual(metrics.net_profit, dec(net_profit))
                self.assertEqual(metrics.net_return, dec(net_return))
                self.assertEqual(metrics.time_weighted_return, dec(net_return))
                self.assertEqual(metrics.max_drawdown, dec("0"))

    def test_average_entry_price_across_a_rising_market(self) -> None:
        strategy = build_strategy(
            "benchmark1_staged_deployment",
            {"symbols": ["BTCUSD"], "tranche_count": 2, "cadence_days": 1},
        )
        _, metrics = self._run(closes=["100", "200", "300"], initial_cash="200",
                               strategy=strategy)
        # 100 USD at 100, then 100 USD at 200 -> 1.5 BTC for 200 USD.
        self.assertEqual(metrics.acquired_quantity_by_asset["BTC"], dec("1.5"))
        self.assertEqual(
            metrics.average_entry_price_by_asset["BTC"],
            div(dec("200"), dec("1.5")),
        )
        # The average entry sits below the closing price: that is the whole
        # point of staging into a rising market.
        self.assertLess(metrics.average_entry_price_by_asset["BTC"], dec("300"))

    def test_fees_slippage_and_spread_are_reported_separately(self) -> None:
        from crypto_research.backtest.costs import (
            FeeSchedule,
            FixedSlippageModel,
            FixedSpreadModel,
            FlatFeeModel,
        )

        dataset = MarketDataSet(bars_by_symbol={"BTCUSD": make_bars("BTCUSD", ["100", "100"])})
        config = BacktestConfig(
            start_time=EPOCH_2024,
            end_time=EPOCH_2024 + 2 * DAY_MS,
            initial_cash={"USD": dec("100")},
            costs=zero_cost_models(
                fees=FlatFeeModel(
                    schedule=FeeSchedule(maker_rate=dec("0.001"), taker_rate=dec("0.006"))
                ),
                spread=FixedSpreadModel(default_bps=dec("20"), prefer_observed=False),
                slippage=FixedSlippageModel(bps=dec("30")),
            ),
            execution=ExecutionConfig(max_reference_age_ms=90_000),
        )
        strategy = build_strategy(
            "benchmark1_staged_deployment",
            {"symbols": ["BTCUSD"], "tranche_count": 1, "cadence_days": 0},
        )
        result = Backtester(
            dataset=dataset, rule_book=default_rule_book(), config=config
        ).run(strategy)
        metrics = compute_metrics(result)
        self.assertGreater(metrics.total_fees_in_quote, dec("0"))
        self.assertGreater(metrics.estimated_spread_cost, dec("0"))
        self.assertGreater(metrics.estimated_slippage_cost, dec("0"))
        self.assertEqual(
            metrics.total_cost_drag,
            metrics.total_fees_in_quote
            + metrics.estimated_spread_cost
            + metrics.estimated_slippage_cost,
        )

    def test_order_counts_and_rejection_breakdown(self) -> None:
        strategy = build_strategy(
            "benchmark1_staged_deployment",
            {"symbols": ["BTCUSD"], "tranche_count": 4, "cadence_days": 1},
        )
        _, metrics = self._run(closes=["100"] * 5, initial_cash="2", strategy=strategy)
        self.assertEqual(metrics.attempted_orders, 4)
        self.assertEqual(metrics.accepted_orders, 0)
        self.assertEqual(metrics.rejected_orders, 4)
        self.assertEqual(metrics.fill_rate, dec("0"))
        self.assertEqual(sum(metrics.rejections_by_reason.values()), 4)

    def test_win_rate_is_reported_as_deliberately_absent(self) -> None:
        _, metrics = self._run(
            closes=["100", "200"],
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))],
        )
        payload = metrics.to_dict()
        self.assertIsNone(payload["win_rate"])
        self.assertIn("accumulation", payload["win_rate_omitted_because"])

    def test_turnover_is_traded_notional_over_average_value(self) -> None:
        strategy = build_strategy(
            "benchmark1_staged_deployment",
            {"symbols": ["BTCUSD"], "tranche_count": 1, "cadence_days": 0},
        )
        _, metrics = self._run(closes=["100", "100"], initial_cash="100", strategy=strategy)
        self.assertEqual(metrics.total_purchased_capital, dec("100"))
        self.assertEqual(metrics.turnover, dec("1"))

    def test_comparison_table_renders(self) -> None:
        _, hold = self._run(
            closes=["100", "200"],
            holdings=[PortfolioHolding(asset="BTC", quantity=dec("1"))],
        )
        table = compare({"benchmark0": hold})
        self.assertIn("net return", table)
        self.assertIn("max drawdown", table)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
