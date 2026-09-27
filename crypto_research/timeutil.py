"""Time handling.

Canonical time in this package is **integer milliseconds since the Unix epoch,
UTC** -- the same representation Binance.US uses. Keeping one integer
representation end to end removes a whole class of timezone and float-rounding
bugs from the ordering guarantees the backtester depends on.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

SECOND_MS = 1000
MINUTE_MS = 60 * SECOND_MS
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS
WEEK_MS = 7 * DAY_MS

# Binance.US kline intervals, per the official REST documentation.
# 1M (one calendar month) is listed as supported but deliberately excluded from
# interval_to_ms below, so that nothing silently treats a month as 30 days.
SUPPORTED_INTERVALS: tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
)

_FIXED_WIDTH_UNITS = {"m": MINUTE_MS, "h": HOUR_MS, "d": DAY_MS, "w": WEEK_MS}
_INTERVAL_RE = re.compile(r"^(\d+)([mhdwM])$")


def interval_to_ms(interval: str) -> int:
    """Millisecond width of a fixed-width kline interval.

    Raises ValueError for ``1M``: calendar months vary in length, and callers
    that need month stepping must handle it explicitly via add_months.
    """
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"{interval!r} is not a Binance.US kline interval")
    match = _INTERVAL_RE.match(interval)
    if match is None:  # pragma: no cover - guarded by SUPPORTED_INTERVALS
        raise ValueError(f"unparseable interval {interval!r}")
    count, unit = int(match.group(1)), match.group(2)
    if unit == "M":
        raise ValueError(
            "calendar-month intervals have no fixed millisecond width; "
            "step months explicitly with add_months instead"
        )
    return count * _FIXED_WIDTH_UNITS[unit]


def now_ms() -> int:
    """Wall-clock milliseconds. Never called from inside a simulation."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def to_iso(ms: int) -> str:
    return to_datetime(ms).isoformat().replace("+00:00", "Z")


def parse_time(value: str | int | datetime) -> int:
    """Parse an epoch-ms int, an ISO-8601 string, or a datetime into epoch ms.

    Naive datetimes and naive ISO strings are interpreted as UTC. That is
    stated explicitly because getting it wrong shifts every bar in a backtest.
    """
    if isinstance(value, bool):
        raise TypeError("bool is not a timestamp")
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(moment.timestamp() * 1000)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    moment = datetime.fromisoformat(normalized)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def add_days(ms: int, days: int) -> int:
    return int((to_datetime(ms) + timedelta(days=days)).timestamp() * 1000)


def add_months(ms: int, months: int) -> int:
    """Calendar-month step, clamped to the last valid day of the target month."""
    moment = to_datetime(ms)
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, _days_in_month(year, month))
    return int(moment.replace(year=year, month=month, day=day).timestamp() * 1000)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return (nxt - timedelta(days=1)).day
