"""Temporal leakage, immutable inputs and independent bounded collection."""
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal

from crypto_research.jsonl import locked
from crypto_research.market_universe import SYMBOLS
from crypto_research.models import MarketBar, EXCHANGE_BINANCE_US
from market_capture import background
from paper_trading import historical, research_costs

START = 1704067200000
END = START + 720 * historical.HOUR


class HistoryClient:
    def iter_klines(self, symbol, interval, *, start_time, end_time):
        for i, at in enumerate(range(start_time, end_time + 1, historical.HOUR)):
            value = Decimal(str(100 + math.sin(i / 7) * 4 + i / 100))
            yield MarketBar(EXCHANGE_BINANCE_US, symbol, "1h", at, at + historical.HOUR - 1,
                            value, value + 1, value - 1, value + Decimal("0.1"), Decimal(10 + i % 5))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / "learning_research"
        self.profile = self.root / "fees.json"
        self.costs = research_costs.load(self.profile)

    def fetch(self, days=30):
        return historical.download(self.directory, days=days, at=END, client=HistoryClient())

    def test_download_is_complete_idempotent_and_keeps_practice_untouched(self):
        wallet = self.root / "practice_agent.sqlite"
        wallet.write_bytes(b"unchanged practice history")
        first = self.fetch()
        second = self.fetch()
        self.assertTrue(first["complete"])
        self.assertEqual(first, second)
        self.assertEqual(len(historical.load_rows(self.directory)), 720 * 5)
        self.assertEqual(wallet.read_bytes(), b"unchanged practice history")

    def test_future_or_invalid_bar_is_refused_and_partial_download_is_visible(self):
        class BadClient(HistoryClient):
            def iter_klines(self, *args, **kwargs):
                from dataclasses import replace
                first = next(super().iter_klines(*args, **kwargs))
                yield replace(first, close_time=END + historical.HOUR)
        with self.assertRaises(ValueError):
            historical.download(self.directory, days=30, at=END, client=BadClient())
        saved = json.loads((self.directory / "download.json").read_text())
        self.assertFalse(saved["complete"])
        self.assertIn("error", saved)

    def test_gapped_windows_are_excluded_without_filling_missing_hours(self):
        self.fetch()
        rows = historical.load_rows(self.directory)
        complete, _ = historical.examples(rows, 24, self.costs)
        gapped = [r for r in rows if not (r["symbol"] == "ADAUSD" and r["at"] == START + 200 * historical.HOUR)]
        samples, coverage = historical.examples(gapped, 24, self.costs)
        self.assertGreater(coverage["ADAUSD"]["skipped_gap_windows"], 0)
        self.assertLess(len(samples), len(complete))
        self.assertEqual(coverage["BTCUSD"]["skipped_gap_windows"], 0)

    def test_short_candle_is_reported_and_older_cached_copy_is_excluded(self):
        self.fetch()
        class ShortClient(HistoryClient):
            def iter_klines(self, *args, **kwargs):
                from dataclasses import replace
                for index, bar in enumerate(super().iter_klines(*args, **kwargs)):
                    yield replace(bar, close_time=bar.close_time - 1000) if index == 100 else bar
        manifest = historical.download(self.directory, days=30, at=END, client=ShortClient())
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["coins"]["BTCUSD"]["bars_received"], 719)
        self.assertEqual(manifest["coins"]["BTCUSD"]["rejected_bars"], 1)
        report = historical.evaluate(self.directory, self.profile)
        self.assertEqual(report["tracks"]["1h"]["coverage"]["BTCUSD"]["bars"], 719)
        self.assertGreater(report["tracks"]["1h"]["coverage"]["BTCUSD"]["skipped_gap_windows"], 0)

    def test_future_price_changes_outcome_but_not_features(self):
        self.fetch()
        rows = historical.load_rows(self.directory)
        before, _ = historical.examples(rows, 4, self.costs)
        chosen = before[0]
        target = chosen["label_at"] - historical.HOUR
        changed = [dict(r, close="200", high="201") if r["symbol"] == chosen["symbol"] and r["at"] == target else r for r in rows]
        after, _ = historical.examples(changed, 4, self.costs)
        self.assertEqual(before[0]["x"], after[0]["x"])
        self.assertNotEqual(before[0]["net"], after[0]["net"])
        self.assertEqual(len(before[0]["x"]), len(historical.FEATURES))

    def test_shared_chronological_cutoff_purges_all_future_labels(self):
        self.fetch()
        samples, _ = historical.examples(historical.load_rows(self.directory), 24, self.costs)
        folds = historical.walk_forward(samples)
        for fold in folds:
            self.assertLess(fold["training_last_label"], fold["test_start"])
            self.assertLess(fold["test_last_label"], fold["test_end"])
            self.assertEqual(set(fold["training_coins"]), set(SYMBOLS))
        self.assertEqual(folds[0]["test_end"], folds[1]["test_start"])
        self.assertEqual(folds[1]["test_end"], folds[2]["test_start"])
        changed = [dict(r, y=1 - r["y"]) if r["at"] >= folds[0]["test_start"] else r for r in samples]
        self.assertEqual(folds[0]["weights"], historical.walk_forward(changed)[0]["weights"])

    def test_opportunity_scores_withhold_overlapping_entries_and_stress_costs(self):
        self.fetch()
        samples, _ = historical.examples(historical.load_rows(self.directory), 24, self.costs)
        weights = [3.0] + [0.0] * (len(historical.FEATURES) - 1)
        result = historical.score(samples, weights, 0.5)
        self.assertLess(result["selected"], len(samples) // 20)
        self.assertLess(result["selected_stress_pct"], result["selected_net_pct"])
        self.assertEqual(set(result["coins"]), set(SYMBOLS))

    def test_evaluations_are_archived_with_frozen_data_and_no_practice_changes(self):
        self.fetch()
        wallet = self.root / "practice_agent.sqlite"
        wallet.write_bytes(b"existing wallet")
        with patch("paper_trading.historical.source_identity", return_value="fixed-source"):
            report = historical.evaluate(self.directory, self.profile)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(set(report["tracks"]), {"1h", "4h", "24h"})
        self.assertEqual(historical.latest(self.directory)["id"], report["id"])
        self.assertTrue((self.directory / report["dataset_file"]).exists())
        journal = [json.loads(line) for line in (self.directory / "attempts.jsonl").read_text().splitlines()]
        self.assertEqual([r["status"] for r in journal], ["started", "completed"])
        self.assertEqual(wallet.read_bytes(), b"existing wallet")
        (self.directory / report["dataset_file"]).write_text("changed")
        with self.assertRaisesRegex(ValueError, "Frozen dataset changed"):
            historical.evaluate(self.directory, self.profile)

    def test_failed_evaluation_is_recorded_and_never_becomes_latest(self):
        self.fetch(days=7)
        with self.assertRaises(ValueError):
            historical.evaluate(self.directory, self.profile)
        self.assertIsNone(historical.latest(self.directory))
        saved = list((self.directory / "studies").glob("*.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text())["status"], "failed")


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "capture"

    def test_fixed_process_launch_is_hidden_independent_and_not_duplicated(self):
        with patch("market_capture.background.subprocess.Popen") as popen:
            background.start(self.directory)
            background.start(self.directory)
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:4], ["-m", "market_capture", "collect"])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["close_fds"])
        self.assertNotEqual(kwargs["stdout"], background.subprocess.PIPE)
        background.stop(self.directory)
        self.assertTrue((self.directory / "STOP").exists())

    def test_rotation_preserves_both_segments_and_reports_stopped(self):
        paths = []
        def runner(path, stop, *, seconds, observer):
            paths.append(path)
            path.write_bytes(b"public events")
            observer({"events": 10, "last_received": 100, "counts": {"depth": 10}, "message": "Recording"})
            if len(paths) == 2:
                background.stop(self.directory)
        result = background.collect(self.directory, runner=runner)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(p.exists() for p in paths))
        self.assertFalse(result["active"])
        self.assertEqual(result["completed_events"], 20)
        self.assertIn("Stopped by request", background.status(self.directory)["message"])

    def test_storage_limit_stops_without_removing_history(self):
        self.directory.mkdir()
        existing = self.directory / "stream_old.sqlite"
        existing.write_bytes(b"history")
        with patch("market_capture.background.storage_bytes", return_value=background.DEFAULT_QUOTA), patch("market_capture.background.run") as runner:
            result = background.collect(self.directory, runner=runner)
        runner.assert_not_called()
        self.assertIn("Storage quota reached", result["message"])
        self.assertEqual(existing.read_bytes(), b"history")

    def test_stale_heartbeat_does_not_start_second_owner_or_remove_stop_request(self):
        background.write_status(self.directory, {"active": True, "at": 0, "message": "old"})
        background.stop(self.directory)
        with locked(self.directory / "owner"), patch("market_capture.background.subprocess.Popen") as popen:
            result = background.start(self.directory)
        popen.assert_not_called()
        self.assertIn("owns this directory", result["message"])
        self.assertTrue((self.directory / "STOP").exists())
