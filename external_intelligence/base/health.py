"""Per-provider data-quality tracking.

Tracked, per provider: the last successful update, the last event timestamp,
data gaps, schema failures, rate-limit errors, timeouts, latency, duplicate
events, dropped (quarantined) events, the fraction of transfers with no
attribution, and the fraction of events with no USD amount.

What an unhealthy provider does -- and does not -- cause. It is marked
unhealthy and its features stop being relied on (they become missing, see
``features.pipeline``); dependent experiments pause or fall back to baseline
features. It never causes a sale, a purchase, a change of position size or any
bypass of a risk check. This module has no way to do any of those things: it
cannot see a portfolio or an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..events import EventClass, ExternalEvent, ReceiptBasis
from ..attribution import EntityType

MAX_GAPS_KEPT = 100
MAX_LAG_SAMPLES = 500


@dataclass(frozen=True, slots=True)
class HealthPolicy:
    """Thresholds for calling a provider unhealthy. All are stated, none hidden."""

    max_silence_ms: int | None = None  # no successful update for this long
    max_consecutive_failures: int = 3
    max_schema_failure_fraction: float = 0.25
    max_rate_limit_errors: int | None = None
    expected_max_gap_ms: int | None = None  # a quiet spell longer than this is a "gap"
    min_records_for_fractions: int = 20  # do not judge a fraction on a handful of records


@dataclass(frozen=True, slots=True)
class HealthStatus:
    healthy: bool
    reasons: tuple[str, ...] = ()


class ProviderHealth:
    """Mutable counters plus the policy used to judge them."""

    def __init__(self, provider: str, policy: HealthPolicy | None = None) -> None:
        self.provider = provider
        self.policy = policy or HealthPolicy()
        self.attempts = 0
        self.successes = 0
        self.consecutive_failures = 0
        self.last_attempt_at: int | None = None
        self.last_success_at: int | None = None
        self.last_event_time: int | None = None
        self.timeouts = 0
        self.rate_limit_errors = 0
        self.schema_failures = 0
        self.provider_errors = 0
        self.records_seen = 0
        self.events_accepted = 0
        self.duplicate_events = 0
        self.dropped_events = 0
        self.transfer_events = 0
        self.unattributed_transfers = 0
        self.amount_expected_events = 0
        self.missing_amount_events = 0
        self.fetch_latency_ms: list[int] = []
        self.ingest_lag_ms: list[int] = []
        self.gaps: list[tuple[int, int]] = []

    # -- recording -----------------------------------------------------------

    @classmethod
    def from_snapshot(cls, provider: str, snapshot: dict[str, Any] | None) -> "ProviderHealth":
        health = cls(provider)
        if snapshot is not None:
            for name in vars(health):
                if name not in {"provider", "policy"} and name in snapshot:
                    setattr(health, name, snapshot[name])
        return health

    def record_success(self, now_ms: int, *, latency_ms: int | None = None) -> None:
        self.attempts += 1
        self.successes += 1
        self.consecutive_failures = 0
        self.last_attempt_at = now_ms
        self.last_success_at = now_ms
        if latency_ms is not None:
            self.fetch_latency_ms.append(latency_ms)
            del self.fetch_latency_ms[:-MAX_LAG_SAMPLES]

    def record_failure(self, now_ms: int, kind: str) -> None:
        """``kind``: ``timeout``, ``rate_limit``, ``schema`` or ``provider_error``."""
        self.attempts += 1
        self.consecutive_failures += 1
        self.last_attempt_at = now_ms
        if kind == "timeout":
            self.timeouts += 1
        elif kind == "rate_limit":
            self.rate_limit_errors += 1
        elif kind == "schema":
            self.schema_failures += 1
        else:
            self.provider_errors += 1

    def record_ingest(
        self,
        accepted: Sequence[ExternalEvent],
        *,
        duplicates: int = 0,
        quarantined: int = 0,
        schema_failures: int = 0,
    ) -> None:
        self.records_seen += len(accepted) + duplicates + quarantined
        self.events_accepted += len(accepted)
        self.duplicate_events += duplicates
        self.dropped_events += quarantined
        self.schema_failures += schema_failures
        for event in accepted:
            if self.last_event_time is None or event.event_time > self.last_event_time:
                self.last_event_time = event.event_time
            if event.event_class is EventClass.ON_CHAIN_TRANSFER:
                self.transfer_events += 1
                if _unattributed(event):
                    self.unattributed_transfers += 1
            if event.event_class is not EventClass.DERIVATIVES_STATE:
                self.amount_expected_events += 1
                if event.amount_usd is None:
                    self.missing_amount_events += 1
            if event.received_time_basis is ReceiptBasis.OBSERVED and event.system_received_time is not None:
                self.ingest_lag_ms.append(event.system_received_time - event.event_time)
                del self.ingest_lag_ms[:-MAX_LAG_SAMPLES]
        threshold = self.policy.expected_max_gap_ms
        if threshold is not None and len(accepted) > 1:
            times = sorted(event.event_time for event in accepted)
            for earlier, later in zip(times, times[1:]):
                if later - earlier > threshold and len(self.gaps) < MAX_GAPS_KEPT:
                    self.gaps.append((earlier, later))

    # -- derived -------------------------------------------------------------

    @property
    def unknown_entity_fraction(self) -> float | None:
        return _fraction(self.unattributed_transfers, self.transfer_events)

    @property
    def missing_amount_fraction(self) -> float | None:
        return _fraction(self.missing_amount_events, self.amount_expected_events)

    @property
    def schema_failure_fraction(self) -> float | None:
        return _fraction(self.schema_failures, self.records_seen + self.schema_failures)

    @property
    def mean_fetch_latency_ms(self) -> float | None:
        return _mean(self.fetch_latency_ms)

    @property
    def mean_ingest_lag_ms(self) -> float | None:
        return _mean(self.ingest_lag_ms)

    def assess(self, now_ms: int) -> HealthStatus:
        """Healthy, or the stated reasons it is not."""
        reasons: list[str] = []
        policy = self.policy
        if self.last_success_at is None:
            reasons.append("never_succeeded")
        elif policy.max_silence_ms is not None and now_ms - self.last_success_at > policy.max_silence_ms:
            reasons.append("no_recent_successful_update")
        if self.consecutive_failures >= policy.max_consecutive_failures:
            reasons.append(f"consecutive_failures>={policy.max_consecutive_failures}")
        fraction = self.schema_failure_fraction
        if (
            fraction is not None
            and self.records_seen + self.schema_failures >= policy.min_records_for_fractions
            and fraction > policy.max_schema_failure_fraction
        ):
            reasons.append("schema_failure_fraction_too_high")
        if policy.max_rate_limit_errors is not None and self.rate_limit_errors > policy.max_rate_limit_errors:
            reasons.append("too_many_rate_limit_errors")
        return HealthStatus(not reasons, tuple(reasons))

    def snapshot(self, now_ms: int) -> dict[str, Any]:
        status = self.assess(now_ms)
        return {
            "provider": self.provider,
            "as_of": now_ms,
            "healthy": status.healthy,
            "reasons": list(status.reasons),
            "last_success_at": self.last_success_at,
            "last_attempt_at": self.last_attempt_at,
            "last_event_time": self.last_event_time,
            "attempts": self.attempts,
            "successes": self.successes,
            "consecutive_failures": self.consecutive_failures,
            "timeouts": self.timeouts,
            "rate_limit_errors": self.rate_limit_errors,
            "schema_failures": self.schema_failures,
            "provider_errors": self.provider_errors,
            "records_seen": self.records_seen,
            "events_accepted": self.events_accepted,
            "duplicate_events": self.duplicate_events,
            "dropped_events": self.dropped_events,
            "unknown_entity_fraction": self.unknown_entity_fraction,
            "missing_amount_fraction": self.missing_amount_fraction,
            "mean_fetch_latency_ms": self.mean_fetch_latency_ms,
            "mean_ingest_lag_ms": self.mean_ingest_lag_ms,
            "gap_count": len(self.gaps),
            "gaps": [list(gap) for gap in self.gaps],
            "transfer_events": self.transfer_events,
            "unattributed_transfers": self.unattributed_transfers,
            "amount_expected_events": self.amount_expected_events,
            "missing_amount_events": self.missing_amount_events,
            "fetch_latency_ms": list(self.fetch_latency_ms),
            "ingest_lag_ms": list(self.ingest_lag_ms),
        }


def _unattributed(event: ExternalEvent) -> bool:
    """True when the provider attributed neither end of a transfer."""
    unattributed = (None, EntityType.UNKNOWN)
    return event.source_entity_type in unattributed and event.destination_entity_type in unattributed


def _fraction(part: int, whole: int) -> float | None:
    return None if whole <= 0 else part / whole


def _mean(values: Sequence[int]) -> float | None:
    return None if not values else sum(values) / len(values)
