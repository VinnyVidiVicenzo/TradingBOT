"""Public market context and matched forward trials; no live services."""

import json
import unittest
from dataclasses import replace
from decimal import Decimal

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.binanceus.transport import PublicTransport
from crypto_research.errors import ConfigError
from paper_trading import market_signals
from paper_trading.practice import Observation, observe
from . import test_practice_agent as fixtures
from .test_public_client import FakeOpener, FakeResponse


def trades(at, *, buying=True, count=20):
    return [{"a": 100 + i, "p": "160", "q": "1", "m": not buying,
             "T": at - 20_000 + i} for i in range(count)]


def book():
    return {"bids": [["160", "10"], ["159.9", "10"]],
            "asks": [["160.01", "10"], ["160.1", "10"]]}


class MarketPrices(fixtures.Prices):
    buying = True
    count = 20

    def aggregate_trades(self, symbol, **kwargs):
        return trades(self.at, buying=self.buying, count=self.count)

    def depth(self, symbol, limit):
        return book()


class MarketParsingTests(unittest.TestCase):
    def setUp(self):
        self.at = 100_000_000

    def sample(self, **kwargs):
        return market_signals.summarize(trades(self.at, **kwargs), book(), self.at)

    def test_aggressor_side_and_notional_are_weighted_by_value(self):
        rows = trades(self.at, count=2)
        rows[1].update(m=True, q="3")
        result = market_signals.summarize(rows, book(), self.at)
        self.assertEqual(Decimal(result["buy_share"]), Decimal("0.25"))
        self.assertEqual(Decimal(result["notional"]), Decimal(640))
        self.assertEqual(Decimal(result["largest_trade"]), Decimal(480))
        self.assertEqual(len(result["sample_sha256"]), 64)

    def test_partial_empty_or_stale_pages_are_not_ready(self):
        self.assertEqual(self.sample(count=1000)["status"], "partial")
        self.assertEqual(self.sample(count=0)["status"], "no_recent_trades")
        rows = trades(self.at)
        for row in rows:
            row["T"] -= 120_000
        self.assertEqual(market_signals.summarize(rows, book(), self.at)["status"], "no_recent_trades")

    def test_discontinuous_ids_wrong_maker_and_nonfinite_values_refuse(self):
        for changes in ({"a": 999}, {"m": "false"}, {"p": "NaN"}, {"q": "-1"}, {"T": self.at + 1}):
            with self.subTest(changes=changes):
                rows = trades(self.at)
                rows[1].update(changes)
                with self.assertRaises((ValueError, ConfigError)):
                    market_signals.summarize(rows, book(), self.at)

    def test_invalid_or_crossed_depth_refuses(self):
        for bids in ([], [["170", "1"]], [["160", "-1"]], [["159", "1"], ["160", "1"]]):
            with self.subTest(bids=bids), self.assertRaises(ValueError):
                market_signals.summarize(trades(self.at), {**book(), "bids": bids}, self.at)

    def test_gate_detects_conflicts_costs_and_staleness(self):
        sample = self.sample()
        sample.update(requested_at=self.at, trend="rising", volatile=False, volume_ratio="1")
        observation = Observation("BTCUSD", self.at - 1, Decimal(170), Decimal(160),
                                  Decimal(160), Decimal("160.01"), Decimal(100), Decimal(100), self.at, sample)
        allowed, _ = market_signals.gate(observation, self.at, Decimal(50))
        self.assertTrue(allowed)
        evidence = market_signals.explanation(observation, self.at, Decimal("0.004"), Decimal("0.001"), Decimal(50))
        self.assertGreater(Decimal(evidence["roundtrip_cost_pct"]), Decimal("0.9"))
        self.assertEqual(evidence["signal_role"], "comparison_only")
        for changes in ({"buy_share": "0.2"}, {"buy_depth": "1"}, {"volume_ratio": "0.5"},
                        {"volatile": True}, {"trade_count": 2}, {"status": "partial"}):
            with self.subTest(changes=changes):
                self.assertFalse(market_signals.gate(replace(observation, market={**sample, **changes}), self.at, Decimal(50))[0])
        self.assertFalse(market_signals.gate(observation, self.at + 61_000, Decimal(50))[0])

    def test_optional_failure_is_explicit_and_timestamp_is_actual_receipt(self):
        class Broken:
            def aggregate_trades(self, *args, **kwargs):
                raise RuntimeError("offline")
        self.assertEqual(market_signals.collect(Broken(), "BTCUSD", self.at)["status"], "unavailable")
        prices = MarketPrices()
        result = market_signals.collect(prices, "BTCUSD", prices.at, clock=lambda: prices.at + 1000)
        self.assertEqual(result["requested_at"], prices.at)
        self.assertEqual(result["sampled_at"], prices.at + 1000)

    def test_public_client_uses_bounded_public_get(self):
        opener = FakeOpener([FakeResponse(trades(self.at))])
        client = BinanceUSPublicClient(PublicTransport(opener=opener))
        self.assertEqual(len(client.aggregate_trades("BTCUSD", start_time=self.at - 1000, end_time=self.at)), 20)
        self.assertIn("/api/v3/aggTrades?", opener.urls[0])
        self.assertEqual(opener.methods, ["GET"])
        with self.assertRaises(ConfigError):
            client.aggregate_trades("BTCUSD", start_time=0, end_time=1, limit=1001)
        self.assertEqual(len(opener.urls), 1)


class MarketIntegrationTests(unittest.TestCase):
    setUp = fixtures.PracticeTests.setUp
    start = fixtures.PracticeTests.start
    check = fixtures.PracticeTests.check

    def test_filter_blocks_trial_entries_without_changing_actual_strategy(self):
        self.prices = MarketPrices()
        self.prices.buying = False
        state = self.start()
        self.assertEqual(Decimal(state["cash"]), 100)
        comparison = state["market_trial"]
        self.assertEqual(Decimal(comparison["wallets"]["reference"]["wallet"]["cash"]), 100)
        self.assertEqual(Decimal(comparison["wallets"]["signals"]["wallet"]["cash"]), 200)
        self.assertEqual(state["learning"]["hours"], 1)
        self.assertEqual(len(state["activity"]), 5)
        self.assertIn("55%", next(r for r in state["activity"] if r["action"] == "BUY")["evidence"]["signal_reason"])

    def test_matched_comparison_has_same_costs_when_filter_supports_entry(self):
        self.prices = MarketPrices()
        state = self.start()
        ref, enhanced = (state["market_trial"]["wallets"][key] for key in ("reference", "signals"))
        self.assertEqual(ref["wallet"]["fees"], enhanced["wallet"]["fees"])
        self.assertEqual(ref["return_pct"], enhanced["return_pct"])
        self.assertLess(Decimal(ref["return_pct"]), 0)
        with self.agent._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM market_samples").fetchone()[0], 5)

    def test_partial_sample_withholds_comparison_but_preserves_primary_trades(self):
        self.prices = MarketPrices()
        self.prices.count = 1000
        state = self.start()
        self.assertIsNone(state["market_trial"])
        self.assertTrue(state["active"])
        self.assertEqual(Decimal(state["cash"]), 100)

    def test_upgrade_keeps_existing_wallet_and_learning_hours(self):
        before = self.start()
        with self.agent._connect() as db:
            state = self.agent._read(db)
            state.pop("market_trial")
            state["learning"]["hours"] = 17
            self.agent._save(db, state)
        self.prices = MarketPrices()
        self.prices.at += fixtures.MINUTE
        after = self.check()
        self.assertEqual(after["cash"], before["cash"])
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(after["learning"]["hours"], 17)
        self.assertIsNotNone(after["market_trial"])
        saved = json.loads(json.dumps(after["market_trial"]))
        self.assertEqual(self.agent.snapshot()["market_trial"], saved)

    def test_primary_exits_still_work_when_market_samples_are_missing(self):
        self.start()
        self.prices.price = Decimal("149")
        self.prices.at += fixtures.MINUTE
        state = self.check()
        self.assertEqual(state["positions"], {})
        self.assertEqual(len([row for row in state["activity"] if row["action"] == "SELL"]), 2)


if __name__ == "__main__":
    unittest.main()
