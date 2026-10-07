"""Coordinate local JSONL readers/writers and recover visibly from torn rows."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + ".lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_record(path: str | Path, payload: dict) -> None:
    path = Path(path)
    line = json.dumps(payload, sort_keys=True) + "\n"
    with locked(path):
        prefix = ""
        if path.exists() and path.stat().st_size:
            with path.open("rb") as handle:
                handle.seek(-1, 2)
                if handle.read(1) != b"\n":
                    prefix = "\n"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix + line)
            handle.flush()


def read_records(path: str | Path, convert) -> list:
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with locked(path), path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                records.append(convert(json.loads(line)))
            except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
                logger.warning("skipping unreadable row %d in %s: %s", number, path, exc)
    return records
