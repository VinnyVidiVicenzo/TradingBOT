"""Bounded raw stream recording with visible disconnect and sequence gaps."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from crypto_research.decimals import dec
from crypto_research.jsonl import locked
from crypto_research.market_universe import SYMBOLS
from crypto_research.timeutil import now_ms

SUFFIXES = ("aggTrade", "bookTicker", "depth20@100ms")
STREAMS = {s.lower() + "@" + suffix: (s, suffix) for s in SYMBOLS for suffix in SUFFIXES}
URI = "wss://stream.binance.us:9443/stream?streams=" + "/".join(STREAMS)
MAX_EVENTS = 100_000
MAX_BYTES = 128 * 1024 * 1024


def integer(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Invalid stream integer")
    return value


def levels(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        raise ValueError("Missing or oversized partial depth")
    parsed = [(dec(p), dec(q)) for p, q in rows]
    if any(p <= 0 or q <= 0 for p, q in parsed):
        raise ValueError("Invalid depth value")
    return parsed


def normalize(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 524288:
        raise ValueError("Invalid stream frame size/type")
    frame = json.loads(raw)
    symbol, suffix = STREAMS[frame["stream"]]
    data = frame["data"]
    if not isinstance(data, dict) or data.get("s", symbol) != symbol:
        raise ValueError("Mismatched stream symbol")
    source_at = integer(data["E"]) if "E" in data else None
    if suffix == "aggTrade":
        sequence = integer(data["a"])
        integer(data["T"])
        if data.get("e") != "aggTrade" or type(data["m"]) is not bool or dec(data["p"]) <= 0 or dec(data["q"]) <= 0:
            raise ValueError("Malformed aggregate trade")
        kind = "trade"
    elif suffix == "bookTicker":
        sequence = integer(data["u"])
        if not 0 < dec(data["b"]) <= dec(data["a"]) or dec(data["B"]) < 0 or dec(data["A"]) < 0:
            raise ValueError("Malformed quote")
        kind = "quote"
    else:
        sequence = integer(data["lastUpdateId"])
        bids, asks = levels(data["bids"]), levels(data["asks"])
        if (bids[0][0] > asks[0][0] or any(a[0] <= b[0] for a, b in zip(bids, bids[1:]))
                or any(a[0] >= b[0] for a, b in zip(asks, asks[1:]))):
            raise ValueError("Unordered or crossed depth")
        kind = "depth"
    return symbol, kind, source_at, sequence


class CaptureFull(RuntimeError):
    pass


class Store:
    def __init__(self, path, observer=None):
        self.path = Path(path)
        self.observer = observer
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS stream_events (id INTEGER PRIMARY KEY, received INTEGER, session TEXT, symbol TEXT, kind TEXT, source_at INTEGER, body TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS stream_lookup ON stream_events(symbol,kind,received)")
        self.db.execute("CREATE TABLE IF NOT EXISTS capture_status (id INTEGER PRIMARY KEY, body TEXT)")
        self.count, self.bytes = self.db.execute("SELECT count(*),coalesce(sum(length(cast(body AS BLOB))),0) FROM stream_events").fetchone()
        self.kinds = dict(self.db.execute("SELECT kind,count(*) FROM stream_events GROUP BY kind"))
        self.last_received = self.db.execute("SELECT max(received) FROM stream_events WHERE kind IN ('trade','quote','depth')").fetchone()[0]

    def status(self, message, at):
        payload = {"message": message[:240], "at": at, "events": self.count, "bytes": self.bytes,
                   "counts": self.kinds, "last_received": self.last_received}
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO capture_status VALUES (1,?)", (json.dumps(payload),))
        if self.observer:
            self.observer(payload)

    def append(self, at, session, symbol, kind, source_at, body, *, limit=MAX_EVENTS):
        size = len(body.encode())
        if self.count >= limit or self.bytes + size > MAX_BYTES:
            raise CaptureFull("Storage cap reached; archive this capture and choose a new file. No history was deleted.")
        with self.db:
            self.db.execute("INSERT INTO stream_events(received,session,symbol,kind,source_at,body) VALUES (?,?,?,?,?,?)",
                            (at, session, symbol, kind, source_at, body))
        self.count += 1
        self.bytes += size
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if kind in ("trade", "quote", "depth"):
            self.last_received = at

    def close(self):
        self.db.close()


def snapshot(path):
    path = Path(path)
    if not path.exists():
        return {"message": "No streaming capture yet", "events": 0, "bytes": 0}
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        row = db.execute("SELECT body FROM capture_status WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"message": "Capture initialized", "events": 0}
    finally:
        db.close()


def run(path, stop, *, seconds=1800, limit=MAX_EVENTS, connector=None, clock=now_ms, observer=None):
    if not 1 <= seconds <= 86400 or not 1 <= limit <= MAX_EVENTS:
        raise ValueError("Invalid recording time or event cap")
    if connector is None:
        from websockets.sync.client import connect
        connector = connect
    with locked(Path(path)):
        store = Store(path, observer=observer)
        deadline = time.monotonic() + seconds
        delay = 1
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                session = uuid.uuid4().hex
                sequence = {}
                try:
                    store.status("Connecting to public Binance.US streams", clock())
                    with connector(URI, proxy=None, open_timeout=8, close_timeout=2, ping_interval=20,
                                   ping_timeout=10, max_size=524288, max_queue=1024, compression=None) as connection:
                        store.append(clock(), session, None, "CONNECT", None, "{}", limit=limit)
                        delay = 1
                        last_status = 0
                        while not stop.is_set() and time.monotonic() < deadline:
                            try:
                                raw = connection.recv(timeout=1)
                            except TimeoutError:
                                store.status("Connected; waiting for public events", clock())
                                continue
                            received = clock()
                            try:
                                symbol, kind, source_at, seq = normalize(raw)
                            except (ValueError, KeyError, TypeError, ArithmeticError, RecursionError) as exc:
                                store.append(received, session, None, "GAP", None,
                                             json.dumps({"reason": "invalid frame", "detail": str(exc)[:160]}), limit=limit)
                                continue
                            previous = sequence.get((symbol, kind))
                            if previous is not None and (seq < previous or (kind == "trade" and seq > previous + 1)):
                                store.append(received, session, symbol, "GAP", None,
                                             json.dumps({"reason": "sequence discontinuity", "previous": previous, "current": seq}), limit=limit)
                            sequence[symbol, kind] = seq
                            store.append(received, session, symbol, kind, source_at, raw, limit=limit)
                            if received - last_status >= 1000:
                                store.status("Recording public trades, quotes and top-20 depth", received)
                                last_status = received
                except CaptureFull:
                    raise
                except Exception as exc:
                    store.append(clock(), session, None, "GAP", None,
                                 json.dumps({"reason": "disconnect", "detail": str(exc)[:160]}), limit=limit)
                    store.status("Disconnected; retrying public stream", clock())
                    stop.wait(delay)
                    delay = min(30, delay * 2)
            store.status("Stopped — no capture runs while this recorder is closed", clock())
        except CaptureFull as exc:
            store.status(str(exc), clock())
        finally:
            store.close()


class Recorder:
    def __init__(self, path):
        self.path, self.stop_event, self.thread, self.error = Path(path), threading.Event(), None, None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.error = None
        def work():
            try:
                run(self.path, self.stop_event)
            except Exception as exc:
                self.error = f"Recorder stopped: {type(exc).__name__}: {str(exc)[:180]}"
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
