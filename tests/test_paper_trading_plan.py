"""A rehearsal plan: two ordinary configs, cross-checked to share their data."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crypto_research.config import load_config
from crypto_research.timeutil import DAY_MS
from paper_trading.errors import PlanMismatchError
from paper_trading.plan import build_plan, load_plan, resolved_for_end_time

from .paper_trading_helpers import BENCHMARK_TEMPLATE, REHEARSAL_TEMPLATE, write_config


class PlanLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db_path = str(self.tmp / "paper.sqlite")
        self.experiment_dir = str(self.tmp / "experiments")

    def write(self, template: Path, name: str, **replacements: str) -> Path:
        return write_config(
            self.tmp / name, template, db_path=self.db_path, experiment_dir=self.experiment_dir,
            **replacements,
        )

    def test_a_matching_pair_of_configs_loads_as_one_plan(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        b = self.write(BENCHMARK_TEMPLATE, "b.toml")
        plan = load_plan(r, b)
        self.assertEqual(plan.symbols, ("BTCUSD", "ETHUSD", "SOLUSD"))
        self.assertEqual(plan.interval, "1d")
        self.assertEqual(plan.db_path, self.db_path.replace("\\", "/"))
        self.assertEqual(plan.name, "paper-rehearsal-staged-200usd-weekly-12")
        self.assertIn("rehearsal", plan.describe())

    def test_a_name_override_is_honoured(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        b = self.write(BENCHMARK_TEMPLATE, "b.toml")
        plan = load_plan(r, b, name="my-rehearsal")
        self.assertEqual(plan.name, "my-rehearsal")

    def test_a_different_database_is_refused(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        other_db = str(self.tmp / "other.sqlite").replace("\\", "/")
        b = write_config(
            self.tmp / "b.toml", BENCHMARK_TEMPLATE, db_path=other_db, experiment_dir=self.experiment_dir,
        )
        with self.assertRaises(PlanMismatchError) as caught:
            load_plan(r, b)
        self.assertIn("db_path", str(caught.exception))

    def test_different_symbols_or_interval_are_refused(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        cases = {
            "b_symbols.toml": {'symbols = ["BTCUSD", "ETHUSD", "SOLUSD"]': 'symbols = ["BTCUSD", "ETHUSD"]'},
            "b_interval.toml": {'interval = "1d"': 'interval = "1h"'},
        }
        for name, replacements in cases.items():
            b = self.write(BENCHMARK_TEMPLATE, name, **replacements)
            with self.assertRaises(PlanMismatchError, msg=name):
                load_plan(r, b)

    def test_a_different_start_is_refused(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        b = self.write(BENCHMARK_TEMPLATE, "b.toml", start="2024-01-02T00:00:00Z", end="2024-01-03T00:00:00Z")
        with self.assertRaises(PlanMismatchError) as caught:
            load_plan(r, b)
        self.assertIn("start", str(caught.exception))

    def test_symbol_order_does_not_matter(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        b = self.write(
            BENCHMARK_TEMPLATE, "b.toml",
            **{'symbols = ["BTCUSD", "ETHUSD", "SOLUSD"]': 'symbols = ["SOLUSD", "BTCUSD", "ETHUSD"]'},
        )
        build_plan(load_config(r), load_config(b))  # does not raise

    def test_a_different_experiment_dir_is_refused(self) -> None:
        r = self.write(REHEARSAL_TEMPLATE, "r.toml")
        other_dir = str(self.tmp / "elsewhere").replace("\\", "/")
        text = BENCHMARK_TEMPLATE.read_text(encoding="utf-8")
        b_path = self.tmp / "b.toml"
        b_path.write_text(
            text.replace('db_path = "data/paper_market.sqlite"', f'db_path = "{self.db_path.replace(chr(92), "/")}"')
                .replace("[experiment]", f'[experiment]\nexperiment_dir = "{other_dir}"', 1),
            encoding="utf-8",
        )
        with self.assertRaises(PlanMismatchError):
            load_plan(r, b_path)


class ResolvedForEndTimeTests(unittest.TestCase):
    def test_only_the_end_time_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = str(tmp_path / "paper.sqlite")
            experiment_dir = str(tmp_path / "experiments")
            r = write_config(tmp_path / "r.toml", REHEARSAL_TEMPLATE, db_path=db_path, experiment_dir=experiment_dir)
            config = load_config(r)
            new_end = config.data.start_time + 5 * DAY_MS
            resolved = resolved_for_end_time(config, new_end)
            self.assertEqual(resolved.data.end_time, new_end)
            self.assertEqual(resolved.backtest.end_time, new_end)
            self.assertEqual(resolved.data.start_time, config.data.start_time)
            self.assertEqual(resolved.strategy_name, config.strategy_name)
            self.assertIsNot(resolved, config)  # the original config is untouched
            self.assertNotEqual(config.data.end_time, new_end)


if __name__ == "__main__":
    unittest.main()
