"""Prospective outcomes, frozen trials and protection of the active wallet."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crypto_research.market_universe import SYMBOLS
from paper_trading import historical, swing_learning as learning, swing_strategy as policy
from .test_swing_strategy import report, quotes
from . import test_swing_strategy as fixtures
from .test_learning_foundation import END


def observed(at=END, *, bid="100"):
    result = quotes(at, bid=bid, bar=at // learning.HOUR * learning.HOUR - learning.HOUR)
    for q in result.values():
        q["features"] = [1.0] + [0.0] * (len(historical.FEATURES) - 1)
    return result


class LearningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "learning.sqlite"
        self.config = policy.config_from_report(report())

    def step(self, at=END, *, bid="100", allowed=True):
        learning.record(self.path, self.config, observed(at, bid=bid), at,
                        [{"symbol": s, "action": "WAIT", "reason": "Below entry threshold"} for s in SYMBOLS],
                        "1000", entries_allowed=allowed)
        return learning.summary(self.path, self.config)

    def read(self):
        db = sqlite3.connect(self.path)
        try:
            return json.loads(db.execute("SELECT body FROM swing_learning_state").fetchone()[0])
        finally:
            db.close()

    def save(self, state):
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute("UPDATE swing_learning_state SET body=?", (json.dumps(state),))
        finally:
            db.close()

    def test_waits_create_future_examples_once_per_coin_hour_without_training_early(self):
        before = copy.deepcopy(self.config)
        summary = self.step()
        self.step(END + 60_000)
        self.assertEqual(summary["counts"], {"pending": 5})
        self.assertEqual(self.read()["labels"], 0)
        self.assertEqual(self.read()["candidate"], self.config["weights"])
        self.assertEqual(before, self.config)
        db = sqlite3.connect(self.path)
        try:
            rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM swing_examples")]
        finally:
            db.close()
        self.assertTrue(all(r["action"] == "WAIT" and r["due"] == END + learning.DAY for r in rows))
        self.assertTrue(all("outcome" not in r for r in rows))

    def test_later_outcomes_train_candidate_and_save_observed_downside(self):
        self.step()
        self.step(END + learning.HOUR, bid="95")
        summary = self.step(END + learning.DAY, bid="102")
        self.assertEqual(summary["labels"], 5)
        self.assertNotEqual(self.read()["candidate"], self.config["weights"])
        db = sqlite3.connect(self.path)
        try:
            row = json.loads(db.execute("SELECT body FROM swing_examples WHERE at=? LIMIT 1", (END,)).fetchone()[0])
        finally:
            db.close()
        self.assertEqual(row["training_labels_at_prediction"], 0)
        self.assertEqual(row["min_bid"], "95")
        self.assertGreater(float(row["net_return"]), 0)
        self.assertLess(float(row["worst_observed_net"]), 0)

    def test_expired_samples_never_receive_backfilled_outcomes(self):
        self.step()
        summary = self.step(END + learning.DAY + learning.GRACE + 1, bid="200")
        self.assertEqual(summary["labels"], 0)
        self.assertEqual(summary["counts"]["expired"], 5)
        self.assertEqual(self.read()["candidate"], self.config["weights"])

    def test_invalid_features_and_backwards_clock_are_rejected(self):
        bad = observed()
        bad["BTCUSD"]["features"] = [1.0]
        with self.assertRaises(ValueError):
            learning.record(self.path, self.config, bad, END, [], "1000")
        self.assertFalse(self.path.exists())
        self.step()
        with self.assertRaisesRegex(ValueError, "backwards"):
            self.step(END - 1000)

    def test_stale_costs_resolve_pending_but_create_no_new_examples(self):
        self.step()
        result = self.step(END + learning.DAY, allowed=False)
        self.assertEqual(result["labels"], 5)
        self.assertEqual(result["counts"], {"resolved": 5})
        self.assertIn("fees", result["message"])

    def freeze(self):
        if not self.path.exists():
            self.step()
        state = self.read()
        state.update(labels=100, training_days=list(range(7)))
        state["candidate"][0] = 2.0
        self.save(state)
        self.step(END + 60_000)
        return self.read()

    def test_checkpoint_requires_days_and_starts_matched_trials_on_later_quotes(self):
        self.step()
        state = self.read()
        state.update(labels=100, training_days=list(range(6)))
        self.save(state)
        self.assertIsNone(self.step(END + 30_000)["trial"])
        state = self.freeze()
        self.assertIsNone(state["trial"]["wallets"])
        self.step(END + 120_000)
        trial = self.read()["trial"]
        self.assertEqual(trial["ends_at"] - trial["started_at"], 35 * learning.DAY)
        self.assertEqual({w["initial"] for w in trial["wallets"].values()}, {"1000"})
        self.assertEqual(trial["wallets"]["reference"]["config"]["weights"], self.config["weights"])
        self.assertEqual(trial["wallets"]["candidate"]["config"]["weights"], trial["weights"])
        self.assertEqual(trial["wallets"]["stress"]["config"]["profile"]["fees"]["BTCUSD"]["taker"], "0.0004")

    def test_frozen_checkpoint_stays_unchanged_while_candidate_keeps_training(self):
        state = self.freeze()
        frozen = list(state["trial"]["weights"])
        self.step(END + learning.HOUR)
        self.step(END + learning.DAY + learning.HOUR)
        after = self.read()
        self.assertEqual(after["trial"]["weights"], frozen)
        self.assertNotEqual(after["candidate"], frozen)
        db = sqlite3.connect(self.path)
        try:
            model = json.loads(db.execute("SELECT body FROM swing_learning_models").fetchone()[0])
        finally:
            db.close()
        self.assertEqual(model["weights"], frozen)

    def test_unstarted_trial_waits_for_valid_fees_without_claiming_a_comparison(self):
        self.freeze()
        self.step(END + 120_000, allowed=False)
        trial = self.read()["trial"]
        self.assertIsNone(trial["started_at"])
        self.assertIsNone(trial["wallets"])
        self.assertTrue(trial["costs_valid"])
        self.step(END + 180_000)
        self.assertEqual(self.read()["trial"]["started_at"], END + 180_000)

    def trial(self):
        wallets = {name: policy.initial("1000", self.config) for name in ("reference", "candidate", "stress", "buy_hold")}
        wallets["candidate"].update(equity="1100", drawdown="0.005", exits=12)
        wallets["stress"].update(equity="1050")
        wallets["buy_hold"].update(equity="1080")
        return {"id": "test", "frozen_at": END - 1, "started_at": END, "ends_at": END + 35 * learning.DAY,
                "weights": list(self.config["weights"]), "wallets": wallets, "scored": 140,
                "scored_coins": {s: 28 for s in SYMBOLS}, "next_scored_at": {},
                "days": {str(d): {"n": 5, "advantage": 0.1} for d in range(1, 29)},
                "last_shadow_at": END + 35 * learning.DAY - 60_000, "max_gap_ms": 60_000,
                "benchmark_complete": True, "benchmark_started": True, "costs_valid": True, "entries_allowed": True}

    def test_validation_scoring_does_not_count_overlapping_targets(self):
        trial = self.trial()
        trial.update(scored=0, days={}, scored_coins={})
        row = {"trial_id": "test", "symbol": "BTCUSD", "predicted_at": END + learning.HOUR,
               "due": END + 25 * learning.HOUR, "reference_probability": 0.8, "trial_probability": 0.2, "outcome": 0}
        learning._score(trial, row, row["due"] + 60_000)
        overlap = {**row, "predicted_at": END + 2 * learning.HOUR, "due": END + 26 * learning.HOUR}
        learning._score(trial, overlap, overlap["due"] + 60_000)
        self.assertEqual(trial["scored"], 1)
        self.assertEqual(trial["next_scored_at"]["BTCUSD"], row["due"] + 60_000)

    def test_qualification_requires_weekly_evidence_returns_cost_stress_and_coverage(self):
        trial = self.trial()
        result = learning._finish(trial)
        self.assertTrue(result["qualified"], result["reasons"])
        self.assertEqual(result["complete_weeks"], 4)
        for changes in ({"costs_valid": False}, {"scored": 10}, {"max_gap_ms": 3 * learning.HOUR}, {"benchmark_complete": False}):
            with self.subTest(changes=changes):
                self.assertFalse(learning._finish({**trial, **changes})["qualified"])
        trial["wallets"]["stress"]["equity"] = "999"
        self.assertFalse(learning._finish(trial)["qualified"])

    def test_fixed_window_is_assessed_once_and_does_not_promote_current_model(self):
        self.step()
        original = copy.deepcopy(self.config)
        state = self.read()
        state["trial"] = self.trial()
        state["trial"]["capital"] = "1000"
        self.save(state)
        self.step(END + 35 * learning.DAY + 1)
        self.step(END + 35 * learning.DAY + 60_001)
        self.assertEqual(self.config, original)
        self.assertIsNone(self.read()["trial"])
        self.assertEqual(self.read()["qualified"], 1)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM swing_learning_events WHERE kind='VALIDATION'").fetchone()[0], 1)
        finally:
            db.close()


class AgentIsolationTests(unittest.TestCase):
    setUp = fixtures.SwingAgentTests.setUp
    start = fixtures.SwingAgentTests.start

    def test_learning_failure_cannot_rollback_decisions_or_change_frozen_wallet_config(self):
        before = self.agent.snapshot()["config"]
        with patch("paper_trading.swing_strategy.swing_learning.record", side_effect=RuntimeError("synthetic learner failure")):
            state = self.start()
        self.assertTrue(state["active"])
        self.assertEqual(len(state["positions"]), 5)
        self.assertEqual(state["config"], before)
        self.assertIn("synthetic learner failure", state["learning"]["error"])

    def test_paused_wallet_does_not_backfill_learning_and_restarts_keep_new_examples(self):
        self.agent.check(client=self.prices, clock=lambda: self.prices.at)
        self.assertFalse(self.agent.learning_path.exists())
        first = self.start()
        self.assertEqual(first["learning"]["counts"], {"pending": 5})
        again = policy.SwingPracticeAgent(self.agent.path)
        self.assertEqual(again.snapshot()["learning"], first["learning"])
