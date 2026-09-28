"""threading_support.run_in_background -- needs a real (never-shown) Tk root."""

from __future__ import annotations

import time
import unittest

from desktop_app.threading_support import run_in_background

from .tk_helpers import TkTestCase


class RunInBackgroundTests(TkTestCase):
    def _pump_until(self, outcome: dict, *, timeout_s: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_s
        while "result" not in outcome and time.monotonic() < deadline:
            self.root.update()
        self.assertIn("result", outcome, "on_done was never called")

    def test_a_successful_job_delivers_its_value_on_the_tk_thread(self) -> None:
        outcome: dict = {}
        run_in_background(self.root, lambda: 42, lambda result: outcome.setdefault("result", result))
        self._pump_until(outcome)
        self.assertTrue(outcome["result"].ok)
        self.assertEqual(outcome["result"].value, 42)

    def test_an_exception_is_delivered_to_on_done_not_raised(self) -> None:
        outcome: dict = {}

        def work():
            raise ValueError("boom")

        run_in_background(self.root, work, lambda result: outcome.setdefault("result", result))
        self._pump_until(outcome)
        self.assertFalse(outcome["result"].ok)
        self.assertIsInstance(outcome["result"].error, ValueError)

    def test_cancel_stops_delivery(self) -> None:
        outcome: dict = {}
        cancel = run_in_background(
            self.root, lambda: 1, lambda result: outcome.setdefault("result", result)
        )
        cancel()
        self.root.update()
        time.sleep(0.3)
        self.root.update()
        self.assertNotIn("result", outcome)


if __name__ == "__main__":
    unittest.main()
