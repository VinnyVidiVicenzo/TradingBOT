"""One tick at a time: idempotent, consistent across symbols, never half-run.

These drive the real ``Downloader``, the real ``run_experiment`` and a real
``ExperimentRegistry`` on a temporary database and directory; only the network
boundary (``FakeClient``) is replaced. A tick's numbers therefore come from the
same code a backtest uses, not from a copy of it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crypto_research.config import load_config
from crypto_research.experiments.registry import ExperimentRegistry
from crypto_research.timeutil import DAY_MS
from paper_trading.plan import build_plan
from paper_trading.runner import (
    STATUS_FEED_ERROR,
    STATUS_NO_DATA_YET,
    STATUS_NO_NEW_BAR,
    STATUS_OK,
    latest_completed,
    tick,
)
from paper_trading.ticklog import path_for, read_all

from .paper_trading_helpers import (
    BENCHMARK_TEMPLATE,
    BrokenClient,
    FakeClient,
    REHEARSAL_TEMPLATE,
    START,
    write_config,
)


class RehearsalHarness:
    def __init__(self, testcase: unittest.TestCase, *, tranche_count: int = 4, cadence_days: int = 1) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db_path = str(tmp / "paper.sqlite")
        self.experiment_dir = str(tmp / "experiments")
        r = write_config(
            tmp / "r.toml", REHEARSAL_TEMPLATE, db_path=self.db_path, experiment_dir=self.experiment_dir,
            **{"tranche_count = 12": f"tranche_count = {tranche_count}",
               "cadence_days = 7": f"cadence_days = {cadence_days}"},
        )
        b = write_config(tmp / "b.toml", BENCHMARK_TEMPLATE, db_path=self.db_path, experiment_dir=self.experiment_dir)
        self.plan = build_plan(load_config(r), load_config(b))
        self.registry = ExperimentRegistry(self.experiment_dir)
        self.client = FakeClient()

    def tick(self, now: int, *, client=None):
        return tick(self.plan, client=client or self.client, registry=self.registry, now=now)

    def ticks(self):
        return read_all(path_for(self.experiment_dir, self.plan.name))


class FirstTicksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = RehearsalHarness(self)

    def test_before_any_bar_exists_nothing_is_run(self) -> None:
        result = self.h.tick(START + 100)
        self.assertEqual(result.status, STATUS_NO_DATA_YET)
        self.assertIsNone(result.rehearsal)
        self.assertIsNone(result.benchmark)
        self.assertEqual(self.h.registry.list_runs(), [])

    def test_the_first_common_closed_day_runs_both_tracks(self) -> None:
        self.h.client.add_day(["60000", "3000", "150"])
        result = self.h.tick(START + DAY_MS + 100)
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual((result.rehearsal.status, result.rehearsal.reused), ("COMPLETED", False))
        self.assertEqual((result.benchmark.status, result.benchmark.reused), ("COMPLETED", False))
        self.assertEqual(len(self.h.registry.list_runs()), 2)


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = RehearsalHarness(self)
        self.h.client.add_day(["60000", "3000", "150"])
        self.first = self.h.tick(START + DAY_MS + 100)
        self.assertEqual(self.first.status, STATUS_OK)

    def test_re_ticking_with_no_new_bar_creates_no_new_run(self) -> None:
        result = self.h.tick(START + DAY_MS + 5000)
        self.assertEqual(result.status, STATUS_NO_NEW_BAR)
        self.assertTrue(result.rehearsal.reused and result.benchmark.reused)
        self.assertEqual(result.rehearsal.run_id, self.first.rehearsal.run_id)
        self.assertEqual(len(self.h.registry.list_runs()), 2)  # still just the first pair

    def test_ticking_many_times_in_a_row_still_creates_only_one_pair(self) -> None:
        for _ in range(5):
            self.h.tick(START + DAY_MS + 5000)
        self.assertEqual(len(self.h.registry.list_runs()), 2)

    def test_a_fresh_client_object_each_time_does_not_confuse_the_dedupe(self) -> None:
        """A real process restart hands tick() a brand-new client and registry object each time."""
        registry_again = ExperimentRegistry(self.h.experiment_dir)
        result = tick(self.h.plan, client=FakeClient(), registry=registry_again, now=START + DAY_MS + 9000)
        self.assertEqual(result.status, STATUS_NO_NEW_BAR)
        self.assertEqual(len(registry_again.list_runs()), 2)


class PartialCrossSectionTests(unittest.TestCase):
    """A decision must never run on some symbols' new price and others' stale one."""

    def setUp(self) -> None:
        self.h = RehearsalHarness(self)

    def test_only_one_symbol_posting_is_not_enough(self) -> None:
        self.h.client.closes_by_symbol["BTCUSD"].append("60000")
        result = self.h.tick(START + DAY_MS + 100)
        self.assertEqual(result.status, STATUS_NO_DATA_YET)  # ETH and SOL have no bar at all yet

    def test_the_common_end_time_is_the_earliest_of_the_three(self) -> None:
        self.h.client.add_day(["60000", "3000", "150"])  # day 1, all three
        first = self.h.tick(START + DAY_MS + 100)
        self.assertEqual(first.status, STATUS_OK)

        self.h.client.closes_by_symbol["BTCUSD"].append("61000")  # only BTC posts day 2
        stalled = self.h.tick(START + 2 * DAY_MS + 100)
        self.assertEqual(stalled.status, STATUS_NO_NEW_BAR)
        self.assertEqual(stalled.end_time, first.end_time)  # did not advance past day 1

        self.h.client.closes_by_symbol["ETHUSD"].append("3100")
        self.h.client.closes_by_symbol["SOLUSD"].append("140")  # now all three have day 2
        caught_up = self.h.tick(START + 2 * DAY_MS + 200)
        self.assertEqual(caught_up.status, STATUS_OK)
        self.assertGreater(caught_up.end_time, first.end_time)
        self.assertEqual(len(self.h.registry.list_runs()), 4)


class FeedFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = RehearsalHarness(self)

    def test_a_feed_failure_runs_neither_track_and_is_logged(self) -> None:
        result = self.h.tick(START + DAY_MS + 100, client=BrokenClient())
        self.assertEqual(result.status, STATUS_FEED_ERROR)
        self.assertIsNone(result.rehearsal)
        self.assertIsNone(result.benchmark)
        self.assertEqual(self.h.registry.list_runs(), [])
        self.assertIn("TransportError", result.detail)

    def test_recovery_after_a_failed_tick_runs_normally(self) -> None:
        self.h.tick(START + DAY_MS + 100, client=BrokenClient())
        self.h.client.add_day(["60000", "3000", "150"])
        result = self.h.tick(START + DAY_MS + 200)
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(len(self.h.registry.list_runs()), 2)

    def test_a_feed_failure_never_reaches_the_experiment_registry_as_a_failed_run(self) -> None:
        """A network hiccup is not the same fact as a strategy or config bug; keep them apart."""
        self.h.tick(START + DAY_MS + 100, client=BrokenClient())
        self.assertEqual(self.h.registry.list_runs(), [])


class TickLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = RehearsalHarness(self)

    def test_every_tick_is_logged_including_ones_that_ran_nothing(self) -> None:
        self.h.tick(START + 100)  # NO_DATA_YET
        self.h.client.add_day(["60000", "3000", "150"])
        self.h.tick(START + DAY_MS + 100)  # OK
        self.h.tick(START + DAY_MS + 200)  # NO_NEW_BAR
        self.h.tick(START + DAY_MS + 300, client=BrokenClient())  # FEED_ERROR
        entries = self.h.ticks()
        self.assertEqual([e.status for e in entries], [
            STATUS_NO_DATA_YET, STATUS_OK, STATUS_NO_NEW_BAR, STATUS_FEED_ERROR,
        ])
        self.assertEqual(entries[1].rehearsal_status, "COMPLETED")
        self.assertIsNotNone(entries[1].rehearsal_run_id)
        self.assertIsNone(entries[3].rehearsal_run_id)

    def test_the_log_is_append_only_across_process_restarts(self) -> None:
        """Every call opens the log fresh, as a real scheduled restart would."""
        self.h.tick(START + 100)
        self.h.tick(START + 200)
        self.assertEqual(len(self.h.ticks()), 2)


class LatestCompletedTests(unittest.TestCase):
    def test_returns_none_before_anything_has_run(self) -> None:
        h = RehearsalHarness(self)
        self.assertIsNone(latest_completed(h.registry, h.plan.rehearsal.name))

    def test_returns_the_full_metrics_of_the_newest_completed_run(self) -> None:
        h = RehearsalHarness(self)
        h.client.add_day(["60000", "3000", "150"])
        h.tick(START + DAY_MS + 100)
        h.client.add_day(["61000", "3100", "155"])
        h.tick(START + 2 * DAY_MS + 100)
        record = latest_completed(h.registry, h.plan.rehearsal.name)
        self.assertIsNotNone(record)
        self.assertIn("metrics", record)
        self.assertEqual(record["metrics"]["accepted_orders"], 3)  # second batch would arrive after the run end
        self.assertEqual(record["metrics"]["rejections_by_reason"]["OUTSIDE_RUN_WINDOW"], 3)


if __name__ == "__main__":
    unittest.main()
