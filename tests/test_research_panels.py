"""Research controls edit only local, explicitly selected research data."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop_app.screens.practice import PracticeScreen
from paper_trading import metals, research_costs
from .tk_helpers import TkTestCase
from .test_research_extensions import bullion, costs


class ResearchPanelTests(TkTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.screen = PracticeScreen(self.root, SimpleNamespace(repo_root=self.repo))

    def test_research_panels_do_not_create_wallet_or_start_recorder(self):
        self.assertFalse((self.repo / "data").exists())
        self.assertFalse(self.screen.research_panel.recorder.running)
        self.assertIn("No physical inventory", self.screen.metals_panel.summary.get())

    def test_fee_import_writes_only_research_profile_not_wallet(self):
        source = self.repo / "reported.json"
        source.write_text(json.dumps(costs("0.001")))
        with patch("desktop_app.widgets.research_panel.filedialog.askopenfilename", return_value=str(source)):
            self.screen.research_panel.import_profile()
        saved = research_costs.load(self.repo / "data" / "research_costs.json")
        self.assertEqual(str(research_costs.fee(saved)), "0.001")
        self.assertFalse((self.repo / "data" / "practice_agent.sqlite").exists())
        self.assertIn("existing studies and wallet are preserved", self.screen.research_panel.swing_text.get())

    def test_recorder_start_is_explicit_and_close_signals_stop(self):
        recorder = self.screen.research_panel.recorder
        with patch.object(recorder, "start") as start, patch.object(recorder, "stop") as stop:
            self.screen.research_panel.start()
            start.assert_called_once()
            self.screen.close()
            stop.assert_called()
        self.assertIsNone(self.screen.research_panel._timer)

    def test_inventory_save_scenario_and_sold_lot_do_not_touch_wallet(self):
        panel = self.screen.metals_panel
        for key, value in bullion().items():
            if key in panel.values:
                panel.values[key].set(value)
        panel.add()
        self.assertIn("-35.00", panel.summary.get())
        panel.hedge["spot"].set("30")
        panel.hedge["ounces"].set("10")
        panel.hedge["strike"].set("30")
        panel.hedge["premium"].set("1")
        panel.hedge["expiry"].set("2027-11-07")
        panel.scenarios()
        self.assertIn("not guaranteed protection", panel.results.get())
        self.assertIn("-20%", panel.results.get())
        panel.listing.selection_set("0")
        with patch("desktop_app.widgets.metals_panel.simpledialog.askstring", return_value="10"):
            panel.mark_sold()
        self.assertIn("No physical inventory", panel.summary.get())
        self.assertFalse((self.repo / "data" / "practice_agent.sqlite").exists())

    def test_unreadable_inventory_is_not_overwritten(self):
        panel = self.screen.metals_panel
        panel.path.parent.mkdir(parents=True)
        panel.path.write_text("damaged data")
        panel.refresh()
        with patch("desktop_app.widgets.metals_panel.messagebox.showerror") as error:
            panel.add()
        error.assert_called_once()
        self.assertEqual(panel.path.read_text(), "damaged data")

    def test_background_collection_is_explicit_and_survives_panel_close(self):
        panel = self.screen.research_panel
        with patch("desktop_app.widgets.research_panel.background.start", return_value={"message": "Starting"}) as start, patch("desktop_app.widgets.research_panel.background.stop") as stop:
            panel.start_background()
            start.assert_called_once_with(panel.collect_dir)
            panel.close()
            stop.assert_not_called()

    def test_historical_worker_does_not_write_wallet_or_replace_models(self):
        from desktop_app.threading_support import JobResult
        panel = self.screen.research_panel
        with patch("desktop_app.widgets.research_panel.run_in_background") as worker:
            panel.history_job("download")
        self.assertTrue(panel._history_busy)
        worker.call_args.args[2](JobResult(ok=True, value={"coins": {"BTCUSD": {"bars_received": 720, "internal_missing_hours": 0}}}))
        self.assertFalse(panel._history_busy)
        self.assertIn("720/0", panel.history_text.get())
        self.assertFalse((self.repo / "data" / "practice_agent.sqlite").exists())

    def test_swing_practice_has_its_own_wallet_and_continues_on_close(self):
        from .test_swing_strategy import report
        from desktop_app.screens.swing import SwingScreen
        panel = SwingScreen(self.root, SimpleNamespace(repo_root=self.repo)).panel
        self.assertFalse(panel.agent.path.exists())
        panel.seed_amount("1000")
        with patch("desktop_app.widgets.swing_practice.historical.latest", return_value=report()), patch("desktop_app.widgets.swing_practice.run_in_background") as worker, patch("desktop_app.widgets.swing_practice.messagebox.showerror") as error:
            panel.start()
        error.assert_not_called()
        self.assertTrue(panel.agent.snapshot()["active"])
        self.assertEqual(worker.call_count, 1)
        self.assertFalse((self.repo / "data" / "practice_agent.sqlite").exists())
        panel.close()
        self.assertTrue(panel.agent.snapshot()["active"])

    def test_swing_starting_amount_tracks_reference_until_user_edits_it(self):
        from desktop_app.screens.swing import SwingScreen
        panel = SwingScreen(self.root, SimpleNamespace(repo_root=self.repo)).panel
        panel.seed_amount("190")
        self.assertEqual(panel.amount.get(), "190")
        panel.amount.set("1000")
        panel.seed_amount("180")
        self.assertEqual(panel.amount.get(), "1000")

    def test_refreshing_selected_quote_preserves_ounces_cost_and_other_lots(self):
        panel = self.screen.metals_panel
        original = [bullion(), bullion("Generic bullion", bid="30")]
        metals.save(panel.path, original)
        panel.refresh()
        panel.listing.selection_set("0")
        panel.load_selected()
        panel.values["bid"].set("33")
        panel.values["spot"].set("31")
        panel.values["quote_date"].set("2026-11-08")
        panel.values["exit_cost"].set("6")
        panel.values["cost"].set("999")
        panel.values["ounces"].set("99")
        panel.update_quote()
        saved = metals.load(panel.path)
        self.assertEqual(saved[0]["bid"], "33")
        self.assertEqual(saved[0]["quote_date"], "2026-11-08")
        self.assertEqual(saved[0]["cost"], "350")
        self.assertEqual(saved[0]["ounces"], "10")
        self.assertEqual(saved[1], original[1])
        self.assertFalse((self.repo / "data" / "practice_agent.sqlite").exists())
