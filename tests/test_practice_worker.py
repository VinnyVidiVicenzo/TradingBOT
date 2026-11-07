"""Independent lifecycle, ownership, outage recovery and unchanged ledgers."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from crypto_research.errors import TransportError
from crypto_research.jsonl import locked
from market_capture import practice_worker as worker
from paper_trading.practice import PracticeAgent
from paper_trading.swing_strategy import SwingPracticeAgent
from .test_practice_agent import Prices
from .test_swing_strategy import PublicPrices, report


class FakeAgent:
    def __init__(self, path):
        self.state = {"active": True, "last_check": 0, "message": "Ready", "cash": "1234"}
        self.calls = 0
        self.failure = False

    def snapshot(self):
        return dict(self.state)

    def check(self, *, retry_data_errors):
        assert retry_data_errors
        self.calls += 1
        if self.failure:
            self.state["retry_error"] = {"at": 1}
            self.state["message"] = "Market data unavailable"
        else:
            self.state["last_check"] = 100_000
            self.state.pop("retry_error", None)
        return self.snapshot()


class PracticeWorkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def session(self, *, clock=lambda: 100_000):
        path = self.root / "wallet.sqlite"
        path.touch()
        return worker.Session(path, FakeAgent, clock=clock)

    def test_reading_unstarted_status_and_description_creates_nothing(self):
        self.assertFalse(worker.status(self.root)["active"])
        self.assertIn("unconfirmed", worker.describe(self.root, "swing"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_session_never_creates_missing_wallet(self):
        session = worker.Session(self.root / "missing.sqlite", FakeAgent)
        session.tick()
        self.assertIsNone(session.agent)
        self.assertFalse(session.path.exists())

    def test_repeated_tick_never_repeats_success_in_same_minute(self):
        session = self.session()
        session.tick()
        session.tick()
        self.assertEqual(session.agent.calls, 1)
        self.assertEqual(session.agent.state["cash"], "1234")
        self.assertEqual(session.view()["last_success"], 100_000)

    def test_outage_backoff_and_explicit_pause_are_respected(self):
        at = [100_000]
        session = self.session(clock=lambda: at[0])
        session.agent = FakeAgent(session.path)
        session.agent.failure = True
        session.tick()
        self.assertEqual(session.next_attempt, 160_000)
        session.tick()
        self.assertEqual(session.agent.calls, 1)
        at[0] = 160_000
        session.tick()
        self.assertEqual(session.next_attempt, 280_000)
        session.agent.state.update(active=False, control_revision=1, message="User paused")
        at[0] = 300_000
        session.tick()
        self.assertEqual(session.agent.calls, 2)
        self.assertEqual(session.view()["message"], "User paused")
        session.agent.state.update(active=True, control_revision=2)
        session.agent.failure = False
        session.tick()
        self.assertEqual(session.agent.calls, 3)
        self.assertEqual(session.failures, 0)

    def test_launcher_is_fixed_hidden_and_does_not_launch_twice(self):
        with patch.object(worker.subprocess, "Popen") as popen:
            worker.start(self.root)
            worker.start(self.root)
        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertEqual(command[1:4], ["-m", "market_capture.practice_worker", "run"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        if worker.os.name == "nt":
            self.assertTrue(popen.call_args.kwargs["creationflags"] & worker.subprocess.CREATE_NO_WINDOW)

    def test_stale_heartbeat_never_overrides_owner_lock(self):
        runtime = worker.directory(self.root)
        runtime.mkdir(parents=True)
        worker._write(runtime / "worker.json", {"active": True, "at": 1, "sessions": {}})
        with locked(runtime / "owner"), patch.object(worker.subprocess, "Popen") as popen:
            worker.start(self.root)
        popen.assert_not_called()
        self.assertTrue(worker.status(self.root)["stale"])

    def test_worker_heartbeat_and_other_session_continue_during_slow_check(self):
        data = self.root / "data"
        data.mkdir()
        for filename in worker.FILES.values():
            (data / filename).touch()
        entered, release, other = threading.Event(), threading.Event(), threading.Event()
        class Slow(FakeAgent):
            def check(self, **kwargs):
                entered.set()
                release.wait(8)
                return super().check(**kwargs)
        class Fast(FakeAgent):
            def check(self, **kwargs):
                other.set()
                return super().check(**kwargs)
        thread = threading.Thread(target=worker.run, args=(self.root,), kwargs={"factories": {"hourly": Slow, "swing": Fast}})
        thread.start()
        try:
            self.assertTrue(entered.wait(4))
            self.assertTrue(other.wait(4))
            deadline = time.monotonic() + 5
            while not worker.status(self.root).get("sessions", {}).get("hourly", {}).get("busy") and time.monotonic() < deadline:
                threading.Event().wait(0.05)
            status = worker.status(self.root)
            self.assertTrue(status["active"])
            self.assertFalse(status["stale"])
            self.assertTrue(status["sessions"]["hourly"]["busy"])
        finally:
            worker.stop(self.root)
            release.set()
            thread.join(8)
        self.assertFalse(thread.is_alive())
        self.assertFalse(worker.status(self.root)["active"])

    def test_restart_records_gap_without_replaying_checks(self):
        session = self.session(clock=lambda: 1_000_000)
        session.agent = FakeAgent(session.path)
        session.agent.state["last_check"] = 10_000
        session.tick()
        self.assertEqual(session.agent.calls, 1)
        self.assertEqual(session.gap_ms, 90_000)


class AgentRecoveryTests(unittest.TestCase):
    setUp = PracticeWorkerTests.setUp
    def test_hourly_outage_preserves_active_cash_history_then_recovers(self):
        agent = PracticeAgent(self.root / "hourly.sqlite")
        before = agent.set_active(True)
        prices = Prices()
        with patch("paper_trading.practice.observe", side_effect=TransportError("Offline")):
            after = agent.check(clock=lambda: prices.at, retry_data_errors=True)
        self.assertTrue(after["active"])
        self.assertIn("retry_error", after)
        for key in ("cash", "positions", "fees", "last_check", "activity", "learning"):
            self.assertEqual(after[key], before[key])
        after = agent.check(client=prices, clock=lambda: prices.at, retry_data_errors=True)
        self.assertGreater(after["last_check"], 0)
        self.assertNotIn("retry_error", after)
        count = len(after["activity"])
        again = agent.check(client=prices, clock=lambda: prices.at, retry_data_errors=True)
        self.assertEqual(len(again["activity"]), count)

    def test_swing_outage_preserves_fixed_model_and_recovers(self):
        from paper_trading import research_costs
        agent = SwingPracticeAgent(self.root / "swing.sqlite")
        agent.initialize("1000", report())
        research_costs.store(self.root / "research_costs.json", report()["cost_profile"])
        before = agent.set_active(True)
        prices = PublicPrices()
        with patch("paper_trading.swing_strategy.fetch", side_effect=TransportError("Offline")):
            after = agent.check(clock=lambda: prices.at, retry_data_errors=True)
        self.assertTrue(after["active"])
        for key in ("cash", "positions", "fees", "last_check", "activity", "config"):
            self.assertEqual(after[key], before[key])
        after = agent.check(client=prices, clock=lambda: prices.at, retry_data_errors=True)
        self.assertGreater(after["last_check"], 0)
        self.assertNotIn("retry_error", after)
        self.assertEqual(after["config"], before["config"])

    def test_pause_then_resume_during_fetch_discards_old_observations(self):
        agent = PracticeAgent(self.root / "hourly.sqlite")
        before = agent.set_active(True)
        prices = Prices()
        from paper_trading.practice import observe
        def changed(*args, **kwargs):
            result = observe(*args, **kwargs)
            agent.set_active(False)
            agent.set_active(True)
            return result
        with patch("paper_trading.practice.observe", side_effect=changed):
            after = agent.check(client=prices, clock=lambda: prices.at, retry_data_errors=True)
        self.assertEqual(after["cash"], before["cash"])
        self.assertEqual(after["activity"], before["activity"])
        self.assertEqual(after["last_check"], 0)

    def test_pause_during_failed_fetch_is_not_reactivated(self):
        agent = SwingPracticeAgent(self.root / "swing.sqlite")
        agent.initialize("1000", report())
        agent.set_active(True)
        prices = PublicPrices()
        def failed(*args, **kwargs):
            agent.set_active(False)
            raise TransportError("Offline")
        with patch("paper_trading.swing_strategy.fetch", side_effect=failed):
            after = agent.check(clock=lambda: prices.at, retry_data_errors=True)
        self.assertFalse(after["active"])
        self.assertNotIn("retry_error", after)
        self.assertEqual(after["cash"], "1000")
