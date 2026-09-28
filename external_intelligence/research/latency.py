"""How much had price already moved by the time we could act?

For every event this measures the price at three moments -- when the event
happened, when the provider published it, and when *we* received it -- and the
price path after receipt. A signal that is already priced in by the time our
system sees it is stale, whatever it means. That matters most for real-time
whale alerts.

Only the receipt time is a moment we could have acted, so only prices at or after
it count as outcomes. Prices are the last observable bar (see ``price``); when two
of the moments fall inside the same bar the return between them cannot be
resolved and is reported as missing, not zero. Returns are simple returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..events import ExternalEvent, ReceiptBasis
from ..features.windows import parse_window
from .price import PricePoint, PriceSource, forward_return
from .stats import summary_stats

DEFAULT_HORIZONS = ("5m", "1h", "4h", "24h")


@dataclass(frozen=True, slots=True)
class LatencyRecord:
    event_id: str
    provider: str
    event_type: str
    asset: str
    event_time: int
    provider_publish_time: int | None
    received_time: int | None
    price_at_event_time: str | None
    price_at_provider_publish_time: str | None
    price_at_system_received_time: str | None
    return_event_to_publish: float | None
    return_event_to_receive: float | None
    returns_after_receipt: Mapping[str, float | None]  # keyed return_receive_to_5m, ...
    skipped_reason: str | None = None


@dataclass(slots=True)
class LatencyReport:
    records: list[LatencyRecord] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _return(start: PricePoint | None, end: PricePoint | None) -> float | None:
    if start is None or end is None or end.as_of <= start.as_of:
        return None
    return float(end.price / start.price - 1)


def analyze_latency(
    events: Sequence[ExternalEvent],
    source: PriceSource,
    *,
    horizons: Sequence[str] = DEFAULT_HORIZONS,
    allow_estimated: bool = False,
) -> LatencyReport:
    report = LatencyReport()
    skipped: dict[str, int] = {}
    keys = [f"return_receive_to_{h}" for h in horizons]
    for event in sorted(events, key=lambda e: (e.event_time, e.event_id)):
        received = _receipt_time(event, allow_estimated)
        if received is None:
            skipped["no_receipt_time"] = skipped.get("no_receipt_time", 0) + 1
            report.records.append(_skipped(event, "no_receipt_time", keys))
            continue
        p_event = source.price_at(event.event_time)
        p_publish = (
            source.price_at(event.provider_publish_time) if event.provider_publish_time is not None else None
        )
        p_receive = source.price_at(received)
        after: dict[str, float | None] = {}
        for horizon, key in zip(horizons, keys):
            value, _ = forward_return(source, received, parse_window(horizon))
            after[key] = value
        report.records.append(
            LatencyRecord(
                event_id=event.event_id, provider=event.provider, event_type=event.event_type.value,
                asset=event.asset, event_time=event.event_time,
                provider_publish_time=event.provider_publish_time, received_time=received,
                price_at_event_time=None if p_event is None else str(p_event.price),
                price_at_provider_publish_time=None if p_publish is None else str(p_publish.price),
                price_at_system_received_time=None if p_receive is None else str(p_receive.price),
                return_event_to_publish=_return(p_event, p_publish),
                return_event_to_receive=_return(p_event, p_receive),
                returns_after_receipt=after,
            )
        )

    usable = [r for r in report.records if r.skipped_reason is None]
    metrics: dict[str, list[float]] = {"return_event_to_publish": [], "return_event_to_receive": []}
    metrics.update({key: [] for key in keys})
    for record in usable:
        for name in ("return_event_to_publish", "return_event_to_receive"):
            value = getattr(record, name)
            if value is not None:
                metrics[name].append(value)
        for key in keys:
            value = record.returns_after_receipt[key]
            if value is not None:
                metrics[key].append(value)
    report.summary = {
        "events": len(events),
        "usable": len(usable),
        "skipped": skipped,
        "price_resolution_ms": source.resolution_ms,
        "metrics": {name: summary_stats(values) for name, values in metrics.items()},
        "notes": [
            "Returns are limited by the price resolution: two moments inside one bar give "
            "no measurable return and are reported as missing.",
            "A large move between event and receipt means the information was already "
            "in the price by the time it was actionable.",
        ],
    }
    return report


def _receipt_time(event: ExternalEvent, allow_estimated: bool) -> int | None:
    if event.received_time_basis is ReceiptBasis.OBSERVED:
        return event.system_received_time
    if event.received_time_basis is ReceiptBasis.ASSUMED_DELAY and allow_estimated:
        return event.estimated_received_time
    return None


def _skipped(event: ExternalEvent, reason: str, keys: Sequence[str]) -> LatencyRecord:
    return LatencyRecord(
        event_id=event.event_id, provider=event.provider, event_type=event.event_type.value,
        asset=event.asset, event_time=event.event_time,
        provider_publish_time=event.provider_publish_time, received_time=None,
        price_at_event_time=None, price_at_provider_publish_time=None,
        price_at_system_received_time=None, return_event_to_publish=None,
        return_event_to_receive=None, returns_after_receipt={k: None for k in keys},
        skipped_reason=reason,
    )
