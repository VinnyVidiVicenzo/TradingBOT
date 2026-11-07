"""Forward-only adaptation gates and fixed-risk integration, without network."""

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from paper_trading import learning
from paper_trading.practice import VERSION, initial_state
from . import test_practice_agent as fixtures

HOUR, MINUTE = fixtures.HOUR, fixtures.MINUTE


def make_wallet():
    state = initial_state()
    state.pop("learning")
    return state


class LearningGateTests(unittest.TestCase):
    def setUp(self):
        self.learner = learning.initial_learning()
        self.records = []

    def advance(self, at, *, bar_time=None):
        observations = [SimpleNamespace(bar_time=at if bar_time is None else bar_time)] * 2
        learning.advance(self.learner, observations, at, make_wallet=make_wallet,
                         simulate=lambda *args: None,
                         record=lambda action, reason, **details: self.records.append((action, reason, details)))

    def ready(self, phase="discovery"):
        self.learner.update(phase=phase, challenger="selective" if phase == "validation" else None)
        self.advance(0)
        self.learner.update(hours=167, last_hour=0, started_at=0)
        for key, portfolio in self.learner["portfolios"].items():
            portfolio["exits"] = 10
            portfolio["wallet"]["equity"] = "205" if key == "selective" else "202"
            portfolio["drawdown"] = "0.01"

    def test_minute_checks_and_offline_time_do_not_become_training_hours(self):
        for i in range(60):
            self.advance(i * MINUTE, bar_time=HOUR)
        self.assertEqual(self.learner["hours"], 1)
        self.advance(30 * 24 * HOUR, bar_time=30 * 24 * HOUR)
        self.assertEqual(self.learner["hours"], 2)
        self.assertEqual(self.learner["active_rule"], "standard")

    def test_discovery_selects_but_does_not_promote_and_resets_trial_wallets(self):
        self.ready()
        self.advance(168 * HOUR)
        self.assertEqual(self.learner["phase"], "validation")
        self.assertEqual(self.learner["challenger"], "selective")
        self.assertEqual(self.learner["active_rule"], "standard")
        self.assertEqual(self.learner["hours"], 0)
        self.assertEqual(set(self.learner["portfolios"]), {"standard", "selective"})
        for p in self.learner["portfolios"].values():
            self.assertEqual(p["wallet"]["cash"], "200")
            self.assertEqual(p["wallet"]["positions"], {})
            self.assertEqual(p["exits"], 0)

    def test_insufficient_completed_trades_cannot_select_challenger(self):
        self.ready()
        self.learner["portfolios"]["selective"]["exits"] = 9
        self.advance(168 * HOUR)
        self.assertEqual(self.learner["phase"], "discovery")
        self.assertEqual(self.learner["active_rule"], "standard")

    def test_validation_promotes_only_after_new_time_and_sufficient_samples(self):
        self.ready("validation")
        self.learner["portfolios"]["selective"]["exits"] = 9
        self.advance(168 * HOUR)
        self.assertEqual(self.learner["active_rule"], "standard")
        self.learner["portfolios"]["selective"]["exits"] = 10
        self.advance(169 * HOUR)
        self.assertEqual(self.learner["active_rule"], "selective")
        self.assertEqual(self.learner["promotions"], 1)
        self.assertEqual(self.learner["phase"], "discovery")
        self.assertTrue(any(r[0] == "ADAPT" for r in self.records))

    def test_apparent_gain_without_cash_edge_or_with_more_drawdown_is_rejected(self):
        for equity, drawdown in (("199", "0.01"), ("202.5", "0.01"), ("205", "0.02")):
            with self.subTest(equity=equity, drawdown=drawdown):
                self.learner = learning.initial_learning()
                self.ready("validation")
                candidate = self.learner["portfolios"]["selective"]
                candidate["wallet"]["equity"] = equity
                candidate["drawdown"] = drawdown
                self.advance(168 * HOUR)
                self.assertEqual(self.learner["active_rule"], "standard")
                self.assertEqual(self.learner["promotions"], 0)

    def test_halted_trial_is_not_promoted(self):
        self.ready("validation")
        self.learner["portfolios"]["selective"]["wallet"]["active"] = False
        self.advance(168 * HOUR)
        self.assertEqual(self.learner["active_rule"], "standard")
        self.assertEqual(self.learner["phase"], "discovery")

    def test_validation_challenger_is_frozen_and_does_not_use_third_rule(self):
        self.ready("validation")
        self.assertNotIn("very_selective", self.learner["portfolios"])
        self.advance(168 * HOUR)
        self.assertEqual(self.learner["active_rule"], "selective")

    def test_actual_outcomes_include_losses_and_net_amounts(self):
        learning.actual_outcome(self.learner, "BUY", {})
        learning.actual_outcome(self.learner, "SELL", {"realized_pnl": "-2.5"})
        learning.actual_outcome(self.learner, "SELL", {"realized_pnl": "1"})
        self.assertEqual(self.learner["actual_exits"], 2)
        self.assertEqual(self.learner["actual_wins"], 1)
        self.assertEqual(Decimal(self.learner["actual_pnl"]), Decimal("-1.5"))


class LearningIntegrationTests(unittest.TestCase):
    setUp = fixtures.PracticeTests.setUp
    start = fixtures.PracticeTests.start
    check = fixtures.PracticeTests.check

    def test_shadow_portfolios_never_spend_actual_wallet_cash(self):
        state = self.start()
        self.assertEqual(Decimal(state["cash"]), 100)
        self.assertEqual(len(state["positions"]), 2)
        self.assertEqual(len(state["learning"]["portfolios"]), 3)
        for p in state["learning"]["portfolios"].values():
            self.assertEqual(Decimal(p["wallet"]["cash"]), 100)
        self.assertEqual(len(state["activity"]), 5)

    def test_learning_and_outcomes_survive_restart_without_added_capital(self):
        from paper_trading.practice import PracticeAgent
        self.start()
        self.prices.price = Decimal("149")
        self.prices.at += MINUTE
        state = self.check()
        saved = state["learning"]
        self.assertEqual(saved["actual_exits"], 2)
        self.assertLess(Decimal(saved["actual_pnl"]), 0)
        self.agent = PracticeAgent(self.agent.path)
        restored = self.agent.snapshot()
        self.assertEqual(restored["learning"], saved)
        self.assertEqual(restored["cash"], state["cash"])

    def test_old_wallet_migration_keeps_cash_holdings_and_fees(self):
        before = self.start()
        with self.agent._connect() as db:
            old = self.agent._read(db)
            old["version"] = "hourly-trend-v1"
            old.pop("learning")
            db.execute("UPDATE wallet SET body=? WHERE id=1", (json.dumps(old),))
        self.agent.set_active(False)
        after = self.agent.snapshot()
        self.assertEqual(after["version"], VERSION)
        self.assertEqual(after["cash"], before["cash"])
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(after["fees"], before["fees"])
        self.assertEqual(after["learning"]["actual_exits"], 0)

    def test_adapted_rule_changes_entries_but_not_budget_or_stop(self):
        from unittest.mock import patch
        from paper_trading.practice import Observation
        with self.agent._connect() as db:
            state = self.agent._read(db)
            state["learning"]["active_rule"] = "very_selective"
            self.agent._save(db, state)
        observation = Observation("BTCUSD", self.prices.at - 1, Decimal("100.5"), Decimal("100"),
                                  Decimal("100"), Decimal("100.01"), Decimal("100"), Decimal("100"), self.prices.at)
        from dataclasses import replace
        with patch("paper_trading.practice.observe", side_effect=lambda client, symbol, *a, **kw: replace(observation, symbol=symbol)):
            state = self.start()
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["cash"], "200")

    def test_shadow_liquidity_failure_does_not_rollback_actual_decision_or_spend_trial_cash(self):
        from dataclasses import replace
        from unittest.mock import patch
        from paper_trading.practice import Observation
        with self.agent._connect() as db:
            state = self.agent._read(db)
            state["learning"]["active_rule"] = "very_selective"
            self.agent._save(db, state)
        btc = Observation("BTCUSD", self.prices.at - 1, Decimal("100.6"), Decimal("100"),
                          Decimal("100"), Decimal("100.01"), Decimal("100"), Decimal("100"), self.prices.at)
        eth = replace(btc, symbol="ETHUSD", ask_size=Decimal("0.00001"))
        with patch("paper_trading.practice.observe", side_effect=lambda client, symbol, *a, **kw: eth if symbol == "ETHUSD" else replace(btc, symbol=symbol)):
            state = self.start()
        self.assertTrue(state["active"])
        self.assertEqual(state["cash"], "200")
        self.assertEqual([r["action"] for r in state["activity"]], ["WAIT"] * 5)
        trial = state["learning"]["portfolios"]["standard"]
        self.assertFalse(trial["wallet"]["active"])
        self.assertEqual(trial["wallet"]["cash"], "200")
        self.assertEqual(trial["wallet"]["positions"], {})
        self.assertEqual(trial["wallet"]["fees"], "0")
        self.assertEqual(trial["exits"], 0)

    def test_rule_change_after_observation_cannot_relabel_current_trade(self):
        from unittest.mock import patch
        def promote(learner, *args, **kwargs):
            learner["active_rule"] = "very_selective"
        with patch("paper_trading.learning.advance", side_effect=promote):
            state = self.start()
        self.assertEqual(state["learning"]["active_rule"], "very_selective")
        self.assertTrue(all(r["entry_margin"] == "0.002" for r in state["activity"]))
        self.assertEqual(Decimal(state["cash"]), 100)


if __name__ == "__main__":
    unittest.main()
