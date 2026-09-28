"""The screens that were only ever checked by eye: Home, Findings, Your past plans,
Safety check, and the window shell (desktop_app.app.App) that ties them together."""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from tkinter import ttk
from unittest import mock

from crypto_research.safety import SafetyFinding, SafetyReport

from desktop_app import plan_history
from desktop_app.app import MIN_HEIGHT, MIN_WIDTH, SCREENS
from desktop_app.plan_builder import Schedule
from desktop_app.screens.findings import FindingsScreen
from desktop_app.screens.home import HomeScreen
from desktop_app.screens.past_plans import PastPlansScreen
from desktop_app.screens.safety_check import SafetyCheckScreen
from desktop_app.sweep import SweepResult, WindowOutcome

from .tk_helpers import AppTestCase, TkTestCase

D = Decimal


class _FakeApp:
    """Just what the screens read from the real App."""

    def __init__(self, root: Path) -> None:
        self.repo_root = root
        self.db_path = str(root / "data" / "market.sqlite")
        self.plan_history_path = root / "experiments" / plan_history.FILENAME
        self.reference = None
        self.shown: list[str] = []

    def show(self, key: str) -> None:
        self.shown.append(key)


def _plan(amount: str, *, ending: str = "250") -> SweepResult:
    return SweepResult(
        amount=D(amount),
        symbols=("BTCUSDT",),
        schedule=Schedule.ALL_AT_ONCE,
        hold_months=12,
        outcomes=tuple(
            WindowOutcome(1_600_000_000_000 + i * 2_700_000_000, D(amount), D(ending), D("0.2"), 0)
            for i in range(8)
        ),
        skipped=0,
    )


class _ScreenTestCase(TkTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.fake = _FakeApp(self.repo)


# -- Home ----------------------------------------------------------------


class HomeScreenTests(_ScreenTestCase):
    def _buttons(self, screen: HomeScreen) -> list[ttk.Button]:
        found, stack = [], list(screen.winfo_children())
        while stack:
            widget = stack.pop(0)
            if isinstance(widget, ttk.Button):
                found.append(widget)
            stack.extend(widget.winfo_children())
        return found

    def test_it_reassures_about_safety_in_plain_words(self) -> None:
        self.screen = HomeScreen(self.root, self.fake)
        texts = [w.cget("text") for w in self.screen.winfo_children() if isinstance(w, ttk.Label)]
        joined = " ".join(texts)
        self.assertIn("never buy, sell, or move your real money", joined)

    def test_each_button_goes_to_its_screen(self) -> None:
        self.screen = HomeScreen(self.root, self.fake)
        buttons = self._buttons(self.screen)
        self.assertEqual(len(buttons), 4)
        for button in buttons:
            button.invoke()
        self.assertEqual(self.fake.shown, ["findings", "try_a_plan", "past_plans", "safety_check"])


# -- What we've found ------------------------------------------------------


class FindingsScreenTests(_ScreenTestCase):
    def _write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_it_lists_the_documents_and_shows_the_first(self) -> None:
        self._write("results/FINAL_REPORT.md", "# The final report\n\nPlain words.\n")
        self._write("results/scenarios_report.txt", "asset   disaster\nBitcoin        82\n")
        self.screen = FindingsScreen(self.root, self.fake)
        self.screen.on_show()
        titles = list(self.screen._list.get(0, "end"))
        self.assertEqual(titles[0], "The full story, in plain words")
        self.assertEqual(len(titles), 2)
        shown = self.screen._view.text.get("1.0", "end")
        self.assertIn("The final report", shown)
        self.assertIn("Every test and finding", self.screen._description.cget("text"))

    def test_choosing_another_entry_swaps_the_text_and_the_description(self) -> None:
        self._write("results/FINAL_REPORT.md", "# The final report\n")
        self._write("results/scenarios_report.txt", "asset   disaster\nBitcoin        82\n")
        self.screen = FindingsScreen(self.root, self.fake)
        self.screen.on_show()
        self.screen._list.selection_clear(0, "end")
        self.screen._list.selection_set(1)
        self.screen._on_select(None)
        shown = self.screen._view.text.get("1.0", "end")
        self.assertIn("Bitcoin        82", shown)  # a plain-text report keeps its alignment
        self.assertNotIn("The final report", shown)
        self.assertIn("Best, worst and typical", self.screen._description.cget("text"))

    def test_the_ai_handoff_document_is_never_listed_even_if_it_exists(self) -> None:
        self._write("CONTEXT_FOR_CLAUDE.md", "# Context\n\nTo Claude, reading this: ...\n")
        self._write("results/FINAL_REPORT.md", "# The final report\n")
        self.screen = FindingsScreen(self.root, self.fake)
        self.screen.on_show()
        self.assertEqual(len(self.screen._list.get(0, "end")), 1)
        self.assertNotIn("Claude", self.screen._view.text.get("1.0", "end"))

    def test_a_folder_with_no_reports_is_an_empty_list_not_a_crash(self) -> None:
        self.screen = FindingsScreen(self.root, self.fake)
        self.screen.on_show()
        self.assertEqual(self.screen._list.size(), 0)

    def test_showing_the_screen_again_does_not_duplicate_the_list(self) -> None:
        self._write("results/FINAL_REPORT.md", "# The final report\n")
        self.screen = FindingsScreen(self.root, self.fake)
        self.screen.on_show()
        self.screen.on_show()
        self.assertEqual(self.screen._list.size(), 1)

    def test_a_new_report_dropped_in_later_is_found_on_the_next_launch(self) -> None:
        self._write("results/brand_new_report.txt", "line one\n")
        self.screen = FindingsScreen(self.root, self.fake)
        self.screen.on_show()
        self.assertEqual(list(self.screen._list.get(0, "end")), ["Brand new"])


# -- Your past plans -----------------------------------------------------


class PastPlansScreenTests(_ScreenTestCase):
    def _save(self, amount: str, when: str) -> None:
        plan_history.append(self.fake.plan_history_path, _plan(amount), created_at_iso=when)

    def test_no_plans_yet_says_so_and_offers_the_way_forward(self) -> None:
        self.screen = PastPlansScreen(self.root, self.fake)
        self.screen.on_show()
        self.assertTrue(self.screen._empty_label.winfo_manager())
        self.assertIn("Try your own plan", self.screen._empty_label.cget("text"))
        self.assertFalse(self.screen._list_frame.winfo_manager())

    def test_newest_plan_is_first_and_is_shown(self) -> None:
        self._save("100", "2026-09-25T10:00:00Z")
        self._save("300", "2026-09-27T10:00:00Z")
        self._save("200", "2026-09-26T10:00:00Z")
        self.screen = PastPlansScreen(self.root, self.fake)
        self.screen.on_show()
        rows = list(self.screen._list.get(0, "end"))
        self.assertEqual(len(rows), 3)
        self.assertIn("$300", rows[0])
        self.assertIn("$200", rows[1])
        self.assertIn("$100", rows[2])
        self.assertIn("$300 in Bitcoin", self.screen._results._title.cget("text"))
        self.assertFalse(self.screen._empty_label.winfo_manager())

    def test_choosing_another_plan_shows_that_plan(self) -> None:
        self._save("100", "2026-09-25T10:00:00Z")
        self._save("300", "2026-09-27T10:00:00Z")
        self.screen = PastPlansScreen(self.root, self.fake)
        self.screen.on_show()
        self.screen._list.selection_clear(0, "end")
        self.screen._list.selection_set(1)
        self.screen._on_select(None)
        self.assertIn("$100 in Bitcoin", self.screen._results._title.cget("text"))

    def test_a_saved_plan_is_shown_beside_the_stock_fund_and_savings_when_that_data_exists(self) -> None:
        from crypto_research.timeutil import parse_time

        from desktop_app import alternatives

        day = 24 * 60 * 60 * 1000
        base = parse_time("2020-06-01T00:00:00Z")
        self.fake.reference = alternatives.build_reference(
            {base + i * day: 100.0 + i * 0.1 for i in range(1500)},
            {base + i * day: 4.0 for i in range(1500)},
        )
        self._save("200", "2026-09-27T10:00:00Z")
        self.screen = PastPlansScreen(self.root, self.fake)
        self.screen.on_show()
        self.assertTrue(self.screen._results.showing_comparison)

    def test_without_that_data_a_saved_plan_still_shows_on_its_own(self) -> None:
        self._save("200", "2026-09-27T10:00:00Z")
        self.screen = PastPlansScreen(self.root, self.fake)  # fake.reference is None
        self.screen.on_show()
        self.assertFalse(self.screen._results.showing_comparison)
        self.assertIn("$200 in Bitcoin", self.screen._results._title.cget("text"))

    def test_a_plan_saved_after_the_screen_was_first_shown_appears_on_the_next_visit(self) -> None:
        self.screen = PastPlansScreen(self.root, self.fake)
        self.screen.on_show()
        self.assertEqual(self.screen._list.size(), 0)
        self._save("200", "2026-09-27T10:00:00Z")
        self.screen.on_show()
        self.assertEqual(self.screen._list.size(), 1)

    def test_a_damaged_line_costs_one_plan_not_the_whole_list(self) -> None:
        self._save("100", "2026-09-25T10:00:00Z")
        with self.fake.plan_history_path.open("a", encoding="utf-8") as handle:
            handle.write('{"version": 1, "created_at": "2026-09-2')
        self._save("300", "2026-09-27T10:00:00Z")
        self.screen = PastPlansScreen(self.root, self.fake)
        with self.assertLogs("desktop_app.plan_history", level="WARNING"):
            self.screen.on_show()
        self.assertEqual(self.screen._list.size(), 2)


# -- Safety check ----------------------------------------------------------


class SafetyCheckScreenTests(_ScreenTestCase):
    def _click_and_wait(self) -> None:
        import time

        self.screen._on_check_clicked()
        deadline = time.monotonic() + 30
        while self.screen._result_var.get() in ("", "Checking...") and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)

    def test_before_clicking_nothing_is_claimed(self) -> None:
        self.screen = SafetyCheckScreen(self.root, self.fake)
        self.assertEqual(self.screen._result_var.get(), "")

    def test_the_real_repository_passes_and_the_screen_says_so_in_plain_words(self) -> None:
        self.fake.repo_root = Path(__file__).parent.parent
        self.screen = SafetyCheckScreen(self.root, self.fake)
        self._click_and_wait()
        message = self.screen._result_var.get()
        self.assertIn("Confirmed: nothing that could trade or move money was found", message)
        self.assertRegex(message, r"across \d+ files scanned just now")
        self.assertTrue(self.screen._button.instate(["!disabled"]))

    def test_a_finding_is_reported_as_a_problem_and_logged_with_detail(self) -> None:
        bad = SafetyReport(
            findings=[SafetyFinding("desktop_app/x.py", 3, "pat", "signing", "import hmac", "desktop-app")],
            files_scanned=5,
        )
        self.screen = SafetyCheckScreen(self.root, self.fake)
        with mock.patch("desktop_app.screens.safety_check.audit_repository", return_value=bad):
            with self.assertLogs("desktop_app.screens.safety_check", level="ERROR") as logged:
                self._click_and_wait()
        message = self.screen._result_var.get()
        self.assertIn("1 issue(s)", message)
        self.assertIn("Do not use this app", message)
        self.assertNotIn("Confirmed", message)
        self.assertIn("desktop_app/x.py", logged.output[0])  # the detail goes to the log, not the person

    def test_the_audit_itself_failing_is_not_reported_as_a_pass(self) -> None:
        self.screen = SafetyCheckScreen(self.root, self.fake)
        with mock.patch(
            "desktop_app.screens.safety_check.audit_repository", side_effect=OSError("disk gone")
        ):
            self._click_and_wait()
        message = self.screen._result_var.get()
        self.assertIn("Couldn't finish the check", message)
        self.assertNotIn("Confirmed", message)
        self.assertTrue(self.screen._button.instate(["!disabled"]))


# -- The window shell --------------------------------------------------------


class AppShellTests(AppTestCase):
    def test_all_five_screens_are_built_and_home_is_showing(self) -> None:
        self.assertEqual(set(self.app._screens), {key for key, _ in SCREENS})
        self.assertEqual(len(SCREENS), 5)

    def test_every_screen_can_be_shown_on_a_fresh_machine_with_no_data(self) -> None:
        for key, _label in SCREENS:
            with self.subTest(screen=key):
                self.app.show(key)  # must not raise with no prices, no reports, no history
                self.root.update()

    def test_showing_a_screen_highlights_its_button(self) -> None:
        self.app.show("try_a_plan")
        self.assertTrue(self.app.nav._buttons["try_a_plan"].instate(["pressed"]))
        self.assertFalse(self.app.nav._buttons["home"].instate(["pressed"]))

    def test_looking_at_the_plan_screen_creates_no_files(self) -> None:
        self.app.show("try_a_plan")
        self.assertFalse((self.repo_root / "data").exists())
        self.assertFalse((self.repo_root / "experiments").exists())

    def test_the_window_is_never_smaller_than_what_the_results_need(self) -> None:
        self.root.update()
        min_w, min_h = self.root.minsize()
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.assertEqual(min_w, min(MIN_WIDTH, screen_w - 80 if screen_w - 80 < MIN_WIDTH + 40 else MIN_WIDTH + 40))
        self.assertLessEqual(min_h, MIN_HEIGHT)
        self.assertLessEqual(self.root.winfo_width(), screen_w)
        self.assertLessEqual(self.root.winfo_height(), screen_h)

    def test_an_unexpected_error_in_a_button_is_logged_and_explained_not_swallowed(self) -> None:
        with mock.patch("desktop_app.app.messagebox.showerror") as dialog:
            with self.assertLogs("desktop_app.app", level="ERROR") as logged:
                try:
                    raise ValueError("boom")
                except ValueError as exc:
                    self.app._on_callback_exception(type(exc), exc, exc.__traceback__)
        dialog.assert_called_once()
        title, text = dialog.call_args.args
        self.assertEqual(title, "Something went wrong")
        self.assertIn("Nothing was bought or sold", text)
        self.assertIn("logs/desktop_app.log", text)
        self.assertIn("boom", "".join(logged.output))

    def test_the_comparison_data_is_absent_on_a_machine_without_it(self) -> None:
        self.assertIsNone(self.app.reference)

    def test_the_comparison_data_is_read_once_not_on_every_result(self) -> None:
        with mock.patch("desktop_app.app.alternatives.load_reference", return_value=None) as loader:
            self.app.reference
            self.app.reference
            self.app.reference
        self.assertEqual(loader.call_count, 1)

    def test_the_comparison_data_is_looked_for_in_the_repositorys_reference_folder(self) -> None:
        with mock.patch("desktop_app.app.alternatives.load_reference", return_value=None) as loader:
            self.app.reference
        loader.assert_called_once_with(self.repo_root / "data" / "reference")

    def test_tk_is_told_to_use_that_handler(self) -> None:
        self.assertEqual(self.root.report_callback_exception, self.app._on_callback_exception)


if __name__ == "__main__":
    unittest.main()
