"""Frozen swing decisions, cash accounting, restart isolation and candle replay."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from crypto_research.market_universe import SYMBOLS
from crypto_research.models import EXCHANGE_BINANCE_US, MarketBar, MarketQuote
from paper_trading import historical, research_costs, swing_strategy as swing, swing_backtest
from .test_learning_foundation import END, HistoryClient


def report():
    profile = research_costs.published_schedule(END)
    weights = [3.0] + [0.0] * (len(historical.FEATURES) - 1)
    return {"id": "test-report", "version": historical.VERSION, "features": list(historical.FEATURES),
            "dataset_sha256": "test-dataset", "cost_profile": profile,
            "tracks": {"24h": {"folds": [{"weights": weights, "training_last_label": END - 100 * historical.HOUR,
                                         "test_start": END - 50 * historical.HOUR}]}}}


def quotes(at=END, *, bid="100", score=0.9, gap="0.01", bar=None):
    return {s: {"bid": bid, "ask": str(Decimal(bid) + Decimal("0.01")), "bid_size": "1000", "ask_size": "1000",
                "trend_gap": gap, "score": score, "bar_time": at - historical.HOUR if bar is None else bar, "received_at": at} for s in SYMBOLS}


class PublicPrices:
    def __init__(self):
        self.at, self.price, self.on_quote = END, Decimal(175), None

    def symbol_status(self, symbol):
        return "TRADING"

    def klines(self, symbol, interval, limit):
        end = self.at // historical.HOUR * historical.HOUR
        result = []
        for i in range(75):
            value = Decimal(100 + i)
            at = end - (75 - i) * historical.HOUR
            result.append(MarketBar(EXCHANGE_BINANCE_US, symbol, interval, at, at + historical.HOUR - 1,
                                    value, value + 1, value - 1, value, Decimal(10)))
        return result

    def book_ticker(self, symbol):
        if self.on_quote:
            self.on_quote()
        return MarketQuote(EXCHANGE_BINANCE_US, symbol, self.price, self.price + Decimal("0.01"),
                           Decimal(1000), Decimal(1000), source_received_at=self.at)


class SwingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = swing.config_from_report(report())
        self.state = swing.initial("200", self.config)
        self.state["active"] = True

    def test_five_entries_share_cash_with_caps_and_complete_cost_accounting(self):
        events = swing.advance(self.state, quotes(), END)
        self.assertEqual(sum(e["action"] == "BUY" for e in events), 5)
        self.assertEqual(Decimal(self.state["cash"]), 0)
        self.assertEqual({Decimal(p["cost"]) for p in self.state["positions"].values()}, {Decimal(40)})
        self.assertGreater(Decimal(self.state["fees"]), 0)
        self.assertLess(Decimal(self.state["equity"]), 200)

    def test_public_rates_are_labeled_as_published_not_account_reported(self):
        profile = research_costs.published_schedule(END)
        self.assertEqual(profile["verification"], "published_schedule")
        self.assertEqual(research_costs.fee(profile), Decimal("0.0002"))
        self.assertTrue(all(v["maker"] == "0" for v in profile["fees"].values()))
        self.assertIn("unverified", profile["source"])

    def test_time_exit_does_not_reenter_same_check(self):
        swing.advance(self.state, quotes(), END)
        events = swing.advance(self.state, quotes(END + 24 * historical.HOUR), END + 24 * historical.HOUR)
        self.assertEqual(sum(e["action"] == "SELL" for e in events), 5)
        self.assertFalse(self.state["positions"])
        self.assertTrue(all("24-hour" in e["reason"] for e in events))

    def test_stop_target_and_trend_exits(self):
        for bid, gap, reason in (("94", "0.01", "fell 5%"), ("111", "0.01", "rose 10%"), ("100", "0", "trend reversed")):
            with self.subTest(reason=reason):
                self.setUp()
                swing.advance(self.state, quotes(), END)
                events = swing.advance(self.state, quotes(END + 60_000, bid=bid, gap=gap, bar=END - historical.HOUR), END + 60_000)
                self.assertFalse(self.state["positions"])
                self.assertTrue(all(reason in e["reason"] for e in events))

    def test_loss_limit_withholds_entries_and_retains_illiquid_exit_for_retry(self):
        swing.advance(self.state, quotes(), END)
        bad = quotes(END + 60_000, bid="50")
        bad["XRPUSD"]["bid_size"] = "0.000001"
        swing.advance(self.state, bad, END + 60_000)
        self.assertTrue(self.state["halted"])
        self.assertTrue(self.state["active"])
        self.assertEqual(set(self.state["positions"]), {"XRPUSD"})
        swing.advance(self.state, quotes(END + 120_000, bid="50"), END + 120_000)
        self.assertFalse(self.state["active"])
        self.assertFalse(self.state["positions"])

    def test_low_score_and_unchanged_hour_do_not_force_new_trades(self):
        events = swing.advance(self.state, quotes(score=0.59), END)
        self.assertTrue(all(e["action"] == "WAIT" for e in events))
        swing.advance(self.state, quotes(END + 60_000, bar=END - historical.HOUR), END + 60_000)
        self.assertFalse(self.state["positions"])


class SwingAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.agent = swing.SwingPracticeAgent(self.root / "swing_practice.sqlite")
        self.agent.initialize("200", report())
        research_costs.store(self.root / "research_costs.json", report()["cost_profile"])
        self.prices = PublicPrices()

    def start(self):
        self.agent.set_active(True)
        return self.agent.check(client=self.prices, clock=lambda: self.prices.at)

    def test_invalid_initial_amount_does_not_create_a_broken_session(self):
        path = self.root / "new_session.sqlite"
        agent = swing.SwingPracticeAgent(path)
        with self.assertRaises(ValueError):
            agent.initialize("not an amount", report())
        self.assertFalse(path.exists())
        agent.initialize("1000", report())
        self.assertEqual(agent.snapshot()["initial"], "1000")

    def test_restarts_preserve_frozen_model_capital_and_positions(self):
        before = self.start()
        self.assertEqual(len(before["positions"]), 5)
        again = swing.SwingPracticeAgent(self.agent.path)
        again.initialize("9999", report())
        after = again.snapshot()
        self.assertEqual(after["initial"], "200")
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(after["config"], before["config"])
        self.prices.at += 60_000
        after = again.check(client=self.prices, clock=lambda: self.prices.at)
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(sum(e["action"] == "BUY" for e in after["activity"]), 5)

    def test_pause_during_fetch_prevents_late_decisions(self):
        self.prices.on_quote = lambda: self.agent.set_active(False)
        state = self.start()
        self.assertFalse(state["active"])
        self.assertFalse(state["positions"])

    def test_new_cost_profile_blocks_entries_but_existing_exits_continue(self):
        before = self.start()
        self.prices.at += 60_000
        self.prices.price = Decimal(160)
        profile = report()["cost_profile"]
        for rates in profile["fees"].values():
            rates["taker"] = "0.001"
        research_costs.store(self.root / "research_costs.json", profile)
        after = self.agent.check(client=self.prices, clock=lambda: self.prices.at)
        self.assertFalse(after["positions"])
        self.assertEqual(after["config"], before["config"])
        self.prices.at += historical.HOUR
        after = self.agent.check(client=self.prices, clock=lambda: self.prices.at)
        self.assertFalse(after["positions"])
        self.assertTrue(all("Fee profile" in row["reason"] for row in after["waiting"].values()))

    def test_data_failure_pauses_without_touching_primary_wallet(self):
        primary = self.root / "practice_agent.sqlite"
        primary.write_bytes(b"primary wallet and learning")
        before = self.start()
        self.prices.at += 60_000
        self.prices.symbol_status = lambda symbol: "BREAK"
        after = self.agent.check(client=self.prices, clock=lambda: self.prices.at)
        self.assertFalse(after["active"])
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(primary.read_bytes(), b"primary wallet and learning")


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.config = swing.config_from_report(report())
        bars = list(HistoryClient().iter_klines("BTCUSD", "1h", start_time=END - 300 * historical.HOUR, end_time=END - 1))
        self.rows = []
        for symbol in SYMBOLS:
            for bar in bars:
                b = replace(bar, symbol=symbol)
                self.rows.append(historical.normalize(b, symbol, END - 300 * historical.HOUR, END))

    def test_portfolio_returns_include_both_fee_sides_and_liquidate_at_test_end(self):
        result = swing_backtest.simulate(self.rows, self.config, END - 100 * historical.HOUR, END)
        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["exits"], 0)
        self.assertGreater(Decimal(result["fees_index_units"]), 0)
        self.assertTrue(result["trade_ledger"])
        self.assertGreaterEqual(result["max_drawdown_pct"], 0)
        ledger = result["trade_ledger"]
        net_cash = Decimal(100) + sum((Decimal(e.get("proceeds", "0")) - Decimal(e.get("spend", "0")) for e in ledger), Decimal(0))
        self.assertAlmostEqual(float(net_cash), 100 * (1 + result["return_pct"] / 100), places=9)
        self.assertAlmostEqual(sum(Decimal(e["fee"]) for e in ledger), Decimal(result["fees_index_units"]), places=20)

    def test_chronological_portfolio_artifact_keeps_frozen_inputs_and_fee_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frozen = json.dumps({"version": 1, "bars": self.rows}).encode()
            digest = hashlib.sha256(frozen).hexdigest()
            (root / "datasets").mkdir()
            (root / "datasets" / (digest + ".json")).write_bytes(frozen)
            study = report()
            study.update(dataset_sha256=digest, dataset_file="datasets/" + digest + ".json", source={"end": END})
            study["tracks"]["24h"]["folds"][0].update(fold=1, test_end=END + 1)
            historical._atomic(root / "latest.json", study)
            result = swing_backtest.evaluate(root)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["cost_profile"]["verification"], "published_schedule")
            self.assertEqual(swing_backtest.latest(root)["id"], result["id"])
            self.assertEqual(len(list((root / "swing_studies").glob("*.json"))), 1)
            self.assertEqual([json.loads(s)["status"] for s in (root / "swing_attempts.jsonl").read_text().splitlines()], ["started", "completed"])

    def test_gap_withholds_entire_fold_instead_of_inventing_marks(self):
        rows = [r for r in self.rows if not (r["symbol"] == "ADAUSD" and r["at"] == END - 80 * historical.HOUR)]
        result = swing_backtest.simulate(rows, self.config, END - 100 * historical.HOUR, END)
        self.assertEqual(result["status"], "withheld_data_gap")
        self.assertIsNone(result["return_pct"])

    def test_same_hour_stop_and_target_uses_stop_and_flat_buy_hold_loses_costs(self):
        rows = copy.deepcopy(self.rows)
        for r in rows:
            if r["at"] >= END - 50 * historical.HOUR:
                r.update(open="100", close="100", high="120", low="80")
        result = swing_backtest.simulate(rows, self.config, END - 50 * historical.HOUR, END)
        exits = [e for e in result["trade_ledger"] if e["action"] == "SELL"]
        self.assertTrue(exits)
        self.assertIn("stop wins", exits[0]["reason"])
        hold = swing_backtest.simulate(rows, self.config, END - 50 * historical.HOUR, END, buy_hold=True)
        self.assertLess(hold["return_pct"], 0)
