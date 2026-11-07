"""Waiting explanations use saved observations and never modify the wallet."""

import copy
import unittest

from desktop_app.practice_status import entry_rows, reason, refresh_status
from paper_trading.practice import initial_state, HOUR, MINUTE

AT = 100 * HOUR + 30 * MINUTE
BAR = 100 * HOUR - 1


def state_with_trend(fast="99", slow="100"):
    state = initial_state()
    state.update(active=True, last_check=AT, last_bars={"BTCUSD": BAR})
    state["activity"] = [{"symbol": "BTCUSD", "at": AT, "action": "WAIT",
                          "reason": "Waiting for a new completed hour.", "fast": fast,
                          "slow": slow, "bar_time": BAR, "entry_margin": "0.002"}]
    return state


class PracticeStatusTests(unittest.TestCase):
    def test_real_blocker_visible_between_hourly_evaluations(self):
        state = state_with_trend("75829.0755", "76694.0578")
        original = copy.deepcopy(state)
        row = entry_rows(state, AT)[0]
        self.assertIn("stronger upward trend", row["blocker"])
        self.assertEqual(row["gap"], "-1.128%")
        self.assertEqual(row["target"], "> +0.200%")
        self.assertIn("30m", row["next"])
        self.assertEqual(state, original)

    def test_exact_threshold_is_not_enough_and_rounding_does_not_hide_near_miss(self):
        exact = entry_rows(state_with_trend("100.2"), AT)[0]
        near = entry_rows(state_with_trend("100.1962517"), AT)[0]
        self.assertIn("stronger", exact["blocker"])
        self.assertEqual(near["gap"], "+0.196%")
        self.assertEqual(near["target"], "> +0.200%")

    def test_current_adapted_rule_is_used_for_next_entry_review(self):
        state = state_with_trend("100.5")
        state["learning"]["active_rule"] = "very_selective"
        row = entry_rows(state, AT)[0]
        self.assertEqual(row["target"], "> +1.000%")
        self.assertIn("stronger", row["blocker"])

    def test_good_trend_waits_for_hour_and_due_time_never_promises_fill(self):
        state = state_with_trend("101")
        row = entry_rows(state, AT)[0]
        self.assertIn("Entry trend met", row["blocker"])
        later = entry_rows(state, BAR + HOUR + 1)[0]
        self.assertIn("awaiting new hourly data", later["next"])

    def test_paused_and_stale_states_do_not_imply_live_conditions(self):
        state = state_with_trend()
        stale = entry_rows(state, AT + 3 * MINUTE)[0]
        self.assertIn("prices are old", stale["blocker"])
        state["active"] = False
        paused = entry_rows(state, AT + 3 * MINUTE)[0]
        self.assertIn("Paused", paused["blocker"])
        self.assertEqual(paused["next"], "Paused")

    def test_cash_is_not_confused_with_total_value(self):
        state = state_with_trend()
        state.update(cash="150", equity="199")
        self.assertIn("stronger", entry_rows(state, AT)[0]["blocker"])
        state.update(cash="5", equity="190")
        self.assertIn("Not enough", entry_rows(state, AT)[0]["blocker"])
        state["positions"]["BTCUSD"] = {"quantity": "1"}
        self.assertIn("Holding", entry_rows(state, AT)[0]["blocker"])

    def test_no_prices_yet_and_learning_is_not_a_trade_lock(self):
        state = initial_state()
        state["active"] = True
        self.assertIn("first price check", entry_rows(state, AT)[0]["blocker"])
        self.assertIn("Learning hours do not block", refresh_status(state, AT))

    def test_countdown_updates_without_new_market_or_wallet_io(self):
        state = state_with_trend()
        self.assertIn("about 50s", refresh_status(state, AT + 10_000))
        self.assertIn("about 40s", refresh_status(state, AT + 20_000))
        self.assertIn("successful refresh", refresh_status(state, AT + 3 * MINUTE))

    def test_historical_wait_rows_retain_time_specific_explanation(self):
        state = state_with_trend("100.1962517")
        text = reason(state["activity"][0])
        self.assertIn("+0.196% vs > +0.200%", text)
        self.assertIn("UTC", text)
        self.assertEqual(reason({"reason": "Buy rule met."}), "Buy rule met.")
