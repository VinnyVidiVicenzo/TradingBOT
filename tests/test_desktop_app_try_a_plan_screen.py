"""The 'Try your own plan' screen, driven end to end on a throwaway price store.

A real (never-shown) Tk root, a real sweep in a real background thread, and a
synthetic database -- so the whole path a click takes is exercised: input
checks, coin discovery, the sweep, the plain-language messages, and what does
(and does not) get saved to the plan history.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from crypto_research.binanceus.parsing import parse_symbol_rules
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository, RuleSnapshotRepository
from crypto_research.timeutil import DAY_MS, parse_time

from desktop_app import plan_history
from desktop_app.plan_builder import Schedule
from desktop_app.screens.try_a_plan import TryAPlanScreen

from .helpers import make_bar, make_rule_entry
from .tk_helpers import TkTestCase

START = parse_time("2024-01-01T00:00:00Z")
DAYS = 600  # to 2025-08-22: enough for eight 12-month starting months


class _FakeApp:
    def __init__(self, root: Path, db_path: Path) -> None:
        self.repo_root = root
        self.db_path = str(db_path)
        self.plan_history_path = root / "experiments" / plan_history.FILENAME
        self.reference = None


def _build_store(path: Path) -> None:
    db = open_database(str(path))
    try:
        RuleSnapshotRepository(db).upsert_many(
            parse_symbol_rules(
                make_rule_entry(symbol, base=symbol[:-4], quote="USDT"),
                received_at=0,
                valid_from=0,
            )
            for symbol in ("BTCUSDT", "ETHUSDT")
        )
        bars = BarRepository(db)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            bars.upsert_many(
                make_bar(symbol, START + i * DAY_MS, str(100 + i)) for i in range(DAYS)
            )
    finally:
        db.close()


class TryAPlanScreenTests(TkTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        _build_store(tmp / "market.sqlite")
        self.app = _FakeApp(tmp, tmp / "market.sqlite")
        self.screen = TryAPlanScreen(self.root, self.app)
        self.screen.on_show()

    def _run(self, *, amount="200", hold=12, schedule=Schedule.SPREAD_OVER_WEEKS, coins=("BTC", "ETH")):
        for base, var in self.screen._coin_vars.items():
            var.set(base in coins)
        self.screen._amount_var.set(amount)
        self.screen._hold_var.set(hold)
        self.screen._schedule_var.set(schedule.value)
        self.screen._on_run_clicked()
        deadline = time.monotonic() + 60
        while not self.screen._run_button.instate(["!disabled"]) and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertTrue(self.screen._run_button.instate(["!disabled"]), "the run never finished")

    def _history(self) -> list:
        return plan_history.load_all(self.app.plan_history_path)

    def _shows_a_result(self) -> bool:
        return bool(self.screen._results._title.winfo_manager())

    # -- what appears on screen ------------------------------------------

    def test_the_curated_coins_found_in_the_store_are_offered_with_bitcoin_and_ethereum_on(self) -> None:
        self.assertEqual(set(self.screen._coin_vars), {"BTC", "ETH"})
        self.assertTrue(all(var.get() for var in self.screen._coin_vars.values()))

    # -- the success path ------------------------------------------------

    def test_a_good_run_shows_a_result_and_saves_it(self) -> None:
        self._run()
        self.assertEqual(self.screen._status_var.get(), "")
        self.assertTrue(self._shows_a_result())
        self.assertIn("Bitcoin and Ethereum", self.screen._results._title.cget("text"))
        (stored,) = self._history()
        self.assertEqual(len(stored.result.outcomes), 8)
        self.assertEqual(stored.result.hold_months, 12)

    def test_running_again_adds_a_second_history_entry_and_replaces_the_display(self) -> None:
        self._run()
        self._run(schedule=Schedule.ALL_AT_ONCE)
        self.assertEqual(len(self._history()), 2)
        self.assertIn("put in all at once", self.screen._results._title.cget("text"))

    def test_each_schedule_completes(self) -> None:
        for schedule in Schedule:
            with self.subTest(schedule=schedule):
                self._run(schedule=schedule, hold=12)
                self.assertEqual(self.screen._status_var.get(), "")
                self.assertTrue(self._shows_a_result())


    # -- the hover explanations and the keyboard -------------------------

    def _walk(self, widget=None):
        widget = widget or self.screen
        yield widget
        for child in widget.winfo_children():
            yield from self._walk(child)

    def test_every_control_explains_itself_on_hover(self) -> None:
        interactive = {"TRadiobutton", "TCheckbutton", "TEntry", "TButton"}
        controls = [w for w in self._walk() if w.winfo_class() in interactive]
        # amount box, 2 coins, 3 ways of putting money in, 3 hold lengths, run button
        self.assertGreaterEqual(len(controls), 10)
        for control in controls:
            with self.subTest(control=control.winfo_class(), text=str(control.cget("text")) if control.winfo_class() != "TEntry" else "amount"):
                self.assertTrue(control.bind("<Enter>"), "no hover explanation attached")

    def test_every_form_label_explains_itself_on_hover(self) -> None:
        wanted = {"How much money?", "Which coins?", "How should it go in?", "How long would you hold it?"}
        labels = {w.cget("text"): w for w in self._walk() if w.winfo_class() == "TLabel"}
        for text in wanted:
            with self.subTest(label=text):
                self.assertIn(text, labels)
                self.assertTrue(labels[text].bind("<Enter>"))

    def test_pressing_enter_in_the_amount_box_runs_the_plan(self) -> None:
        # Tk won't deliver a synthesised key press to a hidden, unfocused window,
        # so check that Return is bound, and that what it is bound to runs the plan.
        self.assertTrue(self.screen._amount_entry.bind("<Return>"))
        self.screen._on_return()
        deadline = time.monotonic() + 30
        while not self._history() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(len(self._history()), 1)

    def test_pressing_enter_while_a_run_is_working_does_not_start_a_second_one(self) -> None:
        self.screen._run_button.state(["disabled"])
        self.screen._show_status("Trying every possible starting month...")
        self.screen._on_run_clicked()
        self.assertEqual(self.screen._status_var.get(), "Trying every possible starting month...")
        self.assertEqual(self._history(), [])

    def test_a_good_run_is_compared_with_a_stock_fund_and_savings_when_that_data_exists(self) -> None:
        from desktop_app import alternatives

        day = DAY_MS
        base = parse_time("2023-06-01T00:00:00Z")
        self.app.reference = alternatives.build_reference(
            {base + i * day: 100.0 + i * 0.05 for i in range(1000)},
            {base + i * day: 4.0 for i in range(1000)},
        )
        self._run()
        self.assertTrue(self.screen._results.showing_comparison)
        self.assertIn("2008-09", self.screen._results._notes.cget("text"))
        self.assertEqual(len(self._history()), 1)  # the saved plan holds only the plan itself

    def test_a_good_run_without_that_data_is_shown_on_its_own(self) -> None:
        self._run()  # the fake app has no reference data
        self.assertTrue(self._shows_a_result())
        self.assertFalse(self.screen._results.showing_comparison)

    # -- the refusals ----------------------------------------------------

    def test_too_little_history_says_so_plainly_and_saves_nothing(self) -> None:
        self._run(hold=36)
        message = self.screen._status_var.get()
        self.assertIn("isn't enough saved price history", message)
        self.assertIn("3 years", message)
        self.assertFalse(self._shows_a_result())
        self.assertEqual(self._history(), [])

    def test_a_stake_too_small_to_buy_anything_is_explained_not_shown_as_good_news(self) -> None:
        self._run(amount="1")
        self.assertIn("None of the money was invested", self.screen._status_var.get())
        self.assertFalse(self._shows_a_result())
        self.assertEqual(self._history(), [])

    def test_a_bad_amount_is_rejected_before_anything_runs(self) -> None:
        self.screen._amount_var.set("abc")
        self.screen._on_run_clicked()
        self.assertIn("doesn't look like an amount", self.screen._status_var.get())
        self.assertTrue(self.screen._run_button.instate(["!disabled"]))
        self.assertEqual(self._history(), [])

    def test_no_coins_picked_is_rejected_before_anything_runs(self) -> None:
        for var in self.screen._coin_vars.values():
            var.set(False)
        self.screen._on_run_clicked()
        self.assertEqual(self.screen._status_var.get(), "Pick at least one coin.")

    def test_a_failed_run_clears_the_previous_result_instead_of_leaving_it_up(self) -> None:
        self._run()
        self.assertTrue(self._shows_a_result())
        self._run(hold=36)  # fails: not enough history
        self.assertFalse(self._shows_a_result())

    def test_the_run_button_is_locked_while_working(self) -> None:
        self.screen._on_run_clicked()
        self.assertTrue(self.screen._run_button.instate(["disabled"]))
        self.assertEqual(self.screen._status_var.get(), "Trying every possible starting month...")
        deadline = time.monotonic() + 60
        while not self.screen._run_button.instate(["!disabled"]) and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)


class EmptyStoreTests(TkTestCase):
    def test_no_downloaded_prices_gives_a_plain_message_not_a_blank_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _FakeApp(Path(tmp), Path(tmp) / "empty.sqlite")
            self.screen = TryAPlanScreen(self.root, app)
            self.screen.on_show()
            self.assertIn(
                "No price history has been downloaded yet", self.screen._status_var.get()
            )
            self.assertEqual(self.screen._coin_vars, {})


if __name__ == "__main__":
    unittest.main()
