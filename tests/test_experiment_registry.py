"""The experiment registry: append-only, and failures are kept."""

from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from crypto_research.backtest.engine import BacktestConfig, Backtester
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.backtest.metrics import compute_metrics
from crypto_research.config import parse_config
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.decimals import dec
from crypto_research.experiments.registry import (
    ExperimentRegistry,
    RunRecord,
    RunStatus,
    config_hash,
)
from crypto_research.experiments.runner import run_experiment
from crypto_research.models import PortfolioHolding
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository, RuleSnapshotRepository
from crypto_research.binanceus.parsing import parse_exchange_info
from crypto_research.strategies import build_strategy
from crypto_research.timeutil import DAY_MS

from .helpers import (
    BTCUSD_EXCHANGE_INFO_ENTRY,
    EPOCH_2024,
    default_rule_book,
    make_bars,
    make_rule_entry,
)
from .test_execution import zero_cost_models


def skeleton(**overrides) -> RunRecord:
    payload = dict(
        run_id="20240101T000000-abcdef12",
        created_at=1_700_000_000_000,
        status=RunStatus.COMPLETED,
        experiment_name="t",
        description="",
        strategy_name="benchmark0_hold",
        strategy_version="1",
        strategy_params={},
        config={"a": 1},
        config_sha256="a" * 64,
        dataset_version={"sha256": "b" * 64, "bar_count": 3},
        eligible_symbols=["BTCUSD"],
        starting_holdings=[],
        starting_cash={"USD": "200"},
        external_contributions=[],
        fee_assumptions={"maker_rate": "0.004"},
        spread_assumptions={},
        slippage_assumptions={},
        latency_assumptions={},
        execution_settings={},
        rate_assumptions_used=[],
        date_range={"start_time": 0, "end_time": 1},
        metrics={"net_return": "0.1", "ending_portfolio_value": "220"},
    )
    payload.update(overrides)
    return RunRecord(**payload)


class AppendOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.registry = ExperimentRegistry(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_run_writes_its_record_and_an_index_line(self) -> None:
        self.registry.write(skeleton())
        run_dir = Path(self._tmp.name) / "runs" / "20240101T000000-abcdef12"
        self.assertTrue((run_dir / "record.json").exists())
        self.assertTrue((run_dir / "metrics.json").exists())
        self.assertTrue((run_dir / "config.resolved.json").exists())
        self.assertEqual(len(self.registry.list_runs()), 1)

    def test_writing_the_same_run_id_twice_is_refused(self) -> None:
        self.registry.write(skeleton())
        with self.assertRaises(FileExistsError):
            self.registry.write(skeleton())

    def test_allocated_run_ids_do_not_collide(self) -> None:
        digest = "c" * 64
        first = self.registry.allocate_run_id(digest, at=1_700_000_000_000)
        self.registry.write(skeleton(run_id=first))
        second = self.registry.allocate_run_id(digest, at=1_700_000_000_000)
        self.assertNotEqual(first, second)

    def test_the_index_accumulates_rather_than_being_rewritten(self) -> None:
        self.registry.write(skeleton(run_id="run-1"))
        self.registry.write(skeleton(run_id="run-2"))
        self.registry.write(skeleton(run_id="run-3"))
        entries = self.registry.list_runs()
        self.assertEqual([e["run_id"] for e in entries], ["run-1", "run-2", "run-3"])

    def test_a_failed_run_is_preserved_with_its_traceback(self) -> None:
        self.registry.write(
            skeleton(
                run_id="failed-1",
                status=RunStatus.FAILED,
                metrics=None,
                error="ConfigError: no bars stored",
                traceback="Traceback (most recent call last): ...",
            )
        )
        record = self.registry.load("failed-1")
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("no bars stored", record["error"])
        self.assertIn("Traceback", record["traceback"])
        self.assertIn(
            "failed-1", [e["run_id"] for e in self.registry.list_runs()]
        )

    def test_auxiliary_outputs_are_written(self) -> None:
        self.registry.write(
            skeleton(run_id="with-outputs"),
            equity_curve_rows=[{"time": 1, "total_value": "100"}],
            order_rows=[{"client_order_id": "o1", "status": "FILLED"}],
            cash_ledger_rows=[{"as_of": 1, "asset": "USD", "amount": "200",
                               "source": "INITIAL", "reason": "seed"}],
        )
        run_dir = Path(self._tmp.name) / "runs" / "with-outputs"
        self.assertTrue((run_dir / "equity_curve.csv").exists())
        self.assertTrue((run_dir / "orders.jsonl").exists())
        self.assertTrue((run_dir / "cash_ledger.csv").exists())

    def test_the_index_entry_carries_the_headline_numbers(self) -> None:
        entry = skeleton().index_entry()
        self.assertEqual(entry["net_return"], "0.1")
        self.assertEqual(entry["ending_portfolio_value"], "220")
        self.assertEqual(entry["strategy"], "benchmark0_hold@1")


class ConfigHashTests(unittest.TestCase):
    def test_identical_configs_hash_identically(self) -> None:
        self.assertEqual(config_hash({"a": 1, "b": 2}), config_hash({"b": 2, "a": 1}))

    def test_a_changed_value_changes_the_hash(self) -> None:
        self.assertNotEqual(config_hash({"a": 1}), config_hash({"a": 2}))


CONFIG_TEXT = """
[experiment]
name = "registry-integration"
description = "end to end"

[data]
symbols = ["BTCUSD"]
interval = "1d"
start = "2024-01-01T00:00:00Z"
end = "2024-01-11T00:00:00Z"
db_path = "{db}"

[portfolio]
valuation_asset = "USD"
initial_cash = {{ USD = "100" }}

[strategy]
name = "benchmark1_staged_deployment"

[strategy.params]
symbols = ["BTCUSD"]
tranche_count = 2
cadence_days = 3

[costs.fees]
maker_rate = "0"
taker_rate = "0"
source = "TEST"

[costs.spread]
default_bps = "0"
prefer_observed = false

[costs.slippage]
model = "fixed"
bps = "0"

[costs.latency]
decision_to_submit_ms = 0

[fill]
model = "full"
"""


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "market.sqlite"
        with open_database(self.db_path) as db:
            BarRepository(db).upsert_many(make_bars("BTCUSD", ["100"] * 10))
            RuleSnapshotRepository(db).upsert_many(
                parse_exchange_info(
                    {"serverTime": 1, "symbols": [BTCUSD_EXCHANGE_INFO_ENTRY]},
                    received_at=1,
                )
            )
        self.registry = ExperimentRegistry(self.root / "experiments")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _config(self, **overrides):
        text = CONFIG_TEXT.format(db=str(self.db_path).replace("\\", "\\\\"))
        payload = tomllib.loads(text)
        for key, value in overrides.items():
            payload["data"][key] = value
        return parse_config(payload, source_path="<test>")

    def test_a_successful_run_is_recorded_completely(self) -> None:
        record, result, metrics = run_experiment(self._config(), registry=self.registry)
        self.assertEqual(record.status, RunStatus.COMPLETED)
        self.assertIsNotNone(result)
        self.assertIsNotNone(metrics)
        self.assertEqual(record.eligible_symbols, ["BTCUSD"])
        self.assertEqual(record.starting_cash, {"USD": "100"})
        self.assertEqual(record.fee_assumptions["default"]["source"], "TEST")
        self.assertEqual(record.dataset_version["bar_count"], 10)
        self.assertEqual(record.date_range["interval"], "1d")
        self.assertEqual(metrics.total_purchased_capital, dec("100"))

    def test_the_record_on_disk_round_trips(self) -> None:
        record, _, _ = run_experiment(self._config(), registry=self.registry)
        stored = self.registry.load(record.run_id)
        self.assertEqual(stored["config_sha256"], record.config_sha256)
        self.assertEqual(stored["status"], "COMPLETED")
        self.assertIn("created_at_iso", stored)

    def test_two_runs_of_the_same_config_are_both_kept(self) -> None:
        first, _, _ = run_experiment(self._config(), registry=self.registry)
        second, _, _ = run_experiment(self._config(), registry=self.registry)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.config_sha256, second.config_sha256)
        self.assertEqual(len(self.registry.list_runs()), 2)

    def test_two_runs_of_the_same_config_produce_identical_metrics(self) -> None:
        _, _, first = run_experiment(self._config(), registry=self.registry)
        _, _, second = run_experiment(self._config(), registry=self.registry)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_a_run_with_missing_data_is_recorded_as_failed_not_lost(self) -> None:
        record, result, metrics = run_experiment(
            self._config(symbols=["ETHUSD"]), registry=self.registry
        )
        self.assertEqual(record.status, RunStatus.FAILED)
        self.assertIsNone(result)
        self.assertIsNone(metrics)
        self.assertIn("ETHUSD", record.error)
        stored = self.registry.load(record.run_id)
        self.assertEqual(stored["status"], "FAILED")
        self.assertIsNotNone(stored["traceback"])

    def test_the_recorded_orders_file_matches_the_run(self) -> None:
        record, result, _ = run_experiment(self._config(), registry=self.registry)
        path = self.registry.runs_dir / record.run_id / "orders.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), len(result.orders))
        self.assertEqual(rows[0]["symbol"], "BTCUSD")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
