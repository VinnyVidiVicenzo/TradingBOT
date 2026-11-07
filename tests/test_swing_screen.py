"""Full-size swing workspace and preservation of the saved session."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from crypto_research.market_universe import SYMBOLS
from desktop_app.screens.swing import SwingScreen
from desktop_app.app import App
from paper_trading import research_costs
from paper_trading.swing_strategy import SwingPracticeAgent
from .test_swing_strategy import report, PublicPrices
from .tk_helpers import TkTestCase, show_offscreen


class SwingScreenTests(TkTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)

    def test_screen_is_offline_and_all_five_rows_fit_at_minimum_size(self):
        screen = SwingScreen(self.root, SimpleNamespace(repo_root=self.repo))
        screen.pack(fill="both", expand=True)
        show_offscreen(self.root, 980, 660)
        self.assertFalse((self.repo / "data").exists())
        self.assertEqual(screen.panel.waiting_tree.get_children(), SYMBOLS)
        self.assertTrue(screen.panel.waiting_tree.bbox("XRPUSD"))
        self.assertGreater(screen.panel.tabs.winfo_height(), 200)
        screen.close()

    def test_reopening_saved_wallet_keeps_money_model_positions_activity_and_learning(self):
        agent = SwingPracticeAgent(self.repo / "data" / "swing_practice.sqlite")
        agent.initialize("1000", report())
        research_costs.store(self.repo / "data" / "research_costs.json", report()["cost_profile"])
        prices = PublicPrices()
        agent.set_active(True)
        before = agent.check(client=prices, clock=lambda: prices.at)
        screen = SwingScreen(self.root, SimpleNamespace(repo_root=self.repo))
        screen.on_show()
        after = agent.snapshot()
        for key in ("cash", "initial", "positions", "equity", "fees", "exits", "config", "activity", "learning"):
            self.assertEqual(after[key], before[key], key)
        self.assertTrue(after["active"])
        self.assertIn("pending: 5", screen.panel.learning_text.get())
        self.assertEqual(screen.panel.waiting_tree.item("XRPUSD", "values")[0], "XRPUSD")
        self.assertIn("account unverified", screen.panel.summary.get())
        screen.close()

    def test_full_app_has_one_swing_controller_and_two_navigation_rows(self):
        app = App(repo_root=self.repo)
        try:
            app.show("swing")
            show_offscreen(app.root, 980, 660)
            self.assertFalse(hasattr(app._screens["practice"].research_panel, "swing_practice"))
            self.assertGreater(app._screens["swing"].panel.tabs.winfo_height(), 170)
            self.assertLessEqual(max(int(b.grid_info()["row"]) for b in app.nav._buttons.values()), 1)
        finally:
            app._close()
