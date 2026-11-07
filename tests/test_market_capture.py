"""Recorder boundaries and causal execution estimates with synthetic streams."""
import ast
import json
from pathlib import Path
import tempfile
import threading
import unittest

from crypto_research.safety import audit, MARKET_CAPTURE_RULES
from market_capture import service, execution


def trade(seq=1):
    return json.dumps({"stream": "btcusd@aggTrade", "data": {
        "e": "aggTrade", "s": "BTCUSD", "E": 1000, "T": 999, "a": seq, "p": "100", "q": "1", "m": False}})


def depth(update=1, qty="10"):
    return json.dumps({"stream": "btcusd@depth20@100ms", "data": {
        "lastUpdateId": update, "bids": [["100", qty]], "asks": [["100.01", qty]]}})


class CaptureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "capture.sqlite"

    def test_public_streams_are_fixed_and_other_streams_refuse(self):
        self.assertTrue(service.URI.startswith("wss://stream.binance.us:9443/stream?streams="))
        self.assertEqual(len(service.STREAMS), 15)
        bad = json.loads(trade())
        bad["stream"] = "other@aggTrade"
        with self.assertRaises(KeyError):
            service.normalize(json.dumps(bad))
        bad = json.loads(trade())
        bad["data"]["s"] = "ETHUSD"
        with self.assertRaises(ValueError):
            service.normalize(json.dumps(bad))

    def test_partial_depth_has_no_fabricated_exchange_timestamp(self):
        symbol, kind, source, seq = service.normalize(depth())
        self.assertEqual((symbol, kind, source, seq), ("BTCUSD", "depth", None, 1))

    def test_bad_numeric_values_and_crossed_books_refuse(self):
        for field, value in (("p", "NaN"), ("q", "-1"), ("m", "false"), ("a", True)):
            frame = json.loads(trade())
            frame["data"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                service.normalize(json.dumps(frame))
        frame = json.loads(depth())
        frame["data"]["asks"][0][0] = "90"
        with self.assertRaises(ValueError):
            service.normalize(json.dumps(frame))

    def test_sequence_gap_is_retained_and_stop_is_responsive(self):
        stop = threading.Event()
        payloads = [trade(1), trade(3), depth(1), depth(90)]
        calls = []
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def recv(self, timeout):
                if payloads:
                    return payloads.pop(0)
                stop.set()
                raise TimeoutError()
        def connector(uri, **kwargs):
            calls.append((uri, kwargs))
            return Connection()
        service.run(self.path, stop, seconds=5, connector=connector, clock=lambda: 2000)
        db = service.sqlite3.connect(self.path)
        try:
            kinds = [row[0] for row in db.execute("SELECT kind FROM stream_events ORDER BY id")]
        finally:
            db.close()
        self.assertEqual(kinds.count("GAP"), 1)  # partial-depth IDs need not be consecutive
        self.assertEqual(kinds.count("depth"), 2)
        self.assertIsNone(calls[0][1]["proxy"])
        self.assertEqual(calls[0][0], service.URI)
        self.assertIn("Stopped", service.snapshot(self.path)["message"])

    def test_event_cap_stops_without_deleting_history(self):
        store = service.Store(self.path)
        try:
            store.append(1, "session", "BTCUSD", "trade", 1, trade(), limit=1)
            with self.assertRaises(service.CaptureFull):
                store.append(2, "session", "BTCUSD", "trade", 2, trade(2), limit=1)
            self.assertEqual(store.db.execute("SELECT count(*) FROM stream_events").fetchone()[0], 1)
        finally:
            store.close()

    def record_depth(self, *, gap=False, stale=False):
        store = service.Store(self.path)
        store.append(1000, "a", "BTCUSD", "depth", None, depth(1))
        store.append(1100, "a", "BTCUSD", "depth", None, depth(2))
        if gap:
            store.append(2000, "a", None, "GAP", None, "{}")
        store.append(62_101 if stale else 61_100, "a", "BTCUSD", "depth", None, depth(3))
        store.close()

    def test_replay_uses_later_books_and_stress_includes_extra_costs(self):
        self.record_depth()
        result = execution.replay(self.path, "BTCUSD", "50")
        self.assertEqual(result["entry_at"], 1100)
        self.assertEqual(result["exit_at"], 61_100)
        self.assertEqual(result["status"], "estimated_full_fill")
        self.assertLess(float(result["return_pct"]), 0)
        self.assertLess(float(result["stress"]["return_pct"]), float(result["return_pct"]))
        self.assertIn("Not modeled", result["passive_fills"])

    def test_replay_withholds_gaps(self):
        self.record_depth(gap=True)
        self.assertEqual(execution.replay(self.path, "BTCUSD", "50")["status"], "capture_gap_withheld")

    def test_replay_withholds_stale_future_snapshot(self):
        self.record_depth(stale=True)
        self.assertEqual(execution.replay(self.path, "BTCUSD", "50")["status"], "stale_depth_window")

    def test_insufficient_depth_is_not_a_profitable_full_fill(self):
        entry = json.loads(depth(qty="0.01"))["data"]
        result = execution.roundtrip(entry, entry, "100", "0.001", "0")
        self.assertEqual(result["status"], "insufficient_entry_depth")
        self.assertIsNone(result["return_pct"])
        big = json.loads(depth(qty="100"))["data"]
        result = execution.roundtrip(big, entry, "100", "0.001", "0")
        self.assertEqual(result["status"], "insufficient_exit_depth")


class CaptureSafetyTests(unittest.TestCase):
    def test_capture_zone_has_no_authentication_or_order_path(self):
        root = Path(__file__).resolve().parents[1] / "market_capture"
        self.assertTrue(audit(root, rules=MARKET_CAPTURE_RULES).ok)
        allowed = {"__future__", "argparse", "json", "pathlib", "sqlite3", "threading", "time", "uuid",
                   "crypto_research", "websockets"}
        for path in root.glob("*.py"):
            approved = allowed | {"os", "subprocess", "sys"} if path.name in ("background.py", "practice_worker.py") else allowed
            if path.name == "practice_worker.py":
                approved |= {"paper_trading"}
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], approved)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(node.module.split(".")[0], approved)
            self.assertNotIn(".send(", path.read_text())


if __name__ == "__main__":
    unittest.main()
