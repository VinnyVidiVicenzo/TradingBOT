"""Chronological opportunity learning, isolation, checkpoint and rollback gates."""

import json
import sqlite3
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from paper_trading import prediction
from paper_trading.practice import Observation
from . import test_practice_agent as fixtures


START = 100 * prediction.HOUR + 1000
FEE, SLIP = Decimal("0.004"), Decimal("0.001")


def observation(at, *, price="100", symbol="BTCUSD", bar=None):
    return Observation(symbol, at - 1000 if bar is None else bar, Decimal("101"), Decimal("100"),
                       Decimal(price), Decimal(price) + Decimal("0.01"), Decimal(100), Decimal(100), at)


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        prediction.initialize(self.db)

    def step(self, at, obs=None, *, fee=FEE):
        prediction.advance(self.db, [obs or observation(at)], at,
                           [{"symbol": "BTCUSD", "action": "WAIT", "reason": "Not enough evidence."}],
                           fee=fee, slippage=SLIP)
        return prediction.summary(self.db)

    def save(self, state):
        self.db.execute("UPDATE prediction_state SET body=? WHERE id=1", (json.dumps(state),))

    def test_predicts_wait_opportunity_before_learning_outcome(self):
        first = self.step(START)
        self.assertEqual(first["labels"], 0)
        row = first["recent"][0]
        self.assertEqual(row["action"], "WAIT")
        self.assertEqual(row["probability"], 0.5)
        self.assertEqual(row["candidate_probability"], 0.5)
        self.assertNotIn("outcome", row)
        self.assertEqual(row["training_labels"], 0)
        before = self.step(START + prediction.HOUR - 1)
        self.assertEqual(before["labels"], 0)
        self.assertTrue(all(w == 0 for w in before["candidate"]))

    def test_one_per_symbol_hour_not_every_minute_and_persists_unique_key(self):
        self.step(START)
        for i in range(1, 5):
            self.step(START + i * 60_000, observation(START + i * 60_000, bar=START - 1000))
        self.assertEqual(self.db.execute("SELECT count(*) FROM opportunities").fetchone()[0], 1)
        prediction.initialize(self.db)
        self.step(START + 300_000, observation(START + 300_000, bar=START - 1000))
        self.assertEqual(self.db.execute("SELECT count(*) FROM opportunities").fetchone()[0], 1)

    def test_later_quote_resolves_after_recorded_costs_not_new_fee_setting(self):
        self.step(START)
        after = self.step(START + prediction.HOUR, observation(START + prediction.HOUR, price="102"), fee=Decimal(0))
        resolved = next(e for e in after["recent"] if e["status"] == "resolved")
        expected = Decimal("102") * (1 - SLIP) * (1 - FEE) / (Decimal("100.01") * (1 + SLIP) * (1 + FEE)) - 1
        self.assertEqual(Decimal(resolved["net_return"]), expected)
        self.assertEqual(resolved["probability"], 0.5)
        self.assertEqual(resolved["outcome"], 1)
        self.assertEqual(after["labels"], 1)
        self.assertGreater(after["candidate"][0], 0)
        self.assertEqual(after["score"]["model"], 0.25)

    def test_flat_price_is_a_loss_after_costs(self):
        self.step(START)
        after = self.step(START + prediction.HOUR)
        self.assertEqual(after["positives"], 0)
        self.assertLess(after["candidate"][0], 0)
        self.assertEqual(after["regimes"]["rising"]["n"], 1)

    def test_offline_gap_expires_instead_of_learning_from_late_price(self):
        self.step(START)
        at = START + prediction.HOUR + prediction.GRACE + 1
        after = self.step(at, observation(at, price="200"))
        self.assertEqual(after["labels"], 0)
        self.assertEqual(after["counts"]["expired"], 1)
        self.assertNotIn("outcome", next(e for e in after["recent"] if e["status"] == "expired"))

    def test_stale_quotes_do_not_label_or_create_opportunities(self):
        self.step(START)
        at = START + prediction.HOUR
        after = self.step(at, replace(observation(at), received_at=at - 60_001))
        self.assertEqual(after["labels"], 0)
        self.assertEqual(after["counts"], {"pending": 1})

    def test_saved_feature_mismatch_is_refused_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            prediction.probability([0] * len(prediction.FEATURES), [1, 2])
        state = prediction._load(self.db)
        state["candidate"] = [0] * (len(prediction.FEATURES) - 1)
        self.save(state)
        self.assertIn("error", prediction.summary(self.db))

    def test_stale_hourly_context_cannot_create_training_rows(self):
        at = START
        self.step(at, observation(at, bar=at - 2 * prediction.HOUR))
        self.assertEqual(self.db.execute("SELECT count(*) FROM opportunities").fetchone()[0], 0)

    def test_baseline_probability_only_uses_already_resolved_outcomes(self):
        self.step(START)
        after = self.step(START + prediction.HOUR, observation(START + prediction.HOUR, price="102"))
        current = next(e for e in after["recent"] if e["status"] == "pending")
        old = next(e for e in after["recent"] if e["status"] == "resolved")
        self.assertEqual(old["baseline_probability"], 0.5)
        self.assertAlmostEqual(current["baseline_probability"], 2 / 3)

    def test_candidate_learns_a_predictable_relationship_without_unbounded_weights(self):
        weights = [0.0] * len(prediction.FEATURES)
        for i in range(600):
            sign = 1 if i % 2 else -1
            values = [1.0, float(sign)] + [0.0] * (len(prediction.FEATURES) - 2)
            weights = prediction.train(weights, values, int(sign > 0), i)
        self.assertGreater(prediction.probability(weights, [1, 1] + [0] * 7), 0.9)
        self.assertLess(prediction.probability(weights, [1, -1] + [0] * 7), 0.1)
        self.assertTrue(all(abs(w) <= 10 for w in weights))

    def trial(self):
        state = prediction._load(self.db)
        state["labels"] = prediction.TRAIN_SAMPLES
        state["candidate"][0] = 0.7
        prediction._checkpoint(self.db, state, START)
        return state

    def test_checkpoint_is_frozen_and_persisted_as_json_with_hash(self):
        state = self.trial()
        trial = state["trial"]
        self.assertEqual(trial["n"], 0)
        state["candidate"][0] = -0.8
        self.assertEqual(trial["weights"][0], 0.7)
        body = json.loads(self.db.execute("SELECT body FROM prediction_models WHERE model_id=?", (trial["id"],)).fetchone()[0])
        self.assertEqual(body["weights"][0], 0.7)
        self.assertEqual(len(body["sha256"]), 64)
        self.assertEqual(state["champion"]["id"], "baseline-0")

    def test_validation_requires_samples_time_better_error_and_positive_net_signals(self):
        for n, elapsed, model_loss, net, signals, passed in (
            (99, 8, 5, 1, 10, False), (100, 6, 5, 1, 10, False),
            (100, 8, 30, 1, 10, False), (100, 8, 5, -1, 10, False),
            (100, 8, 5, 1, 9, False), (100, 8, 5, 1, 10, True),
        ):
            with self.subTest(n=n, elapsed=elapsed, net=net, passed=passed):
                self.setUp()
                state = self.trial()
                state["trial"].update(n=n, model=model_loss, champion=25, baseline=24, signals=signals, net=net)
                selected = state["trial"]["id"]
                prediction._checkpoint(self.db, state, START + elapsed * 24 * prediction.HOUR)
                self.assertEqual(state["champion"]["id"] == selected, passed)

    def test_old_training_predictions_cannot_enter_later_challenger_validation(self):
        self.step(START)
        state = self.trial()
        self.save(state)
        after = self.step(START + prediction.HOUR)
        self.assertEqual(after["trial"]["n"], 0)
        self.assertEqual(after["labels"], prediction.TRAIN_SAMPLES + 1)

    def test_two_bad_champion_blocks_roll_back_and_cancel_stale_trial(self):
        state = self.trial()
        state["champion"] = {"id": "previously-promoted", "weights": [2.0] + [0.0] * 8}
        state["fallback"] = {"id": "baseline-0", "weights": [0.0] * 9}
        sample = {"probability": 0.99, "baseline_probability": 0.1, "fallback_probability": 0.5,
                  "condition": "falling", "trial_id": None, "model_id": "previously-promoted"}
        for i in range(prediction.DRIFT_BLOCK):
            prediction._score(self.db, state, sample, 0, -0.01, START + i)
        self.assertEqual(state["champion"]["id"], "previously-promoted")
        for i in range(prediction.DRIFT_BLOCK):
            prediction._score(self.db, state, sample, 0, -0.01, START + 100 + i)
        self.assertEqual(state["champion"]["id"], "baseline-0")
        self.assertEqual(state["rollbacks"], 1)
        self.assertIsNone(state["trial"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM prediction_events WHERE kind='ROLLBACK'").fetchone()[0], 1)


class PredictionIntegrationTests(unittest.TestCase):
    setUp = fixtures.PracticeTests.setUp
    start = fixtures.PracticeTests.start
    check = fixtures.PracticeTests.check

    def test_predictions_do_not_spend_wallet_or_change_learning_hours(self):
        state = self.start()
        self.assertEqual(Decimal(state["cash"]), 100)
        self.assertEqual(len(state["positions"]), 2)
        self.assertEqual(state["learning"]["hours"], 1)
        self.assertEqual(state["prediction"]["counts"], {"pending": 5})
        self.assertEqual(sorted(e["action"] for e in state["prediction"]["recent"]), ["BUY", "BUY", "WAIT", "WAIT", "WAIT"])

    def test_prediction_failure_rolls_back_only_prediction_work(self):
        def fail(db, *args, **kwargs):
            db.execute("UPDATE prediction_state SET body='broken' WHERE id=1")
            raise RuntimeError("synthetic model failure")
        with patch("paper_trading.prediction.advance", side_effect=fail):
            state = self.start()
        self.assertTrue(state["active"])
        self.assertEqual(Decimal(state["cash"]), 100)
        self.assertEqual(state["prediction"]["labels"], 0)
        self.assertIn("synthetic model failure", state["prediction_warning"])
        self.assertEqual(state["learning"]["hours"], 1)

    def test_restart_preserves_wallet_learner_and_forecast_rows(self):
        from paper_trading.practice import PracticeAgent
        first = self.start()
        self.agent = PracticeAgent(self.agent.path)
        second = self.agent.snapshot()
        self.assertEqual(second["cash"], first["cash"])
        self.assertEqual(second["positions"], first["positions"])
        self.assertEqual(second["learning"], first["learning"])
        self.assertEqual(second["prediction"], first["prediction"])


if __name__ == "__main__":
    unittest.main()
