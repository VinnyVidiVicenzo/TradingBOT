"""Explicit, bounded public collector independent of the desktop process.

Only a fixed module command is launched; no command text or shell is accepted.
Stop requests are files, never signals sent to a possibly reused process ID.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from crypto_research.jsonl import locked
from crypto_research.timeutil import now_ms
from .service import run

DEFAULT_QUOTA = 512 * 1024 * 1024
RESERVE = 16 * 1024 * 1024
MAX_SEGMENTS = 256


def write_status(directory, payload):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    pending = directory / "collector.pending"
    pending.write_text(json.dumps({"version": 1, "at": now_ms(), **payload}), encoding="utf-8")
    pending.replace(directory / "collector.json")


def status(directory):
    path = Path(directory) / "collector.json"
    if not path.exists():
        return {"active": False, "message": "Background collection has not been started."}
    if path.stat().st_size > 65536:
        raise ValueError("Collector status is oversized")
    result = json.loads(path.read_text(encoding="utf-8"))
    result["stale"] = not 0 <= now_ms() - result["at"] < 45_000
    if result.get("active") and result["stale"]:
        result["message"] = "Heartbeat is old; collection is unconfirmed. Check the collector log or restart it."
    return result


def storage_bytes(directory):
    return sum(p.stat().st_size for p in Path(directory).iterdir() if p.is_file())


class StopRequest:
    def __init__(self, directory, quota):
        self.directory, self.quota = Path(directory), quota
        self.reason, self.checked = None, 0.0
        self.event = threading.Event()

    def is_set(self):
        if (self.directory / "STOP").exists():
            self.reason = "Stopped by request."
        if time.monotonic() - self.checked > 1:
            self.checked = time.monotonic()
            if storage_bytes(self.directory) >= self.quota - RESERVE:
                self.reason = "Storage quota reached. Archive captures and choose a new directory; no history was deleted."
        return self.reason is not None

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < end:
            self.event.wait(min(0.25, max(0, end - time.monotonic())))
        return self.is_set()


def collect(directory, *, quota=DEFAULT_QUOTA, segment_seconds=900, runner=run, seconds=None):
    directory = Path(directory)
    if not 32 * 1024 * 1024 <= quota <= 4 * 1024**3 or not 1 <= segment_seconds <= 3600:
        raise ValueError("Invalid collector limits")
    if seconds is not None and not 1 <= seconds <= 86400:
        raise ValueError("Invalid optional collector duration")
    directory.mkdir(parents=True, exist_ok=True)
    # A second launch cannot become another writer, even after stale heartbeats.
    with locked(directory / "owner"):
        stop = StopRequest(directory, quota)
        deadline = time.monotonic() + seconds if seconds is not None else None
        total = {"active": True, "pid": os.getpid(), "quota": quota, "completed_events": 0,
                 "message": "Starting independent public collection."}
        try:
            while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
                files = list(directory.glob("stream_*.sqlite"))
                if len(files) >= MAX_SEGMENTS:
                    stop.reason = "Segment limit reached. Archive captures before starting a new directory; history was kept."
                    break
                path = directory / ("stream_" + str(now_ms()) + "_" + uuid.uuid4().hex[:8] + ".sqlite")
                def observed(sample):
                    total.update(segment=path.name, segment_events=sample["events"],
                                 last_received=sample["last_received"], message=sample["message"],
                                 counts=sample["counts"], storage_bytes=storage_bytes(directory))
                    write_status(directory, total)
                remaining = max(1, int(deadline - time.monotonic())) if deadline else segment_seconds
                runner(path, stop, seconds=min(segment_seconds, remaining), observer=observed)
                total["completed_events"] += total.get("segment_events", 0)
                total["segment_events"] = 0
            total.update(active=False, message=stop.reason or "Collector duration completed; captures preserved.")
        except Exception as exc:
            total.update(active=False, message=f"Collector stopped: {type(exc).__name__}: {str(exc)[:180]}")
            raise
        finally:
            total["active"] = False
            write_status(directory, total)
    return total


def start(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / "launch"):
        current = status(directory)
        if current.get("active") and not current.get("stale"):
            return current
        try:
            with locked(directory / "owner"):
                pass
        except OSError:
            return {**current, "message": "A collector still owns this directory. Stop it and wait for its stopped heartbeat before restarting."}
        (directory / "STOP").unlink(missing_ok=True)
        write_status(directory, {"active": True, "message": "Starting background collector; waiting for heartbeat."})
        command = [sys.executable, "-m", "market_capture", "collect", "--directory", str(directory)]
        options = {"cwd": str(Path(__file__).resolve().parents[1]), "stdin": subprocess.DEVNULL,
                   "stderr": subprocess.STDOUT, "close_fds": True, "shell": False}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        try:
            with (directory / "collector.log").open("ab") as output:
                subprocess.Popen(command, stdout=output, **options)
        except Exception as exc:
            write_status(directory, {"active": False, "message": f"Could not start collector: {str(exc)[:180]}"})
            raise
    return status(directory)


def stop(directory):
    directory = Path(directory)
    if directory.exists():
        (directory / "STOP").write_text("Stop public collection", encoding="utf-8")
