"""Rolling-window lengths: 15m, 1h, 4h, 24h, 7d and so on."""

from __future__ import annotations

import re

_UNITS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_PATTERN = re.compile(r"^(\d+)([mhdw])$")


def parse_window(text: str) -> int:
    """Milliseconds in a window such as ``"15m"``, ``"4h"``, ``"24h"`` or ``"7d"``."""
    match = _PATTERN.match(text.strip().lower())
    if match is None or int(match.group(1)) <= 0:
        raise ValueError(f"{text!r} is not a window like 15m, 1h, 4h, 24h or 7d")
    return int(match.group(1)) * _UNITS[match.group(2)]


def format_window(ms: int) -> str:
    """The shortest exact label for ``ms`` (inverse of :func:`parse_window`)."""
    for unit, size in (("w", _UNITS["w"]), ("d", _UNITS["d"]), ("h", _UNITS["h"]), ("m", _UNITS["m"])):
        if ms % size == 0:
            return f"{ms // size}{unit}"
    raise ValueError(f"{ms} ms is not a whole number of minutes")
