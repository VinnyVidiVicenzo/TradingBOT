"""A generic event study: describe what followed events of a kind. Do not explain it.

For a category of event (a large exchange inflow, a stablecoin mint, an extreme
buy imbalance, a liquidation spike ...) this reports what price did afterwards at
configurable horizons, next to what it did *anywhere*, so the reader can see
whether the post-event numbers differ from ordinary ones at all.

Design points that keep it honest:

* **Entry is the moment we could act**, i.e. the receipt time, never the time the
  thing happened on-chain or on a venue. Entry price is the last observable price.
* **Overlap is not hidden.** Events close together share outcome windows, so each
  horizon reports the raw count and a count of *independent* events (greedy,
  non-overlapping), and the statistics quoted are on the independent set.
* **The unconditional baseline sits beside every result**: the same horizon
  measured from every bar.
* **Regimes are backward-looking only**: trend and volatility state are computed
  from prices before entry.
* **No causal language.** Returns following an event are not evidence the event
  caused them. The disclaimer travels with every result.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from ..events import EventType, ExternalEvent, ReceiptBasis
from ..features.windows import parse_window
from .price import PricePoint, PriceSource, forward_return
from .stats import summary_stats
from .triggers import Trigger

DISCLAIMER = (
    "Forward returns after an event describe what followed it. They do not show that the "
    "event caused the move: prices are pushed by many things at once, events cluster in "
    "busy markets, and overlapping windows make neighbouring results look more independent "
    "than they are."
)
DEFAULT_HORIZONS = ("5m", "1h", "4h", "24h", "7d")
TREND_LOOKBACK = "30d"
SHORT_VOL_WINDOW = "7d"
LONG_VOL_WINDOW = "30d"
MIN_VOL_POINTS = 10


@dataclass(frozen=True, slots=True)
class EventFilter:
    event_types: tuple[EventType, ...]
    assets: tuple[str, ...] | None = None
    providers: tuple[str, ...] | None = None
    exchange: str | None = None
    min_amount_usd: Decimal | None = None

    def matches(self, event: ExternalEvent) -> bool:
        if event.event_type not in self.event_types:
            return False
        if self.assets is not None and event.asset not in self.assets:
            return False
        if self.providers is not None and event.provider not in self.providers:
            return False
        if self.exchange is not None and event.exchange != self.exchange:
            return False
        if self.min_amount_usd is not None:
            return event.amount_usd is not None and event.amount_usd >= self.min_amount_usd
        return True


@dataclass(frozen=True, slots=True)
class EventStudySpec:
    name: str
    event_filter: EventFilter | None = None  # not used by run_trigger_study
    horizons: tuple[str, ...] = DEFAULT_HORIZONS
    pre_window: str = "1h"
    regime: str | None = None  # "trend" | "volatility" | None
    allow_estimated: bool = False
    min_independent: int = 30  # fewer than this and the result says it is not interpretable


@dataclass(slots=True)
class EventStudyResult:
    name: str
    events_matching: int
    events_usable: int
    skipped: dict[str, int]
    horizons: list[dict[str, Any]] = field(default_factory=list)
    resolution_ms: int = 0
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "events_matching": self.events_matching,
            "events_usable": self.events_usable, "skipped": dict(self.skipped),
            "price_resolution_ms": self.resolution_ms, "horizons": self.horizons,
            "disclaimer": self.disclaimer,
        }


@dataclass(slots=True)
class _Entry:
    key: str
    time: int
    price: PricePoint
    pre_move: float | None
    regime: str | None


def run_event_study(
    events: Sequence[ExternalEvent], source: PriceSource, spec: EventStudySpec
) -> EventStudyResult:
    if spec.event_filter is None:
        raise ValueError("an event study needs an event_filter (use run_trigger_study for derived triggers)")
    skipped: dict[str, int] = {}
    matching = [e for e in events if spec.event_filter.matches(e)]
    if spec.event_filter.min_amount_usd is not None:
        skipped["unknown_amount_excluded"] = sum(
            1 for e in events
            if e.event_type in spec.event_filter.event_types and e.amount_usd is None
        )
    entries: list[_Entry] = []
    for event in sorted(matching, key=lambda e: (e.event_time, e.event_id)):
        entry_time = _entry_time(event, spec.allow_estimated)
        if entry_time is None:
            skipped["no_receipt_time"] = skipped.get("no_receipt_time", 0) + 1
            continue
        entry = _entry(source, event.event_id, entry_time, spec)
        if entry is None:
            skipped["no_price_at_entry"] = skipped.get("no_price_at_entry", 0) + 1
            continue
        entries.append(entry)
    return _finish(spec, source, len(matching), entries, skipped)


def run_trigger_study(
    triggers: Sequence[Trigger], source: PriceSource, spec: EventStudySpec
) -> EventStudyResult:
    """The same study for derived categories (an extreme reading of a feature).

    Each trigger's ``entry_time`` is the moment the reading was computed and
    therefore actionable, so no receipt-time bookkeeping is needed here.
    """
    skipped: dict[str, int] = {}
    entries: list[_Entry] = []
    for trigger in sorted(triggers, key=lambda t: (t.entry_time, t.trigger_id)):
        entry = _entry(source, trigger.trigger_id, trigger.entry_time, spec)
        if entry is None:
            skipped["no_price_at_entry"] = skipped.get("no_price_at_entry", 0) + 1
            continue
        entries.append(entry)
    return _finish(spec, source, len(triggers), entries, skipped)


def _entry(source: PriceSource, key: str, entry_time: int, spec: EventStudySpec) -> _Entry | None:
    price = source.price_at(entry_time)
    if price is None:
        return None
    before = source.price_at(entry_time - parse_window(spec.pre_window))
    pre_move = None
    if before is not None and price.as_of > before.as_of:
        pre_move = float(price.price / before.price - 1)
    return _Entry(key, entry_time, price, pre_move, _regime(source, entry_time, spec.regime))


def _finish(
    spec: EventStudySpec, source: PriceSource, matching: int, entries: list[_Entry], skipped: dict[str, int]
) -> EventStudyResult:
    entries.sort(key=lambda x: (x.time, x.key))
    result = EventStudyResult(
        name=spec.name, events_matching=matching, events_usable=len(entries),
        skipped=skipped, resolution_ms=source.resolution_ms,
    )
    for horizon in spec.horizons:
        result.horizons.append(_horizon_result(entries, source, spec, horizon))
    return result


def _entry_time(event: ExternalEvent, allow_estimated: bool) -> int | None:
    if event.received_time_basis is ReceiptBasis.OBSERVED:
        return event.system_received_time
    if event.received_time_basis is ReceiptBasis.ASSUMED_DELAY and allow_estimated:
        return event.estimated_received_time
    return None


def _horizon_result(
    entries: Sequence[_Entry], source: PriceSource, spec: EventStudySpec, horizon: str
) -> dict[str, Any]:
    horizon_ms = parse_window(horizon)
    reasons: dict[str, int] = {}
    outcomes: list[tuple[_Entry, float, float | None]] = []
    for entry in entries:
        value, reason = forward_return(source, entry.time, horizon_ms)
        if value is None:
            reasons[reason or "unavailable"] = reasons.get(reason or "unavailable", 0) + 1
            continue
        start = float(entry.price.price)
        path = source.path(entry.time, entry.time + horizon_ms)
        drawdown = min(0.0, min(float(p.price) / start - 1 for p in path)) if path else None
        outcomes.append((entry, value, drawdown))

    independent = _independent(outcomes, horizon_ms)
    returns_all = [o[1] for o in outcomes]
    returns_ind = [o[1] for o in independent]
    drawdowns = [o[2] for o in independent if o[2] is not None]
    pre_moves = [o[0].pre_move for o in independent if o[0].pre_move is not None]

    baseline_returns: list[float] = []
    for moment in source.observable_times():
        value, _ = forward_return(source, moment, horizon_ms)
        if value is not None:
            baseline_returns.append(value)

    regimes: dict[str, dict[str, Any]] = {}
    if spec.regime is not None:
        for label in sorted({o[0].regime or "UNKNOWN" for o in independent}):
            group = [o[1] for o in independent if (o[0].regime or "UNKNOWN") == label]
            regimes[label] = summary_stats(group)

    return {
        "horizon": horizon,
        "n_raw": len(outcomes),
        "n_independent": len(independent),
        "interpretable": len(independent) >= spec.min_independent,
        "not_computable": dict(sorted(reasons.items())),
        "after_event_independent": summary_stats(returns_ind),
        "after_event_raw_overlapping": summary_stats(returns_all),
        "mean_max_drawdown_after": statistics.fmean(drawdowns) if drawdowns else None,
        "pre_event_move": summary_stats(pre_moves),
        "unconditional_baseline": summary_stats(baseline_returns),
        "regimes": regimes,
    }


def _independent(
    outcomes: Sequence[tuple[_Entry, float, float | None]], horizon_ms: int
) -> list[tuple[_Entry, float, float | None]]:
    kept: list[tuple[_Entry, float, float | None]] = []
    last: int | None = None
    for item in outcomes:  # already in entry-time order
        if last is None or item[0].time >= last + horizon_ms:
            kept.append(item)
            last = item[0].time
    return kept


def _regime(source: PriceSource, entry: int, kind: str | None) -> str | None:
    if kind is None:
        return None
    if kind == "trend":
        now = source.price_at(entry)
        then = source.price_at(entry - parse_window(TREND_LOOKBACK))
        if now is None or then is None or now.as_of <= then.as_of:
            return "UNKNOWN"
        return "UPTREND" if now.price > then.price else "DOWNTREND"
    if kind == "volatility":
        short = _volatility(source, entry, parse_window(SHORT_VOL_WINDOW))
        long_ = _volatility(source, entry, parse_window(LONG_VOL_WINDOW))
        if short is None or long_ is None or long_ == 0:
            return "UNKNOWN"
        return "RISING_VOL" if short > long_ else "FALLING_VOL"
    raise ValueError(f"unknown regime kind {kind!r}")


def _volatility(source: PriceSource, entry: int, window_ms: int) -> float | None:
    points = source.path(entry - window_ms, entry)
    if len(points) < MIN_VOL_POINTS:
        return None
    returns = [float(b.price / a.price - 1) for a, b in zip(points, points[1:])]
    return statistics.stdev(returns) if len(returns) >= 2 else None
