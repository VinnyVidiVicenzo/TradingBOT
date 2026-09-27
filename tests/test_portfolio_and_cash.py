"""Cash buckets, holdings, and the USD / USDT separation."""

from __future__ import annotations

import unittest

from crypto_research.backtest.portfolio import (
    CashLedger,
    InsufficientCash,
    Portfolio,
    RateView,
)
from crypto_research.decimals import dec
from crypto_research.errors import ValuationError
from crypto_research.models import CashSource, PortfolioHolding


class CashLedgerBucketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = CashLedger()
        self.ledger.deposit("USD", dec("100"), CashSource.INITIAL, at=0)
        self.ledger.deposit("USD", dec("40"), CashSource.EXTERNAL_CONTRIBUTION, at=10)

    def test_buckets_are_tracked_separately(self) -> None:
        self.assertEqual(self.ledger.available("USD", CashSource.INITIAL), dec("100"))
        self.assertEqual(
            self.ledger.available("USD", CashSource.EXTERNAL_CONTRIBUTION), dec("40")
        )
        self.assertEqual(self.ledger.available("USD"), dec("140"))

    def test_withdrawal_from_one_bucket_does_not_touch_another(self) -> None:
        self.ledger.withdraw("USD", dec("100"), CashSource.INITIAL, at=20)
        self.assertEqual(self.ledger.available("USD", CashSource.INITIAL), dec("0"))
        self.assertEqual(
            self.ledger.available("USD", CashSource.EXTERNAL_CONTRIBUTION), dec("40")
        )

    def test_overdrawing_a_bucket_does_not_spill_into_the_other(self) -> None:
        # The whole point of the split: a staged-deployment plan must not be
        # able to reach into contributed money just because it ran out.
        with self.assertRaises(InsufficientCash):
            self.ledger.withdraw("USD", dec("120"), CashSource.INITIAL, at=20)
        self.assertEqual(self.ledger.available("USD", CashSource.INITIAL), dec("100"))

    def test_unrestricted_withdrawal_drains_in_declaration_order(self) -> None:
        taken = self.ledger.withdraw("USD", dec("130"), None, at=20)
        self.assertEqual(taken, [(CashSource.INITIAL, dec("100")),
                                 (CashSource.EXTERNAL_CONTRIBUTION, dec("30"))])

    def test_usd_and_usdt_are_separate_balances(self) -> None:
        self.ledger.deposit("USDT", dec("75"), CashSource.INITIAL, at=0)
        self.assertEqual(self.ledger.available("USD", CashSource.INITIAL), dec("100"))
        self.assertEqual(self.ledger.available("USDT", CashSource.INITIAL), dec("75"))
        self.assertEqual(
            self.ledger.totals_by_asset(), {"USD": dec("140"), "USDT": dec("75")}
        )

    def test_every_movement_is_journalled(self) -> None:
        self.ledger.withdraw("USD", dec("25"), CashSource.INITIAL, at=30, reason="buy BTCUSD")
        entries = self.ledger.entries
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1].amount, dec("-25"))
        self.assertEqual(entries[-1].reason, "buy BTCUSD")


class StartingHoldingsAreNotCashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.portfolio = Portfolio(
            starting_holdings=[
                PortfolioHolding(asset="BTC", quantity=dec("0.002")),
                PortfolioHolding(asset="BNB", quantity=dec("0.5")),
            ],
            valuation_asset="USD",
        )

    def test_holdings_do_not_appear_as_available_cash(self) -> None:
        self.assertEqual(self.portfolio.cash.available("USD"), dec("0"))
        self.assertEqual(self.portfolio.cash.available("BTC"), dec("0"))
        self.assertEqual(self.portfolio.cash.totals_by_asset(), {})

    def test_holdings_are_present_as_positions(self) -> None:
        self.assertEqual(self.portfolio.quantity("BTC"), dec("0.002"))
        self.assertEqual(self.portfolio.quantity("BNB"), dec("0.5"))

    def test_explicit_cost_basis_is_recorded_but_kept_out_of_average_entry(self) -> None:
        portfolio = Portfolio(
            starting_holdings=[
                PortfolioHolding(
                    asset="BTC", quantity=dec("0.002"),
                    cost_basis_per_unit=dec("30000"), cost_basis_asset="USD",
                )
            ]
        )
        self.assertEqual(portfolio.opening_cost_basis("BTC"), (dec("60.000"), "USD"))
        # Nothing has been bought in-simulation, so there is no entry price.
        self.assertIsNone(portfolio.average_entry_price("BTC", "USD"))

    def test_cost_basis_without_a_currency_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            PortfolioHolding(asset="BTC", quantity=dec("1"), cost_basis_per_unit=dec("30000"))


class ValuationTests(unittest.TestCase):
    def test_usdt_is_not_assumed_to_be_a_dollar(self) -> None:
        portfolio = Portfolio(valuation_asset="USD")
        portfolio.cash.deposit("USDT", dec("100"), CashSource.INITIAL, at=0)
        rates = RateView({}, valuation_asset="USD")
        with self.assertRaises(ValuationError) as ctx:
            portfolio.value(rates)
        self.assertIn("USDT", str(ctx.exception))
        self.assertIn("will not be", str(ctx.exception))

    def test_usdt_is_valued_at_its_market_price_when_available(self) -> None:
        portfolio = Portfolio(valuation_asset="USD")
        portfolio.cash.deposit("USDT", dec("100"), CashSource.INITIAL, at=0)
        rates = RateView({"USDTUSD": dec("0.9993")}, valuation_asset="USD")
        self.assertEqual(portfolio.value(rates), dec("99.93"))

    def test_an_explicit_assumed_rate_is_allowed_and_flagged(self) -> None:
        portfolio = Portfolio(valuation_asset="USD")
        portfolio.cash.deposit("USDT", dec("100"), CashSource.INITIAL, at=0)
        rates = RateView({}, valuation_asset="USD", assumed_rates={("USDT", "USD"): dec("1")})
        self.assertEqual(portfolio.value(rates), dec("100"))
        self.assertIn(("USDT", "USD"), rates.used_assumptions)

    def test_inverse_pair_is_used_when_the_direct_pair_is_absent(self) -> None:
        rates = RateView({"USDTUSD": dec("0.9993")}, valuation_asset="USDT")
        self.assertAlmostEqual(
            float(rates.rate("USD", "USDT")), 1 / 0.9993, places=9
        )

    def test_holdings_and_cash_both_count_toward_value(self) -> None:
        portfolio = Portfolio(
            starting_holdings=[PortfolioHolding(asset="BTC", quantity=dec("0.001"))],
            valuation_asset="USD",
        )
        portfolio.cash.deposit("USD", dec("50"), CashSource.INITIAL, at=0)
        rates = RateView({"BTCUSD": dec("40000")}, valuation_asset="USD")
        self.assertEqual(portfolio.value(rates), dec("90"))
        self.assertEqual(portfolio.cash_value(rates), dec("50"))

    def test_unpriceable_holding_raises_rather_than_valuing_at_zero(self) -> None:
        portfolio = Portfolio(
            starting_holdings=[PortfolioHolding(asset="BNB", quantity=dec("1"))]
        )
        with self.assertRaises(ValuationError):
            portfolio.value(RateView({}, valuation_asset="USD"))


class AverageEntryPriceTests(unittest.TestCase):
    def test_average_entry_across_two_buys(self) -> None:
        portfolio = Portfolio(valuation_asset="USD")
        portfolio.credit_asset("BTC", dec("0.001"), cost=dec("40"), quote_asset="USD")
        portfolio.credit_asset("BTC", dec("0.001"), cost=dec("60"), quote_asset="USD")
        self.assertEqual(portfolio.average_entry_price("BTC", "USD"), dec("50000"))

    def test_average_entry_is_none_for_another_quote_currency(self) -> None:
        portfolio = Portfolio(valuation_asset="USD")
        portfolio.credit_asset("BTC", dec("0.001"), cost=dec("40"), quote_asset="USDT")
        self.assertIsNone(portfolio.average_entry_price("BTC", "USD"))
        self.assertEqual(portfolio.average_entry_price("BTC", "USDT"), dec("40000"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
