"""Synthetic public prices exercise practice trading without network access."""

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from crypto_research.models import MarketBar, MarketQuote
from paper_trading.practice import PracticeAgent, HOUR, MINUTE, SYMBOLS, observe


class Prices:
    def __init__(self):
        self.at = 60 * HOUR + 1000
        self.price = Decimal("160")
        self.reverse = False
        self.gap = False
        self.size = Decimal("100")
        self.on_quote = None
        self.rising_symbols = {"BTCUSD", "ETHUSD"}

    def symbol_status(self, symbol):
        return "TRADING"

    def klines(self, symbol, interval, limit):
        bars = []
        for i in range(60):
            close = Decimal(200 - i if self.reverse else 100 + i)
            if symbol not in self.rising_symbols:
                close = Decimal("160")
            bars.append(MarketBar("binance_us", symbol, "1h", i * HOUR,
                                  (i + 1) * HOUR - 1, close, close, close, close, Decimal(1)))
        return bars[:20] + bars[21:] if self.gap else bars

    def book_ticker(self, symbol):
        if self.on_quote:
            self.on_quote()
        return MarketQuote("binance_us", symbol, self.price, self.price + Decimal("0.01"),
                           self.size, self.size, source_received_at=self.at)


class PracticeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.agent = PracticeAgent(Path(tmp.name) / "practice.sqlite")
        self.prices = Prices()

    def check(self):
        return self.agent.check(client=self.prices, clock=lambda: self.prices.at)

    def start(self):
        self.agent.set_active(True)
        return self.check()

    def test_starts_paused_with_exactly_200_and_no_network(self):
        state = self.check()
        self.assertFalse(state["active"])
        self.assertEqual(state["cash"], "200")
        self.assertEqual(state["activity"], [])

    def test_buys_in_budget_and_accounts_for_both_sides_costs(self):
        state = self.start()
        self.assertEqual(Decimal(state["cash"]), 100)
        self.assertEqual(len(state["positions"]), 2)
        self.assertEqual([r["symbol"] for r in state["activity"] if r["action"] == "BUY"], ["ETHUSD", "BTCUSD"])
        self.assertGreater(Decimal(state["fees"]), 0)
        self.assertLess(Decimal(state["equity"]), 200)
        self.assertGreater(Decimal(state["equity"]), 198)
        for event in (e for e in state["activity"] if e["action"] == "BUY"):
            self.assertEqual(Decimal(event["spend"]), 50)
            self.assertAlmostEqual(Decimal(event["quantity"]) * Decimal(event["price"]) + Decimal(event["fee"]), Decimal(50))

    def test_repeated_check_and_process_restart_never_repeat_entries(self):
        state = self.start()
        before = state["cash"]
        self.assertEqual(len(self.check()["activity"]), len(SYMBOLS))
        self.agent = PracticeAgent(self.agent.path)
        self.prices.at += MINUTE
        state = self.check()
        self.assertEqual(state["cash"], before)
        self.assertEqual(len(state["positions"]), 2)
        self.assertEqual([r["action"] for r in state["activity"][:2]], ["WAIT", "WAIT"])

    def test_downtrend_waits_without_spending(self):
        self.prices.reverse = True
        state = self.start()
        self.assertEqual(state["cash"], "200")
        self.assertEqual(state["positions"], {})

    def test_stop_loss_sells_and_does_not_reenter_same_hour(self):
        self.start()
        self.prices.price = Decimal("149")
        self.prices.at += MINUTE
        state = self.check()
        self.assertEqual(state["positions"], {})
        sales = [r for r in state["activity"] if r["action"] == "SELL"]
        self.assertEqual(len(sales), 2)
        self.assertIn("fell 5%", sales[0]["reason"])
        self.prices.at += MINUTE
        state = self.check()
        self.assertEqual(state["positions"], {})
        self.assertLess(Decimal(state["cash"]), 200)

    def test_profit_limit_and_downtrend_sell(self):
        for price, reverse, reason in (("180", False, "rose 10%"), ("160", True, "average fell")):
            with self.subTest(reason=reason):
                self.setUp()
                self.start()
                self.prices.price = Decimal(price)
                self.prices.reverse = reverse
                self.prices.at += MINUTE
                state = self.check()
                self.assertEqual(state["positions"], {})
                self.assertIn(reason, next(r for r in state["activity"] if r["action"] == "SELL")["reason"])

    def test_total_loss_threshold_liquidates_and_halts(self):
        self.start()
        self.prices.price = Decimal("80")
        self.prices.at += MINUTE
        state = self.check()
        self.assertFalse(state["active"])
        self.assertEqual(state["positions"], {})
        self.assertLess(Decimal(state["cash"]), 160)

    def test_data_failure_keeps_wallet_and_pauses(self):
        before = self.start()
        self.prices.at += MINUTE
        self.prices.gap = True
        state = self.check()
        self.assertFalse(state["active"])
        self.assertEqual(state["cash"], before["cash"])
        self.assertEqual(state["positions"], before["positions"])
        self.assertEqual(state["activity"][0]["action"], "PAUSE")

    def test_liquidity_failure_rolls_back_all_simulations(self):
        original = self.prices.book_ticker
        self.prices.book_ticker = lambda symbol: replace(original(symbol), ask_size=Decimal("0.00001")) if symbol == "ETHUSD" else original(symbol)
        state = self.start()
        self.assertFalse(state["active"])
        self.assertEqual(state["cash"], "200")
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["fees"], "0")
        self.assertEqual(len(state["activity"]), 1)

    def test_pause_during_fetch_blocks_pending_decisions(self):
        self.prices.on_quote = lambda: self.agent.set_active(False)
        state = self.start()
        self.assertFalse(state["active"])
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["cash"], "200")

    def test_unfinished_bar_is_ignored(self):
        original = self.prices.klines
        def with_open(*args, **kwargs):
            bars = original(*args, **kwargs)
            bars.append(replace(bars[-1], open_time=60 * HOUR, close_time=61 * HOUR - 1,
                                close=Decimal("1000000")))
            return bars
        self.prices.klines = with_open
        observation = observe(self.prices, "BTCUSD", self.prices.at)
        self.assertLess(observation.fast, 200)

    def test_stale_or_crossed_or_wide_quotes_refuse(self):
        original = self.prices.book_ticker
        for changes in ({"source_received_at": 1}, {"ask": Decimal("200")}, {"bid": Decimal("0")}):
            with self.subTest(changes=changes):
                self.prices.book_ticker = lambda symbol: replace(original(symbol), **changes)
                with self.assertRaises(ValueError):
                    observe(self.prices, "BTCUSD", self.prices.at)

    def test_overlapping_checks_share_one_wallet_without_duplicate_entries(self):
        from concurrent.futures import ThreadPoolExecutor
        self.agent.set_active(True)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.check(), range(2)))
        state = self.agent.snapshot()
        self.assertEqual(Decimal(state["cash"]), 100)
        self.assertEqual(len(state["activity"]), len(SYMBOLS))
        self.assertTrue(all(r["active"] for r in results))

    def test_stale_history_and_closed_market_pause_without_spending(self):
        self.prices.at += 3 * HOUR
        state = self.start()
        self.assertFalse(state["active"])
        self.assertEqual(state["cash"], "200")
        self.prices.at = 60 * HOUR + 1000
        self.prices.symbol_status = lambda symbol: "BREAK"
        state = self.start()
        self.assertFalse(state["active"])
        self.assertIn("market is not open", state["message"])


if __name__ == "__main__":
    unittest.main()
