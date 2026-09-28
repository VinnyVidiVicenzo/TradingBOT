"""The ingestion pipeline: raw payload -> normalized events -> validated -> stored.

In order, for every payload:

1. the raw payload is stored exactly as received, before anything is interpreted;
2. it is split into records (an unparseable record is quarantined, not fatal);
3. the provider adapter normalizes each record into events, or rejects it with a
   reason;
4. every event's timestamps are checked (future timestamps, out-of-order times
   and, for live feeds, staleness are quarantined);
5. events are inserted; a repeat of an event already stored is counted as a
   duplicate and changes nothing;
6. attribution claims are recorded as history; health is updated and persisted.

Provider failures during a live poll are turned into a report and a health
update. They never raise toward, and have no route to, anything that could sell,
buy, resize or bypass a risk check: this module cannot see a portfolio.
"""

from __future__ import annotations

import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from .base.clock import Clock
from .base.coverage import RECEIPTS_ESTIMATED, RECEIPTS_NONE, RECEIPTS_OBSERVED, CoverageInterval
from .base.formats import UnparseableRecord, parse_records
from .base.health import ProviderHealth
from .base.provider import NormalizeContext, NormalizedRecord, Provider
from .base.receipt import ReceiptMode, ReceiptPolicy
from .base.transport import Fetcher, FetchRequest
from .errors import (
    LiveIngestionNotEnabled,
    ProviderError,
    ProviderNotIntegrated,
    ProviderRateLimited,
    ProviderSchemaError,
    ProviderTimeout,
)
from .events import timestamp_problems
from .storage import IntelligenceStore

DEFAULT_TOLERANCE_MS = 5_000
# Quarantine reasons that mean "deliberately out of scope", not "information was lost". Every
# other reason is a record we could not use, which stops a window being declared complete.
NON_LOSS_REASONS = frozenset({"unsupported_transaction_type", "unsupported_kind", "no_external_destination"})


class IngestStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"  # some records were quarantined
    FAILED = "FAILED"  # nothing could be read


@dataclass(frozen=True, slots=True)
class IngestOptions:
    tolerance_ms: int = DEFAULT_TOLERANCE_MS
    # Live feeds set this so a replayed old event is not treated as fresh.
    max_staleness_ms: int | None = None
    # (from_ms, to_ms): the window this payload is declared to cover completely.
    # Without it no coverage is recorded and "no events" stays "unknown".
    covers: tuple[int, int] | None = None
    families: tuple[str, ...] | None = None
    # Coverage is withheld when records were lost (unreadable, invalid, out of order), because
    # a count of "0" over a window with holes would be a guess. Set this to accept the gap knowingly.
    accept_coverage_with_losses: bool = False
    # The assets the declared window is complete for. ``None`` means every asset the provider
    # reports; an export you filtered to BTC must say ``("BTC",)``, or ETH would read as zero.
    assets: tuple[str, ...] | None = None


@dataclass(slots=True)
class IngestReport:
    provider: str
    source_ref: str
    status: IngestStatus
    receipt_policy: dict[str, Any]
    payload_id: str | None = None
    records_read: int = 0
    events_inserted: int = 0
    duplicates: int = 0
    quarantined: int = 0
    quarantine_reasons: dict[str, int] = field(default_factory=dict)
    attributions_recorded: int = 0
    coverage_recorded: list[tuple[str, int, int]] = field(default_factory=list)
    coverage_withheld: str | None = None
    events_without_receipt_time: int = 0
    error: str | None = None
    retry_after_ms: int | None = None


def ingest_text(
    provider: Provider,
    text: str,
    *,
    suffix: str,
    source_ref: str,
    source_kind: str,
    store: IntelligenceStore,
    policy: ReceiptPolicy,
    health: ProviderHealth,
    clock: Clock,
    options: IngestOptions = IngestOptions(),
    latency_ms: int | None = None,
) -> IngestReport:
    now = clock.now_ms()
    payload_id = store.raw.record(
        provider=provider.name, source_kind=source_kind, source_ref=source_ref, body=text, received_at=now
    )
    report = IngestReport(
        provider=provider.name,
        source_ref=source_ref,
        status=IngestStatus.OK,
        receipt_policy=policy.describe(),
        payload_id=payload_id,
    )
    try:
        records = parse_records(text, suffix=suffix, envelope_keys=provider.envelope_keys)
    except ProviderSchemaError as exc:
        return _fail(report, store, health, now, "schema", str(exc))

    report.records_read = len(records)
    accepted: list[Any] = []
    duplicates = 0
    reasons: Counter[str] = Counter()

    def quarantine(reason: str, detail: str, reference: str | None, record: Any) -> None:
        reasons[reason] += 1
        store.quarantine.add(
            provider=provider.name, reason=reason, detail=detail[:300] or None,
            raw_payload_reference=reference, recorded_at=now,
            record=record if not isinstance(record, UnparseableRecord) else record.text,
        )

    for index, record in records:
        reference = f"raw:{payload_id}#{index}"
        if isinstance(record, UnparseableRecord):
            quarantine("unparseable_record", "line is not valid JSON", reference, record)
            continue
        context = NormalizeContext(provider.name, policy, now, reference, index)
        try:
            result = provider.normalize_record(record, context)
        except (ProviderNotIntegrated, LiveIngestionNotEnabled):
            raise  # a switched-off or placeholder provider is a programming decision, not bad data
        except Exception as exc:  # untrusted input: quarantine the record, never abort the import
            result = NormalizedRecord.rejected("invalid_record", f"{type(exc).__name__}: {exc}"[:300])
        for skipped in result.skipped:
            quarantine(skipped.reason, skipped.detail, reference, record)
        if result.rejection is not None:
            quarantine(result.rejection.reason, result.rejection.detail, reference, record)
            continue
        for event in result.events:
            problems = timestamp_problems(
                event, ingest_time_ms=now, tolerance_ms=options.tolerance_ms,
                max_staleness_ms=options.max_staleness_ms,
            )
            if problems:
                quarantine(problems[0], ",".join(problems), reference, record)
                continue
            if store.events.insert(event, inserted_at=now):
                accepted.append(event)
                report.events_inserted += 1
                if event.available_time is None:
                    report.events_without_receipt_time += 1
            else:
                duplicates += 1
        for attribution in result.attributions:
            if store.attributions.record(attribution, recorded_at=now):
                report.attributions_recorded += 1

    report.duplicates = duplicates
    report.quarantined = sum(reasons.values())
    report.quarantine_reasons = dict(sorted(reasons.items()))
    if report.quarantined:
        report.status = IngestStatus.PARTIAL

    if options.covers is not None:
        lost = {reason: n for reason, n in reasons.items() if reason not in NON_LOSS_REASONS}
        if report.events_without_receipt_time:
            lost["event_without_receipt_time"] = report.events_without_receipt_time
        if lost and not options.accept_coverage_with_losses:
            detail = ", ".join(f"{reason}={n}" for reason, n in sorted(lost.items()))
            report.coverage_withheld = (
                f"{sum(lost.values())} record(s) were lost or unusable ({detail}), so the window "
                "cannot be declared complete; fix the export, or set accept_coverage_with_losses "
                "to accept the gap knowingly"
            )
        else:
            lo, hi = options.covers
            basis = "live_ingestion" if source_kind == "fetch" else "declared_import_window"
            if lost:
                basis += "_losses_accepted"
            receipts, known_from = _coverage_receipts(policy, now)
            for family in options.families or provider.families:
                store.coverage.record(
                    CoverageInterval(
                        provider.name, family, lo, hi, basis, source_ref, receipts, known_from, options.assets
                    ),
                    recorded_at=now,
                )
                report.coverage_recorded.append((family, lo, hi))

    health.record_success(now, latency_ms=latency_ms)
    health.record_ingest(accepted, duplicates=duplicates, quarantined=report.quarantined)
    store.health.record(health.snapshot(now), recorded_at=now)
    return report


def import_file(
    provider: Provider,
    path: str | Path,
    *,
    store: IntelligenceStore,
    policy: ReceiptPolicy,
    health: ProviderHealth,
    clock: Clock,
    options: IngestOptions = IngestOptions(),
) -> IngestReport:
    """Import a user-provided export. ``policy`` is required: there is no default."""
    file_path = Path(path)
    report = IngestReport(
        provider=provider.name, source_ref=file_path.name, status=IngestStatus.FAILED,
        receipt_policy=policy.describe(),
    )
    try:
        text = file_path.read_text(encoding="utf-8-sig")  # tolerates the byte-order mark spreadsheets add
    except UnicodeDecodeError as exc:
        return _fail(report, store, health, clock.now_ms(), "schema", f"not UTF-8 text: {exc.reason}")
    except OSError as exc:
        return _fail(report, store, health, clock.now_ms(), "provider_error", f"cannot read file: {exc.strerror}")
    return ingest_text(
        provider, text, suffix=file_path.suffix, source_ref=file_path.name, source_kind="file",
        store=store, policy=policy, health=health, clock=clock, options=options,
    )


def poll_once(
    provider: Provider,
    fetcher: Fetcher,
    request: FetchRequest,
    *,
    store: IntelligenceStore,
    policy: ReceiptPolicy,
    health: ProviderHealth,
    clock: Clock,
    options: IngestOptions = IngestOptions(),
) -> IngestReport:
    """One live retrieval attempt. Provider trouble becomes a report, not an exception.

    ``LiveIngestionNotEnabled`` is different: it means the caller asked a provider
    that is deliberately switched off, which is a programming decision, so it
    propagates.
    """
    now = clock.now_ms()
    source_ref = _redacted(request.url)
    report = IngestReport(
        provider=provider.name, source_ref=source_ref, status=IngestStatus.FAILED,
        receipt_policy=policy.describe(),
    )
    try:
        payload = provider.fetch_payload(fetcher, request)
    except ProviderTimeout as exc:
        return _fail(report, store, health, now, "timeout", str(exc))
    except ProviderRateLimited as exc:
        report.retry_after_ms = exc.retry_after_ms
        return _fail(report, store, health, now, "rate_limit", str(exc))
    except ProviderSchemaError as exc:
        return _fail(report, store, health, now, "schema", str(exc))
    except ProviderError as exc:
        return _fail(report, store, health, now, "provider_error", str(exc))
    return ingest_text(
        provider, payload.text, suffix=payload.suffix, source_ref=payload.source_ref or source_ref,
        source_kind="fetch", store=store, policy=policy, health=health, clock=clock,
        options=options, latency_ms=payload.latency_ms,
    )


def _fail(
    report: IngestReport, store: IntelligenceStore, health: ProviderHealth,
    now: int, kind: str, message: str,
) -> IngestReport:
    report.status = IngestStatus.FAILED
    report.error = f"{kind}: {message}"
    health.record_failure(now, kind)
    store.health.record(health.snapshot(now), recorded_at=now)
    return report


def _coverage_receipts(policy: ReceiptPolicy, now: int) -> tuple[str, int | None]:
    """How a declared window depends on receipt times, and from when it may be relied on."""
    if policy.mode is ReceiptMode.NONE:
        return RECEIPTS_NONE, None
    if policy.mode is ReceiptMode.OBSERVED_NOW:
        return RECEIPTS_OBSERVED, now  # nothing was received before this moment
    if policy.mode is ReceiptMode.OBSERVED_FIELD:
        return RECEIPTS_OBSERVED, None
    return RECEIPTS_ESTIMATED, None


def _redacted(url: str) -> str:
    """The URL without its query string, so nothing sensitive is ever recorded."""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.rpartition("@")[2]  # never keep a user name or password
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def summarize(reports: Sequence[IngestReport]) -> dict[str, Any]:
    """A compact roll-up for a CLI or a log line."""
    return {
        "payloads": len(reports),
        "events_inserted": sum(r.events_inserted for r in reports),
        "duplicates": sum(r.duplicates for r in reports),
        "quarantined": sum(r.quarantined for r in reports),
        "failed": sum(1 for r in reports if r.status is IngestStatus.FAILED),
    }
