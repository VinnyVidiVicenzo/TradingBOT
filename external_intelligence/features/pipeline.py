"""Point-in-time feature computation.

The single rule: a feature computed for decision time ``T`` may use an event only
if the event was *available* by then -- ``system_received_time <= T`` for an
observed receipt, or (only when the caller explicitly allows an assumption) an
estimated receipt no later than ``T``. Event time on its own is never enough. A
sensitivity control, ``extra_delay_ms``, shifts the availability cut-off earlier
to ask "would this still hold if we had been slower?".

Missing stays missing. A feature is ``None`` -- with a stated status -- when

* no provider is configured for it, or the provider is unhealthy;
* the feed was not declared complete over the window (a count of "0" would be a
  guess, and a non-zero sum would only be a lower bound);
* an event in the window has no USD amount (the sum would be understated);
* the quantity is undefined (a ratio with no denominator);
* a state reading is too old.

Zero is returned only when it is true: the window was covered and nothing
happened in it.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

from crypto_research.decimals import div

from ..base.coverage import (
    FAMILY_DERIVATIVES,
    FAMILY_EXECUTED,
    FAMILY_LIQUIDATIONS,
    FAMILY_TRANSFERS,
    CoverageIndex,
)
from ..base.health import HealthStatus
from ..events import ExternalEvent
from ..storage.repositories import EventRepository
from .definitions import (
    SCOPE_STABLECOIN_MARKET,
    FeatureDefinition,
    Metric,
    default_feature_definitions,
)

DEFAULT_PROVIDERS: Mapping[str, str] = {
    FAMILY_TRANSFERS: "whale_alert",
    FAMILY_EXECUTED: "coinlobster",
    FAMILY_LIQUIDATIONS: "coinlobster",
    FAMILY_DERIVATIVES: "coinlobster",
}

HealthGate = Callable[[str, int], HealthStatus]


class FeatureStatus(str, Enum):
    OK = "OK"
    MISSING_NO_PROVIDER = "MISSING_NO_PROVIDER"
    MISSING_PROVIDER_UNHEALTHY = "MISSING_PROVIDER_UNHEALTHY"
    MISSING_NO_COVERAGE = "MISSING_NO_COVERAGE"
    MISSING_PARTIAL_AMOUNTS = "MISSING_PARTIAL_AMOUNTS"
    MISSING_UNDEFINED = "MISSING_UNDEFINED"
    MISSING_STALE = "MISSING_STALE"
    MISSING_NO_HISTORY = "MISSING_NO_HISTORY"


@dataclass(frozen=True, slots=True)
class FeatureValue:
    value: Decimal | int | float | None
    status: FeatureStatus
    detail: str = ""
    n_events: int = 0

    @property
    def is_missing(self) -> bool:
        return self.value is None

    def as_float(self) -> float | None:
        return None if self.value is None else float(self.value)


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    asset: str
    decision_time: int
    values: Mapping[str, FeatureValue]
    input_hash: str
    events_used: int
    settings: Mapping[str, Any]

    def get(self, name: str) -> FeatureValue:
        return self.values[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "decision_time": self.decision_time,
            "input_hash": self.input_hash,
            "events_used": self.events_used,
            "settings": dict(self.settings),
            "values": {
                name: {
                    "value": None if fv.value is None else str(fv.value),
                    "status": fv.status.value,
                    "detail": fv.detail,
                    "n_events": fv.n_events,
                }
                for name, fv in self.values.items()
            },
        }


# --------------------------------------------------------------------------
# where events come from
# --------------------------------------------------------------------------


class EventSource(Protocol):
    def events_available(
        self, *, as_of: int, assets: Sequence[str] | None, providers: Sequence[str] | None,
        min_event_time: int | None, max_event_time: int | None, allow_estimated: bool,
    ) -> list[ExternalEvent]: ...


class RepositoryEventSource:
    """Events straight from the store; availability is enforced in SQL."""

    def __init__(self, repository: EventRepository) -> None:
        self._repo = repository

    def events_available(
        self, *, as_of: int, assets: Sequence[str] | None, providers: Sequence[str] | None,
        min_event_time: int | None, max_event_time: int | None, allow_estimated: bool,
    ) -> list[ExternalEvent]:
        return self._repo.load_available(
            as_of=as_of, assets=assets, providers=providers, min_event_time=min_event_time,
            max_event_time=max_event_time, allow_estimated=allow_estimated,
        )


class InMemoryEventSource:
    """The same contract over a list, for tests and for fast research loops."""

    def __init__(self, events: Sequence[ExternalEvent]) -> None:
        self._events = sorted(events, key=lambda e: (e.event_time, e.event_id))
        self._times = [e.event_time for e in self._events]

    def events_available(
        self, *, as_of: int, assets: Sequence[str] | None, providers: Sequence[str] | None,
        min_event_time: int | None, max_event_time: int | None, allow_estimated: bool,
    ) -> list[ExternalEvent]:
        low = 0 if min_event_time is None else bisect.bisect_left(self._times, min_event_time)
        high = bisect.bisect_right(self._times, as_of if max_event_time is None else max_event_time)
        chosen = []
        for event in self._events[low:high]:
            if providers and event.provider not in providers:
                continue
            if assets and event.asset not in assets:
                continue
            if event.is_available_at(as_of, allow_estimated=allow_estimated):
                chosen.append(event)
        return chosen


# --------------------------------------------------------------------------
# the pipeline
# --------------------------------------------------------------------------


class FeaturePipeline:
    def __init__(
        self,
        source: EventSource,
        coverage: CoverageIndex,
        *,
        definitions: Sequence[FeatureDefinition] | None = None,
        providers: Mapping[str, str] | None = None,
        allow_estimated: bool = False,
        extra_delay_ms: int = 0,
        health_gate: HealthGate | None = None,
    ) -> None:
        if extra_delay_ms < 0:
            raise ValueError("extra_delay_ms cannot be negative")
        self._source = source
        self._coverage = coverage
        self.definitions = list(definitions if definitions is not None else default_feature_definitions())
        self.providers = dict(providers if providers is not None else DEFAULT_PROVIDERS)
        self.allow_estimated = allow_estimated
        self.extra_delay_ms = extra_delay_ms
        self._health_gate = health_gate

    def settings(self) -> dict[str, Any]:
        return {
            "allow_estimated": self.allow_estimated,
            "extra_delay_ms": self.extra_delay_ms,
            "providers": dict(sorted(self.providers.items())),
            "features": [d.name for d in self.definitions],
        }

    def compute(
        self, asset: str, decision_time: int, *, names: Sequence[str] | None = None
    ) -> FeatureSnapshot:
        wanted = self.definitions if names is None else [d for d in self.definitions if d.name in set(names)]
        if names is not None and len(wanted) != len(set(names)):
            unknown = sorted(set(names) - {d.name for d in self.definitions})
            raise KeyError(f"unknown feature(s): {unknown}")
        cutoff = decision_time - self.extra_delay_ms
        fetched: dict[tuple[str, str], list[ExternalEvent]] = {}
        used: set[str] = set()
        values: dict[str, FeatureValue] = {}

        # The furthest back each (provider, scope) group has to look, so its events
        # are fetched once for every feature that needs them.
        earliest: dict[tuple[str, str], int] = {}
        for definition in wanted:
            group_provider = self.providers.get(definition.coverage_family)
            if group_provider is not None:
                group_key = (group_provider, definition.scope)
                begins = decision_time - definition.window_ms - definition.lookback_ms
                earliest[group_key] = min(earliest.get(group_key, begins), begins)

        for definition in sorted(wanted, key=lambda d: d.name):
            provider = self.providers.get(definition.coverage_family)
            if provider is None:
                values[definition.name] = FeatureValue(
                    None, FeatureStatus.MISSING_NO_PROVIDER,
                    f"no provider configured for {definition.coverage_family}",
                )
                continue
            if self._health_gate is not None:
                status = self._health_gate(provider, decision_time)
                if not status.healthy:
                    values[definition.name] = FeatureValue(
                        None, FeatureStatus.MISSING_PROVIDER_UNHEALTHY,
                        f"{provider} unhealthy: {', '.join(status.reasons) or 'unspecified'}",
                    )
                    continue
            span = definition.window_ms + definition.lookback_ms
            start = decision_time - span
            key = (provider, definition.scope)
            events = fetched.get(key)
            if events is None:
                events = self._source.events_available(
                    as_of=cutoff,
                    assets=None if definition.scope == SCOPE_STABLECOIN_MARKET else [asset],
                    providers=[provider],
                    min_event_time=earliest[key],
                    max_event_time=decision_time,
                    allow_estimated=self.allow_estimated,
                )
                fetched[key] = events
            coverage_asset = None if definition.scope == SCOPE_STABLECOIN_MARKET else asset
            if not self._coverage.covers(
                provider, definition.coverage_family, start, decision_time,
                allow_estimated=self.allow_estimated, as_of=cutoff, asset=coverage_asset,
            ):
                relevant = [e for e in events if e.event_type in definition.all_types and e.event_time > start]
                note = (
                    f" ({len(relevant)} event(s) present would only be a lower bound)" if relevant else ""
                )
                unusable = self._coverage.covers(
                    provider, definition.coverage_family, start, decision_time,
                    allow_estimated=True, asset=coverage_asset,
                )
                why = (
                    "a window is declared, but it rests on estimated receipt times this run does not allow, "
                    "or on data that had not yet been received at the decision time"
                    if unusable else "not declared complete over the window for this asset"
                )
                values[definition.name] = FeatureValue(
                    None, FeatureStatus.MISSING_NO_COVERAGE,
                    f"{provider}/{definition.coverage_family}: {why}{note}",
                )
                continue
            value = _compute_one(
                definition, events, decision_time, cutoff, self.allow_estimated, self.extra_delay_ms
            )
            values[definition.name] = value[0]
            used.update(value[1])

        digest = hashlib.sha256(
            json.dumps(
                {
                    "asset": asset, "decision_time": decision_time, "settings": self.settings(),
                    "features": sorted(values), "events": sorted(used),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return FeatureSnapshot(
            asset=asset, decision_time=decision_time, values=dict(sorted(values.items())),
            input_hash=digest, events_used=len(used), settings=self.settings(),
        )


# --------------------------------------------------------------------------
# metric computation
# --------------------------------------------------------------------------


def _compute_one(
    definition: FeatureDefinition, events: Sequence[ExternalEvent], decision_time: int,
    cutoff: int, allow_estimated: bool, extra_delay_ms: int,
) -> tuple[FeatureValue, set[str]]:
    metric = definition.metric
    if metric in (Metric.LATEST_VALUE, Metric.LATEST_USD, Metric.CHANGE_VALUE, Metric.CHANGE_USD):
        return _state_metric(definition, events, decision_time, cutoff, allow_estimated)

    positive = [
        e for e in events
        if e.event_type in definition.positive_types
        and decision_time - definition.window_ms < e.event_time <= decision_time
    ]
    negative = [
        e for e in events
        if e.event_type in definition.negative_types
        and decision_time - definition.window_ms < e.event_time <= decision_time
    ]
    ids = {e.event_id for e in positive + negative}
    n = len(positive) + len(negative)
    if metric is Metric.COUNT:
        return FeatureValue(len(positive) + len(negative), FeatureStatus.OK, "", n), ids
    if any(e.amount_usd is None for e in positive + negative):
        return (
            FeatureValue(None, FeatureStatus.MISSING_PARTIAL_AMOUNTS,
                         "an event in the window has no USD amount; a sum would be understated", n),
            ids,
        )
    pos = sum((e.amount_usd for e in positive), Decimal(0))
    neg = sum((e.amount_usd for e in negative), Decimal(0))
    if metric is Metric.USD_SUM:
        return FeatureValue(pos + neg, FeatureStatus.OK, "", n), ids
    if metric is Metric.USD_NET:
        return FeatureValue(pos - neg, FeatureStatus.OK, "", n), ids
    if metric is Metric.RATIO:
        if neg == 0:
            return FeatureValue(None, FeatureStatus.MISSING_UNDEFINED, "no volume on the denominator side", n), ids
        return FeatureValue(div(pos, neg), FeatureStatus.OK, "", n), ids
    if metric is Metric.IMBALANCE:
        if pos + neg == 0:
            return FeatureValue(None, FeatureStatus.MISSING_UNDEFINED, "no volume on either side", n), ids
        return FeatureValue(div(pos - neg, pos + neg), FeatureStatus.OK, "", n), ids
    raise ValueError(f"unhandled metric {metric}")


def _latest(
    events: Sequence[ExternalEvent], types: Sequence[Any], latest_time: int, max_age: int,
    available_by: int | None, allow_estimated: bool,
) -> ExternalEvent | None:
    eligible = [
        e for e in events
        if e.event_type in types
        and latest_time - max_age <= e.event_time <= latest_time
        and (available_by is None or e.is_available_at(available_by, allow_estimated=allow_estimated))
    ]
    return max(eligible, key=lambda e: (e.event_time, e.event_id)) if eligible else None


def _state_metric(
    definition: FeatureDefinition, events: Sequence[ExternalEvent], decision_time: int,
    cutoff: int, allow_estimated: bool,
) -> tuple[FeatureValue, set[str]]:
    metric = definition.metric
    use_value = metric in (Metric.LATEST_VALUE, Metric.CHANGE_VALUE)

    def reading(event: ExternalEvent | None) -> Decimal | None:
        if event is None:
            return None
        return event.value if use_value else event.amount_usd

    now_event = _latest(events, definition.positive_types, decision_time, definition.window_ms, None, allow_estimated)
    if now_event is None:
        return FeatureValue(None, FeatureStatus.MISSING_STALE, "no reading within the maximum age", 0), set()
    ids = {now_event.event_id}
    current = reading(now_event)
    if current is None:
        return FeatureValue(None, FeatureStatus.MISSING_UNDEFINED, "the latest reading carries no value", 1), ids
    if metric in (Metric.LATEST_VALUE, Metric.LATEST_USD):
        return FeatureValue(current, FeatureStatus.OK, "", 1), ids

    earlier_time = decision_time - definition.lookback_ms
    earlier_event = _latest(
        events, definition.positive_types, earlier_time, definition.window_ms,
        cutoff - definition.lookback_ms, allow_estimated,
    )
    if earlier_event is None:
        return FeatureValue(None, FeatureStatus.MISSING_STALE, "no earlier reading to compare with", 1), ids
    ids.add(earlier_event.event_id)
    earlier = reading(earlier_event)
    if earlier is None:
        return FeatureValue(None, FeatureStatus.MISSING_UNDEFINED, "the earlier reading carries no value", 2), ids
    return FeatureValue(current - earlier, FeatureStatus.OK, "", 2), ids
