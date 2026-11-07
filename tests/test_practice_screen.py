"""Practice screen lifecycle, with all worker/network work replaced."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop_app.screens.practice import PracticeScreen
from desktop_app.threading_support import JobResult
from .tk_helpers import TkTestCase, show_offscreen


class PracticeScreenTests(TkTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.screen = PracticeScreen(self.root, SimpleNamespace(repo_root=self.repo))

    def test_construction_is_offline_and_does_not_create_wallet(self):
        self.assertFalse((self.repo / "data").exists())
        self.assertIn("200.00", self.screen.summary.get())
        self.assertEqual(self.screen.waiting.get_children(), ("BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XRPUSD"))

    def test_start_pause_and_pending_worker_delivery(self):
        with patch("desktop_app.screens.practice.run_in_background") as worker:
            self.screen.start()
        self.assertEqual(worker.call_count, 1)
        self.assertTrue(self.screen.agent.snapshot()["active"])
        self.screen.pause()
        self.assertFalse(self.screen.agent.snapshot()["active"])
        self.screen._done(JobResult(ok=True))
        self.assertIsNone(self.screen._timer)
        self.assertIn("PAUSED", self.screen.status.get())

    def test_reopening_preserves_wallet_and_running_session(self):
        self.screen.on_show()
        self.screen.agent.set_active(True)
        reopened = PracticeScreen(self.root, SimpleNamespace(repo_root=self.repo))
        reopened.on_show()
        self.assertTrue(reopened.agent.snapshot()["active"])
        self.assertEqual(reopened.agent.snapshot()["cash"], "200")

    def test_close_cancels_ui_timer_and_keeps_session_running(self):
        self.screen.on_show()
        self.screen.agent.set_active(True)
        self.screen._timer = self.screen.after(60_000, lambda: None)
        self.screen.close()
        self.assertIsNone(self.screen._timer)
        self.assertTrue(self.screen.agent.snapshot()["active"])
        self.assertIsNone(self.screen._status_timer)

    def test_controls_fit_small_supported_window(self):
        self.screen.pack(fill="both", expand=True)
        show_offscreen(self.root, 980, 660)
        self.assertLess(self.screen.activity.winfo_rooty(), self.root.winfo_rooty() + 660)
        self.assertGreater(self.screen.activity.winfo_height(), 90)

    def test_decision_details_show_saved_costs_and_conflicts(self):
        from paper_trading.practice import initial_state
        state = initial_state()
        state["activity"] = [{"at": 1000, "action": "BUY", "symbol": "BTCUSD", "reason": "Trend supports entry.",
                              "spend": "50", "evidence": {"market": {"status": "partial"},
                              "signal_reason": "Market sample is partial.", "roundtrip_cost_pct": "1.01"}}]
        self.screen.render(state)
        self.screen.activity.selection_set(self.screen.activity.get_children()[0])
        self.screen._selected()
        self.assertIn("1.01%", self.screen.details_text.get())
        self.assertIn("comparison only", self.screen.details_text.get())
        self.assertIn("partial", self.screen.market_text.get())
        self.assertEqual(len(self.screen.tabs.tabs()), 7)

    def test_opportunity_panel_shows_advisory_forecasts_and_keeps_wallet(self):
        from .test_practice_agent import Prices
        self.screen.on_show()
        self.screen.agent.set_active(True)
        prices = Prices()
        state = self.screen.agent.check(client=prices, clock=lambda: prices.at)
        self.screen.render(state)
        text = self.screen.prediction_text.get()
        self.assertIn("Advisory model only", text)
        self.assertIn("Pending: 5", text)
        self.assertIn("uncalibrated", text)
        self.assertIn("BTCUSD BUY", text)
        self.assertIn("Total value:", self.screen.summary.get())

    def test_waiting_board_shows_threshold_and_live_countdown_from_saved_data(self):
        from .test_practice_status import state_with_trend, AT
        with patch("desktop_app.screens.practice.now_ms", return_value=AT + 10_000):
            self.screen.render(state_with_trend("99", "100"))
        row = self.screen.waiting.item("BTCUSD", "values")
        self.assertIn("stronger upward trend", row[1])
        self.assertEqual(row[2], "-1.000%")
        self.assertEqual(row[3], "> +0.200%")
        self.assertIn("about 50s", self.screen.refresh_text.get())
        self.assertIn("do not block entries", self.screen.refresh_text.get())
        visible = self.screen.activity.item(self.screen.activity.get_children()[0], "values")
        self.assertIn("Trend below entry requirement", visible[-1])
        self.assertFalse((self.repo / "data").exists())
