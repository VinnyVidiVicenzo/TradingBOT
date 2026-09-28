"""desktop_app.plan_history -- an append-only, crash-tolerant history file."""

from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from desktop_app import plan_history
from desktop_app.plan_builder import Schedule
from desktop_app.sweep import SweepResult, WindowOutcome

D = Decimal


def _result(amount: str = "200", ending: str = "237.123456789") -> SweepResult:
    return SweepResult(
        amount=D(amount),
        symbols=("BTCUSDT", "ETHUSDT"),
        schedule=Schedule.SPREAD_OVER_WEEKS,
        hold_months=12,
        outcomes=(
            WindowOutcome(1_600_000_000_000, D(amount), D(ending), D("0.31"), 0),
            WindowOutcome(1_602_600_000_000, D(amount), D("64.5"), D("0.7"), 2),
        ),
        skipped=1,
    )


class PlanHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "nested" / plan_history.FILENAME

    def test_a_plan_survives_a_round_trip_exactly(self) -> None:
        original = _result()
        plan_history.append(self.path, original, created_at_iso="2026-09-27T12:00:00Z")
        (stored,) = plan_history.load_all(self.path)
        self.assertEqual(stored.result, original)
        self.assertEqual(stored.created_at_iso, "2026-09-27T12:00:00Z")
        # Decimals come back as the same digits, not a float approximation.
        self.assertEqual(stored.result.outcomes[0].ending_value, D("237.123456789"))

    def test_appending_creates_the_folder_and_keeps_order(self) -> None:
        plan_history.append(self.path, _result("100"), created_at_iso="2026-09-27T12:00:00Z")
        plan_history.append(self.path, _result("300"), created_at_iso="2026-09-28T12:00:00Z")
        amounts = [plan.result.amount for plan in plan_history.load_all(self.path)]
        self.assertEqual(amounts, [D("100"), D("300")])

    def test_each_plan_is_exactly_one_line(self) -> None:
        plan_history.append(self.path, _result(), created_at_iso="2026-09-27T12:00:00Z")
        plan_history.append(self.path, _result(), created_at_iso="2026-09-27T12:01:00Z")
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_a_missing_file_is_an_empty_history(self) -> None:
        self.assertEqual(plan_history.load_all(self.path), [])

    def test_a_line_cut_short_by_a_crash_is_skipped_not_fatal(self) -> None:
        plan_history.append(self.path, _result("100"), created_at_iso="2026-09-27T12:00:00Z")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"version": 1, "created_at": "2026-09-28T1')  # truncated mid-write
        with self.assertLogs("desktop_app.plan_history", level="WARNING") as logged:
            plans = plan_history.load_all(self.path)
        self.assertEqual([p.result.amount for p in plans], [D("100")])
        self.assertIn("skipping unreadable line 2", logged.output[0])

    def test_a_plan_saved_after_a_crash_is_not_lost_with_the_damaged_line(self) -> None:
        plan_history.append(self.path, _result("100"), created_at_iso="2026-09-27T12:00:00Z")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"version": 1, "created_at": "2026-09-28T1')  # cut off, no newline
        plan_history.append(self.path, _result("300"), created_at_iso="2026-09-29T12:00:00Z")
        with self.assertLogs("desktop_app.plan_history", level="WARNING"):
            plans = plan_history.load_all(self.path)
        self.assertEqual([p.result.amount for p in plans], [D("100"), D("300")])

    def test_appending_to_a_well_formed_file_adds_no_blank_line(self) -> None:
        plan_history.append(self.path, _result("100"), created_at_iso="2026-09-27T12:00:00Z")
        plan_history.append(self.path, _result("200"), created_at_iso="2026-09-28T12:00:00Z")
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("\n\n", text)
        self.assertTrue(text.endswith("\n"))

    def test_an_unknown_format_version_is_skipped(self) -> None:
        plan_history.append(self.path, _result("100"), created_at_iso="2026-09-27T12:00:00Z")
        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        record["version"] = 99
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        with self.assertLogs("desktop_app.plan_history", level="WARNING") as logged:
            plans = plan_history.load_all(self.path)
        self.assertEqual(len(plans), 1)
        self.assertIn("unknown history format version 99", logged.output[0])

    def test_a_hand_edited_bad_value_is_skipped(self) -> None:
        plan_history.append(self.path, _result("100"), created_at_iso="2026-09-27T12:00:00Z")
        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        for key, bad in (("amount", "not a number"), ("schedule", "SOMETHING_ELSE"), ("hold_months", 7)):
            broken = dict(record, **{key: bad})
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(broken) + "\n")
        with self.assertLogs("desktop_app.plan_history", level="WARNING") as logged:
            plans = plan_history.load_all(self.path)
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(logged.output), 3)  # every bad line is reported, none is silent

    def test_blank_lines_are_ignored(self) -> None:
        plan_history.append(self.path, _result(), created_at_iso="2026-09-27T12:00:00Z")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("\n\n")
        self.assertEqual(len(plan_history.load_all(self.path)), 1)


if __name__ == "__main__":
    unittest.main()
