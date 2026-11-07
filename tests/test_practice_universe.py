"""Five-market coverage, bounded cash allocation, and existing-wallet continuity."""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from crypto_research.market_universe import SYMBOLS, LEGACY_SYMBOLS
from desktop_app.practice_status import entry_rows
from market_capture import service, execution
from paper_trading import prediction, research_costs
from paper_trading.practice import PracticeAgent, MINUTE, VERSION
from . import test_practice_agent as fixtures
from .test_market_capture import trade, depth
from .test_opportunity_prediction import observation, START
from .test_research_extensions import costs


class UniverseTests(unittest.TestCase):
    setUp = fixtures.PracticeTests.setUp
    start = fixtures.PracticeTests.start
    check = fixtures.PracticeTests.check

    def test_all_five_buy_signals_share_existing_cash_with_no_borrowing(self):
        self.prices.rising_symbols = set(SYMBOLS)
        state = self.start()
        self.assertTrue(state["active"])
        self.assertEqual(set(state["positions"]), set(SYMBOLS))
        self.assertEqual(Decimal(state["cash"]), 0)
        self.assertEqual({Decimal(p["cost"]) for p in state["positions"].values()}, {Decimal(40)})
        self.assertEqual(state["prediction"]["counts"], {"pending": 5})
        self.assertEqual(state["swing"]["tracks"]["4h"]["counts"], {"pending": 5})
        self.assertEqual(state["swing"]["tracks"]["24h"]["counts"], {"pending": 5})
        self.assertEqual({row["symbol"] for row in entry_rows(state, self.prices.at)}, set(SYMBOLS))
        self.prices.price = Decimal("80")
        self.prices.at += MINUTE
        stopped = self.check()
        self.assertFalse(stopped["active"])
        self.assertFalse(stopped["positions"])
        self.assertEqual(sum(e["action"] == "SELL" for e in stopped["activity"]), 5)

    def test_new_markets_use_remaining_cash_without_replacing_existing_positions_or_history(self):
        with patch("paper_trading.practice.SYMBOLS", LEGACY_SYMBOLS):
            before = self.start()
        with self.agent._connect() as db:
            state = self.agent._read(db)
            state["version"] = "hourly-trend-learning-v2"
            state["learning"]["hours"] = 59
            self.agent._save(db, state)
        self.agent = PracticeAgent(self.agent.path)
        self.assertEqual(self.agent.snapshot()["learning"]["hours"], 59)
        self.assertEqual(self.agent.snapshot()["version"], VERSION)
        self.prices.rising_symbols = set(SYMBOLS)
        self.prices.at += MINUTE
        after = self.check()
        for symbol in LEGACY_SYMBOLS:
            self.assertEqual(after["positions"][symbol], before["positions"][symbol])
        for symbol in ("SOLUSD", "ADAUSD", "XRPUSD"):
            self.assertEqual(after["positions"][symbol]["cost"], "33.33")
        self.assertEqual(Decimal(after["cash"]), Decimal("0.01"))
        self.assertEqual(after["learning"]["hours"], 59)
        self.assertEqual(after["prediction"]["counts"], {"pending": 5})
        self.assertEqual(sum(e["action"] == "BUY" for e in after["activity"]), 5)

    def test_insufficient_allocation_waits_without_prioritizing_first_coins(self):
        with self.agent._connect() as db:
            state = self.agent._read(db)
            state.update(cash="40", positions={"BTCUSD": {"quantity": "1", "entry": "160", "cost": "160"}})
            self.agent._save(db, state)
        self.prices.rising_symbols = set(SYMBOLS)
        # Four new signals receive $10 each; below this they must all wait.
        with self.agent._connect() as db:
            state = self.agent._read(db)
            state["cash"] = "39"
            self.agent._save(db, state)
        after = self.start()
        self.assertTrue(after["active"])
        self.assertEqual(set(after["positions"]), {"BTCUSD"})
        self.assertEqual(after["cash"], "39")
        row = next(r for r in entry_rows(after, self.prices.at) if r["symbol"] == "XRPUSD")
        self.assertIn("$10 practice minimum", row["blocker"])
        self.prices.at += MINUTE
        after = self.check()
        row = next(r for r in entry_rows(after, self.prices.at) if r["symbol"] == "XRPUSD")
        self.assertIn("$10 practice minimum", row["blocker"])

    def test_unavailable_new_market_pauses_without_partial_simulations(self):
        before = self.start()
        self.prices.at += MINUTE
        self.prices.symbol_status = lambda symbol: "BREAK" if symbol == "XRPUSD" else "TRADING"
        after = self.check()
        self.assertFalse(after["active"])
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(after["cash"], before["cash"])
        self.assertIn("XRPUSD", after["message"])

    def test_new_coin_features_are_neutral_without_changing_legacy_meanings(self):
        for symbol, expected in (("BTCUSD", -1.0), ("ETHUSD", 1.0), ("SOLUSD", 0.0), ("ADAUSD", 0.0), ("XRPUSD", 0.0)):
            values = prediction.features(observation(START, symbol=symbol), START)
            self.assertEqual(len(values), len(prediction.FEATURES))
            self.assertEqual(values[-1], expected)

    def test_legacy_fee_import_retains_explicit_fallback_through_save(self):
        legacy = {"version": 1, "observed_at": START,
                  "fees": {s: {"maker": "0.001", "taker": "0.001"} for s in LEGACY_SYMBOLS}}
        profile = research_costs.validate(legacy)
        self.assertEqual(profile["assumed_symbols"], ["ADAUSD", "SOLUSD", "XRPUSD"])
        self.assertEqual(research_costs.fee(profile), Decimal("0.004"))
        path = self.agent.path.parent / "costs.json"
        research_costs.store(path, profile)
        self.assertEqual(research_costs.load(path), profile)
        self.assertNotEqual(profile["sha256"], costs("0.001")["sha256"])

    def test_new_market_frames_and_execution_are_supported(self):
        for symbol in ("SOLUSD", "ADAUSD", "XRPUSD"):
            with self.subTest(symbol=symbol):
                packet = json.loads(trade())
                packet["stream"], packet["data"]["s"] = symbol.lower() + "@aggTrade", symbol
                self.assertEqual(service.normalize(json.dumps(packet))[:2], (symbol, "trade"))
                quote = {"stream": symbol.lower() + "@bookTicker", "data": {"s": symbol, "u": 1, "b": "100", "a": "100.01", "B": "10", "A": "10"}}
                self.assertEqual(service.normalize(json.dumps(quote))[:2], (symbol, "quote"))
                path = self.agent.path.parent / (symbol + ".sqlite")
                store = service.Store(path)
                try:
                    for i, at in enumerate((1000, 1100, 61_100)):
                        book = json.loads(depth(i + 1))
                        book["stream"] = symbol.lower() + "@depth20@100ms"
                        raw = json.dumps(book)
                        self.assertEqual(service.normalize(raw)[:2], (symbol, "depth"))
                        store.append(at, "test", symbol, "depth", None, raw)
                finally:
                    store.close()
                self.assertEqual(execution.replay(path, symbol, "50")["status"], "estimated_full_fill")

    def test_default_fee_export_covers_all_five_without_assumed_rates(self):
        from account_tools.cli import build_parser, _dispatch
        from account_tools.client import BinanceUSAccountClient
        from .test_account_tools import transport_with, FakeResponse, ACCOUNT_PAYLOAD
        target = self.agent.path.parent / "reported.json"
        args = build_parser().parse_args(["fees", "--research-profile", str(target)])
        reported = [{"symbol": symbol, "makerCommission": "0.001", "takerCommission": "0.002"} for symbol in SYMBOLS]
        transport, _ = transport_with([FakeResponse(ACCOUNT_PAYLOAD), FakeResponse(reported)])
        with patch("builtins.print"):
            _dispatch(args, BinanceUSAccountClient(transport), "2026-11-07T00:00:00Z")
        profile = research_costs.load(target)
        self.assertEqual(set(profile["fees"]), set(SYMBOLS))
        self.assertEqual(profile["assumed_symbols"], [])
        self.assertEqual(research_costs.fee(profile), Decimal("0.002"))
