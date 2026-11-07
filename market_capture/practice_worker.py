"""Fixed, local paper-session worker; no account access or live execution.

The GUI only toggles saved session flags. One OS lock owns both wallets;
separate threads prevent a slow market request from blocking the other wallet
or the heartbeat. No missed decisions or outcomes are reconstructed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from crypto_research.jsonl import locked
from crypto_research.timeutil import now_ms, to_iso
from paper_trading.practice import PracticeAgent
from paper_trading.swing_strategy import SwingPracticeAgent

FILES = {"hourly": "practice_agent.sqlite", "swing": "swing_practice.sqlite"}


def directory(repo_root):
    return Path(repo_root).resolve() / "data" / "practice_runtime"


def _write(path, body):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(body), encoding="utf-8")
    pending.replace(path)


def status(repo_root):
    """Read heartbeat without creating files or assuming a saved flag is live."""
    path = directory(repo_root) / "worker.json"
    try:
        if path.stat().st_size > 65536:
            raise ValueError("Oversized worker status")
        body = json.loads(path.read_text(encoding="utf-8"))
        body["stale"] = not 0 <= now_ms() - body["at"] < 30_000
        return body
    except (OSError, ValueError, KeyError, TypeError):
        return {"active": False, "stale": True, "sessions": {}}


def describe(repo_root, name):
    body = status(repo_root)
    if not body.get("active") or body["stale"]:
        return "Background worker unconfirmed — press Start to reconnect. Saved wallets and history are kept."
    row = body.get("sessions", {}).get(name, {})
    last = "never" if not row.get("last_success") else to_iso(row["last_success"])
    text = f"Background worker connected | Last successful check (UTC): {last} | " + row.get("message", "Starting checks")
    if row.get("next_attempt") and row.get("failures"):
        text += " | Retry (UTC): " + to_iso(row["next_attempt"])
    if row.get("gap_ms", 0) > 90_000:
        text += f" | Last observation gap: {row['gap_ms'] // 60_000} minutes; missed activity was not replayed."
    return text


def start(repo_root):
    """Start only this fixed module, hidden and detached from the window."""
    repo_root = Path(repo_root).resolve()
    runtime = directory(repo_root)
    runtime.mkdir(parents=True, exist_ok=True)
    with locked(runtime / "launch"):
        try:
            with locked(runtime / "owner"):
                pass
        except OSError:
            return status(repo_root)  # Ownership wins over even a stale heartbeat.
        current = status(repo_root)
        if current.get("starting") and not current["stale"]:
            return current  # Another launcher is waiting for its child to acquire ownership.
        (runtime / "STOP").unlink(missing_ok=True)
        _write(runtime / "worker.json", {"active": True, "starting": True, "at": now_ms(), "sessions": {}})
        command = [sys.executable, "-m", "market_capture.practice_worker", "run", "--repo-root", str(repo_root)]
        options = {"cwd": str(Path(__file__).resolve().parents[1]), "stdin": subprocess.DEVNULL,
                   "stderr": subprocess.STDOUT, "close_fds": True, "shell": False}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        try:
            with (runtime / "worker.log").open("ab") as output:
                subprocess.Popen(command, stdout=output, **options)
        except Exception:
            _write(runtime / "worker.json", {"active": False, "at": now_ms(), "sessions": {}})
            raise
    return status(repo_root)


def stop(repo_root):
    """Maintenance stop only; never changes saved session intent or kills a PID."""
    runtime = directory(repo_root)
    if runtime.exists():
        (runtime / "STOP").write_text("Stop paper worker", encoding="utf-8")


class Session:
    def __init__(self, path, factory, *, clock=now_ms):
        self.path, self.factory, self.clock = path, factory, clock
        self.agent = None
        self.next_attempt, self.failures = 0, 0
        self.last_success, self.last_attempt, self.gap_ms = 0, 0, 0
        self.message, self.busy = "Waiting for a saved session", False
        self.revision = None
        self.lock = threading.Lock()

    def view(self):
        with self.lock:
            return {key: getattr(self, key) for key in ("next_attempt", "failures", "last_success", "last_attempt", "gap_ms", "message", "busy")}

    def tick(self):
        if not self.path.exists():
            return  # A worker never seeds a wallet or changes starting amounts.
        if self.agent is None:
            self.agent = self.factory(self.path)
        before = self.agent.snapshot()
        if not before:
            return
        at = self.clock()
        with self.lock:
            self.last_success = before["last_check"]
            revision = before.get("control_revision", 0)
            if revision != self.revision:
                self.revision = revision
                self.next_attempt, self.failures = 0, 0
            if not before["active"]:
                self.message = before["message"]
                return
            if at < max(self.next_attempt, self.last_success + 60_000):
                return
            self.busy, self.last_attempt = True, at
            self.message = "Checking fresh public prices; no decisions use stale prices."
        try:
            after = self.agent.check(retry_data_errors=True)
            with self.lock:
                self.last_success = after["last_check"]
                if after.get("retry_error") and after["active"]:
                    self.failures += 1
                    self.next_attempt = self.clock() + min(900_000, 60_000 * 2 ** min(self.failures - 1, 4))
                else:
                    self.failures = 0
                    self.next_attempt = self.clock() + 60_000
                if after["last_check"] > before["last_check"] and before["last_check"]:
                    self.gap_ms = after["last_check"] - before["last_check"]
                self.message = after["message"]
        except Exception as exc:
            # Keep a broken wallet isolated. No implicit resume of risk/user pauses.
            with self.lock:
                self.message = f"Check failed: {type(exc).__name__}: {str(exc)[:180]}"
                self.failures += 1
                self.next_attempt = self.clock() + 900_000
        finally:
            with self.lock:
                self.busy = False


def run(repo_root, *, seconds=None, factories=None):
    repo_root = Path(repo_root).resolve()
    runtime = directory(repo_root)
    runtime.mkdir(parents=True, exist_ok=True)
    factories = factories or {"hourly": PracticeAgent, "swing": SwingPracticeAgent}
    with locked(runtime / "owner"):
        sessions = {name: Session(repo_root / "data" / filename, factories[name]) for name, filename in FILES.items()}
        stopping = threading.Event()
        threads = []
        def checks(session):
            while not stopping.is_set() and not (runtime / "STOP").exists():
                try:
                    session.tick()
                except Exception as exc:
                    with session.lock:
                        session.message = f"Session unavailable: {type(exc).__name__}: {str(exc)[:180]}"
                    if stopping.wait(30):
                        break
                stopping.wait(2)
        def heartbeat(active=True):
            _write(runtime / "worker.json", {"active": active, "at": now_ms(), "pid": os.getpid(),
                   "sessions": {name: session.view() for name, session in sessions.items()}})
        try:
            heartbeat()
            for session in sessions.values():
                thread = threading.Thread(target=checks, args=(session,), daemon=True)
                threads.append(thread)
                thread.start()
            deadline = time.monotonic() + seconds if seconds is not None else None
            while not (runtime / "STOP").exists() and (deadline is None or time.monotonic() < deadline):
                heartbeat()
                stopping.wait(2)
        finally:
            stopping.set()
            # Keep ownership and heartbeat until all in-flight checks finish.
            while any(thread.is_alive() for thread in threads):
                heartbeat()
                for thread in threads:
                    thread.join(timeout=0.2)
            heartbeat(active=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent practice-only execution and learning worker")
    parser.add_argument("command", choices=("run", "status", "stop"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--seconds", type=int)
    args = parser.parse_args(argv)
    if args.seconds is not None and not 1 <= args.seconds <= 86400:
        parser.error("seconds must be 1..86400")
    if args.command == "run":
        run(args.repo_root, seconds=args.seconds)
    elif args.command == "stop":
        stop(args.repo_root)
    else:
        print(json.dumps(status(args.repo_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
