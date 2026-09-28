"""Fixtures for the external-intelligence tests.

Synthetic and small, each built to exercise one rule at a time. Nothing here
resembles real provider data beyond the documented field names: no real payload
was available when these were written.
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from crypto_research.models import EXCHANGE_BINANCE_US, MarketBar

from external_intelligence.base.coverage import CoverageIndex, CoverageInterval
from external_intelligence.events import ExternalEvent, ReceiptBasis, make_event_id

HOUR = 3_600_000
MINUTE = 60_000
T0 = 1_700_000_000_000  # a fixed instant (November 2023); nothing depends on it being real


def make_event(
    pid: int | str,
    *,
    provider: str = "whale_alert",
    event_type: str = "EXCHANGE_INFLOW",
    asset: str = "BTC",
    event_time: int = T0,
    received: int | None = None,
    usd: str | None = "1000000",
    estimated: tuple[int, int, str] | None = None,
    **overrides: Any,
) -> ExternalEvent:
    """An event with an observed receipt (``received``), an estimated one, or none."""
    fields: dict[str, Any] = dict(
        event_id=make_event_id(provider, str(pid)),
        provider=provider,
        provider_event_id=str(pid),
        event_type=event_type,
        asset=asset,
        event_time=event_time,
        amount_usd=None if usd is None else Decimal(usd),
    )
    if received is not None:
        fields.update(system_received_time=received, received_time_basis=ReceiptBasis.OBSERVED)
    elif estimated is not None:
        when, delay, why = estimated
        fields.update(
            estimated_received_time=when, assumed_delay_ms=delay, received_time_assumption=why,
            received_time_basis=ReceiptBasis.ASSUMED_DELAY,
        )
    fields.update(overrides)
    return ExternalEvent(**fields)


def coverage_for(provider: str, family: str, start: int, end: int) -> CoverageIndex:
    return CoverageIndex([CoverageInterval(provider, family, start, end, "declared_import_window")])


def full_coverage(start: int, end: int) -> CoverageIndex:
    """Both default providers declared complete for every family over [start, end]."""
    index = CoverageIndex()
    for provider, family in (
        ("whale_alert", "transfers"),
        ("coinlobster", "executed_trades"),
        ("coinlobster", "liquidations"),
        ("coinlobster", "derivatives"),
    ):
        index.add(CoverageInterval(provider, family, start, end, "declared_import_window"))
    return index


def make_bars(
    closes: Sequence[float | str],
    *,
    start: int = T0,
    interval_ms: int = HOUR,
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    feed_delay_ms: int = 0,
) -> list[MarketBar]:
    """One bar per close. Open equals the previous close; volume is a constant."""
    bars: list[MarketBar] = []
    previous = Decimal(str(closes[0]))
    for i, close in enumerate(closes):
        c = Decimal(str(close))
        opened = previous if i else c
        bars.append(
            MarketBar(
                exchange=EXCHANGE_BINANCE_US, symbol=symbol, interval=interval,
                open_time=start + i * interval_ms, close_time=start + (i + 1) * interval_ms - 1,
                open=opened, high=max(opened, c) * Decimal("1.001"), low=min(opened, c) / Decimal("1.001"),
                close=c, volume=Decimal("100"), quote_volume=Decimal("100") * c,
                feed_delay_ms=feed_delay_ms,
            )
        )
        previous = c
    return bars


def write_temp(directory: str, name: str, content: str) -> Path:
    path = Path(directory) / name
    path.write_text(content, encoding="utf-8")
    return path


def jsonl(records: Iterable[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(record) for record in records)


class Temp:
    """``with Temp() as tmp:`` -- a directory that disappears afterwards."""

    def __enter__(self) -> str:
        self._dir = tempfile.TemporaryDirectory()
        return self._dir.name

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()
