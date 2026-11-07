"""Separate horizons, cost provenance, frozen evaluation, and physical premium risk."""
import copy
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from paper_trading import metals, prediction, research_costs, swing
from .test_opportunity_prediction import observation, START, FEE, SLIP
from . import test_practice_agent as fixtures


def costs(rate="0.004", observed=None):
    return research_costs.validate({"version": 1, "observed_at": observed,
        "fees": {s: {"maker": rate, "taker": rate} for s in research_costs.SYMBOLS}})


class SwingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        prediction.initialize(self.db)
        self.profile = costs()

    def step(self, at):
        swing.advance(self.db, [observation(at)], at, [], self.profile)

    def test_tracks_are_isolated_and_do_not_overlap_targets(self):
        self.step(START)
        self.step(START + prediction.HOUR)
        for name in ("4h", "24h"):
            scoped = swing.scoped(self.db, name, self.profile)
            self.assertEqual(scoped.execute("SELECT count(*) FROM opportunities").fetchone()[0], 1)
        self.step(START + 4 * prediction.HOUR)
        result = swing.summary(self.db, self.profile)
        self.assertEqual(result["tracks"]["4h"]["labels"], 1)
        self.assertEqual(result["tracks"]["24h"]["labels"], 0)
        self.assertEqual(prediction.summary(self.db)["labels"], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM opportunities").fetchone()[0], 0)

    def test_missing_horizon_expires_without_backfill(self):
        self.step(START)
        self.step(START + 25 * prediction.HOUR)
        tracks = swing.summary(self.db, self.profile)["tracks"]
        self.assertEqual(tracks["4h"]["labels"], 0)
        self.assertEqual(tracks["24h"]["labels"], 0)
        self.assertEqual(tracks["24h"]["counts"]["expired"], 1)

    def test_changed_costs_create_new_studies_and_preserve_old_rows(self):
        self.step(START)
        old = swing.scoped(self.db, "4h", self.profile)
        self.profile = costs("0.001")
        self.step(START + prediction.HOUR)
        new = swing.scoped(self.db, "4h", self.profile)
        self.assertNotEqual(old.prefix, new.prefix)
        self.assertEqual(old.execute("SELECT count(*) FROM opportunities").fetchone()[0], 1)
        self.assertEqual(new.execute("SELECT count(*) FROM opportunities").fetchone()[0], 1)

    def test_normalized_baselines_and_stress_use_recorded_outcomes(self):
        self.step(START)
        self.step(START + 4 * prediction.HOUR)
        report = swing.summary(self.db, self.profile)["tracks"]["4h"]["evaluation"]
        self.assertEqual(report["n"], 1)
        self.assertEqual(report["mean_pct"]["cash"], 0)
        self.assertLess(report["mean_pct"]["all"], 0)
        self.assertEqual(report["mean_pct"]["model"], 0)  # initial 0.5 estimate cannot select a long

    def test_table_scope_refuses_arbitrary_sql_identifiers(self):
        with self.assertRaises(ValueError):
            swing.ScopedDB(self.db, "swing_;drop table wallet")


class CostTests(unittest.TestCase):
    def test_identical_rates_have_same_identity_despite_timestamp_and_format(self):
        self.assertEqual(costs("0.0040", 1)["sha256"], costs("0.004", 2)["sha256"])
        self.assertNotEqual(costs("0.004")["sha256"], costs("0.001")["sha256"])

    def test_invalid_rates_and_missing_symbol_refuse(self):
        for value in ("NaN", "-0.001", "0.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                costs(value)
        data = costs()
        data["fees"].pop("ETHUSD")
        with self.assertRaises(ValueError):
            research_costs.validate(data)

    def test_profile_roundtrip_and_defaults_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fees.json"
            self.assertIn("not account verified", research_costs.load(path)["source"])
            stored = research_costs.store(path, costs("0.001"))
            self.assertEqual(research_costs.load(path), stored)

    def test_account_export_contains_rates_not_balances_or_authentication(self):
        from account_tools.cli import build_parser, _dispatch
        from .test_account_tools import transport_with, FakeResponse, ACCOUNT_PAYLOAD, TRADE_FEE_PAYLOAD
        from account_tools.client import BinanceUSAccountClient
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fees.json"
            args = build_parser().parse_args(["fees", "--symbols", "BTCUSD,ETHUSD", "--research-profile", str(target)])
            transport, _ = transport_with([FakeResponse(ACCOUNT_PAYLOAD), FakeResponse(TRADE_FEE_PAYLOAD)])
            client = BinanceUSAccountClient(transport)
            with patch("builtins.print"):
                _dispatch(args, client, "2026-11-07T00:00:00Z")
            exported = json.loads(target.read_text())
            self.assertEqual(set(exported["fees"]), {"BTCUSD", "ETHUSD"})
            self.assertNotIn("balances", exported)
        profile = research_costs.validate(exported)
        self.assertEqual(profile["assumed_symbols"], ["ADAUSD", "SOLUSD", "XRPUSD"])
        self.assertEqual(research_costs.fee(profile), Decimal("0.004"))


class PreservationTests(unittest.TestCase):
    setUp = fixtures.PracticeTests.setUp
    start = fixtures.PracticeTests.start
    check = fixtures.PracticeTests.check

    def test_swing_tracks_do_not_reset_wallet_or_current_learner(self):
        state = self.start()
        self.assertEqual(state["cash"], "100")
        self.assertEqual(state["learning"]["hours"], 1)
        self.assertEqual(state["swing"]["tracks"]["4h"]["counts"], {"pending": 5})
        self.assertEqual(state["swing"]["tracks"]["24h"]["counts"], {"pending": 5})


def bullion(description="American Silver Eagles", *, bid="32", spot="30"):
    return metals.lot({"metal": "silver", "description": description, "ounces": "10", "cost": "350",
                       "bid": bid, "spot": spot, "quote_date": "2026-11-07", "exit_cost": "5"})


class MetalsTests(unittest.TestCase):
    def test_inventory_uses_dealer_bid_and_total_cost_not_spot(self):
        summary = metals.inventory([bullion()], "silver")
        self.assertEqual(summary["resale_value"], Decimal(315))
        self.assertEqual(summary["pnl"], Decimal(-35))
        self.assertEqual(summary["break_even_bid"], Decimal("35.5"))

    def test_daily_purchase_lots_and_sold_inventory_stay_separate(self):
        first, second = bullion(), bullion("Generic bullion", bid="30")
        summary = metals.inventory([first, second], "silver")
        self.assertEqual(summary["ounces"], Decimal(20))
        first["sold"] = True
        self.assertEqual(metals.inventory([first, second], "silver")["ounces"], Decimal(10))

    def test_partial_sale_preserves_cost_basis_and_limits_hedge_to_remaining_inventory(self):
        original = [bullion()]
        result = metals.record_sale(original, 0, "3")
        self.assertEqual(original[0]["ounces"], "10")
        self.assertEqual(metals.inventory(result, "silver")["ounces"], Decimal(7))
        self.assertEqual(sum(Decimal(p["cost"]) for p in result), Decimal(350))
        self.assertEqual(sum(Decimal(p["exit_cost"]) for p in result), Decimal(5))
        self.assertEqual(metals.inventory(result, "silver")["cost"], Decimal(245))
        with self.assertRaises(ValueError):
            metals.scenario(result, "silver", "30", "-0.1", hedge={"ounces": "10", "strike": "30", "premium": "1", "expiry": "2027-11-07"})

    def test_put_can_offset_spot_loss_but_not_lost_premiums_and_costs(self):
        hedge = {"ounces": "10", "strike": "30", "premium": "1", "expiry": "2027-11-07"}
        normal = metals.scenario([bullion()], "silver", "30", "-0.2", hedge=hedge)
        compressed = metals.scenario([bullion()], "silver", "30", "-0.2", premium_change="-2", hedge=hedge)
        self.assertEqual(normal["put_payout"], Decimal(60))
        self.assertEqual(normal["hedge_cost"], Decimal(10))
        self.assertLess(normal["hypothetical_hedged_pnl"], 0)
        self.assertEqual(compressed["hypothetical_hedged_pnl"], normal["hypothetical_hedged_pnl"] - 20)

    def test_oversized_or_bad_hedge_refuses(self):
        with self.assertRaises(ValueError):
            metals.scenario([bullion()], "silver", "30", "-0.1", hedge={"ounces": "11", "strike": "30", "premium": "1", "expiry": "2027-11-07"})
        with self.assertRaises(ValueError):
            metals.scenario([bullion()], "silver", "30", "-1.1")

    def test_inventory_roundtrip_and_no_phantom_lots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            self.assertEqual(metals.load(path), [])
            lots = [bullion()]
            original = copy.deepcopy(lots)
            metals.save(path, lots)
            self.assertEqual(metals.load(path), original)
            self.assertEqual(lots, original)


if __name__ == "__main__":
    unittest.main()
