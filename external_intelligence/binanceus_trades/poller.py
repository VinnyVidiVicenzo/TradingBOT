"""Polling Binance.US's trade feed without gaps, duplicates, or a guessed "zero".

A feature pipeline can only ever report a true zero over a window the provider
was *declared* complete for (see ``base/coverage.py``); otherwise "no large
trades" and "we did not look" are indistinguishable. Getting that declaration
right for a live feed means never skipping a stretch of trades between two
polls, which is what this module exists to guarantee:

* every poll resumes from exactly the trade id after the last one it fully
  saw -- never "the most recent N trades", which could silently skip whatever
  happened while nobody was polling;
* the coverage window declared for a poll is bounded by the trades *actually
  returned*, not by wall-clock time, so a poll can never claim to cover a
  stretch it did not really see;
* the very first poll for a symbol only learns where the feed currently is
  and declares nothing: there is no honest way to claim coverage for history
  before this project started watching.

The cursor (the last trade id fully scanned) is the thing that makes "resume
without a gap" possible; it is stored append-only, like everything else in this
zone -- see ``storage.CursorRepository``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..base.clock import Clock
from ..base.coverage import FAMILY_EXECUTED
from ..base.health import ProviderHealth
from ..base.receipt import ReceiptPolicy
from ..base.transport import Fetcher
from ..errors import ProviderError, ProviderSchemaError
from ..ingest import IngestOptions, ingest_text
from ..storage import IntelligenceStore
from .adapter import BinanceUSTradesAdapter, RawTrade, records_to_jsonl, trades_to_flat_records

SEED_LOOKBACK = 1  # the seed poll learns only the single most recent trade id


@dataclass(frozen=True, slots=True)
class PollTarget:
    """One market this poller watches. Quote asset is fixed to USD -- see adapter.py."""

    market_symbol: str  # Binance.US's own symbol, e.g. "BTCUSD"
    asset: str  # the base asset, e.g. "BTC" -- never guessed from the symbol string
    quote_asset: str = "USD"

    def __post_init__(self) -> None:
        if (self.quote_asset != "USD" or not self.asset.isalnum()
                or self.asset != self.asset.upper()
                or self.market_symbol != self.asset + "USD"):
            raise ValueError(
                f"{self.market_symbol}: only USD-quoted pairs are supported; a {self.quote_asset} "
                "notional is not a USD notional and must not be reported as one"
            )


@dataclass(slots=True)
class PollReport:
    status: str  # "SEEDED" | "NO_NEW_TRADES" | "OK"
    target: PollTarget
    trades_seen: int = 0
    events_inserted: int = 0
    duplicates: int = 0
    quarantined: int = 0
    coverage_from: int | None = None
    coverage_to: int | None = None
    cursor_before: int | None = None
    cursor_after: int | None = None
    detail: str = ""


def poll(
    adapter: BinanceUSTradesAdapter,
    fetcher: Fetcher,
    store: IntelligenceStore,
    target: PollTarget,
    *,
    policy: ReceiptPolicy,
    health: ProviderHealth,
    clock: Clock,
) -> PollReport:
    """One poll of one symbol. Safe to call as often as you like.

    A network or schema failure is caught here, recorded against ``health``,
    and raised as ``ProviderError`` -- it never leaves the cursor or coverage
    changed, so the next successful poll resumes from exactly where the last
    successful one left off.
    """
    now = clock.now_ms()
    cursor_text = store.cursors.latest(adapter.name, target.market_symbol)
    cursor_before = int(cursor_text) if cursor_text is not None else None

    try:
        if cursor_before is None:
            return _seed(adapter, fetcher, store, target, health=health, clock=clock)
        trades = adapter.fetch_raw(fetcher, target.market_symbol, from_id=cursor_before + 1)
        _validate_continuity(trades, cursor_before)
    except ProviderError as exc:
        now = clock.now_ms()
        _retain_raw(adapter, store, target, now, error=str(exc))
        health.record_failure(now, "provider_error")
        store.health.record(health.snapshot(now), recorded_at=now)
        raise

    now = clock.now_ms()
    _retain_raw(adapter, store, target, now)

    if not trades:
        health.record_success(now)
        store.health.record(health.snapshot(now), recorded_at=now)
        return PollReport("NO_NEW_TRADES", target, cursor_before=cursor_before, cursor_after=cursor_before)

    try:
        return _ingest_batch(adapter, store, target, trades, policy=policy, health=health, now=now, cursor_before=cursor_before)
    except ProviderError as exc:
        health.record_failure(now, "provider_error")
        store.health.record(health.snapshot(now), recorded_at=now)
        raise


def _validate_continuity(trades: list[RawTrade], cursor: int) -> None:
    expected = cursor + 1
    previous_time = None
    for trade in trades:
        if trade.trade_id != expected or (previous_time is not None and trade.time_ms < previous_time):
            raise ProviderSchemaError("trade page is not contiguous and chronological; cursor unchanged")
        expected += 1
        previous_time = trade.time_ms


def _retain_raw(adapter, store, target, now, *, error=None):
    response = adapter.last_response
    if response is None:
        return
    reference = store.raw.record(
        provider=adapter.name, source_kind="fetch", source_ref=target.market_symbol,
        body=response.body.decode("utf-8", errors="backslashreplace"), received_at=now,
    )
    if error is not None:
        store.quarantine.add(provider=adapter.name, reason="invalid_trade_page",
                             detail=error[:300], raw_payload_reference=reference,
                             recorded_at=now)


def _seed(
    adapter: BinanceUSTradesAdapter, fetcher: Fetcher, store: IntelligenceStore, target: PollTarget,
    *, health: ProviderHealth, clock: Clock,
) -> PollReport:
    """Learn where the feed currently is. Declares no coverage and stores no event."""
    trades = adapter.fetch_raw(fetcher, target.market_symbol, limit=SEED_LOOKBACK)
    now = clock.now_ms()
    _retain_raw(adapter, store, target, now)
    health.record_success(now)
    store.health.record(health.snapshot(now), recorded_at=now)
    if not trades:
        return PollReport(
            "SEEDED", target, detail=f"{target.market_symbol} has no trade history to seed from yet",
        )
    seed_id = trades[-1].trade_id
    store.cursors.record(adapter.name, target.market_symbol, str(seed_id), recorded_at=now)
    return PollReport(
        "SEEDED", target, cursor_after=seed_id,
        detail="coverage begins at the next poll; history before this moment is not claimed",
    )


def _ingest_batch(
    adapter: BinanceUSTradesAdapter, store: IntelligenceStore, target: PollTarget,
    trades: list[RawTrade], *, policy: ReceiptPolicy, health: ProviderHealth, now: int, cursor_before: int,
) -> PollReport:
    records = trades_to_flat_records(
        trades, adapter.large_notional_usd, adapter.whale_notional_usd,
        asset=target.asset, quote_asset=target.quote_asset, market_symbol=target.market_symbol,
    )
    coverage_from = min(t.time_ms for t in trades)
    coverage_to = max(t.time_ms for t in trades)
    cursor_after = max(t.trade_id for t in trades)
    time_key = target.market_symbol + ":event_time"
    previous_time = store.cursors.latest(adapter.name, time_key)
    if previous_time is not None:
        if coverage_from < int(previous_time):
            raise ProviderSchemaError("trade timestamps moved backwards; cursor unchanged")
        coverage_from = int(previous_time)

    ingest_report = ingest_text(
        adapter, records_to_jsonl(records), suffix=".jsonl",
        source_ref=f"{target.market_symbol} trades {cursor_before + 1}-{cursor_after}",
        source_kind="fetch", store=store, policy=policy, health=health, clock=_FixedNow(now),
        options=IngestOptions(
            covers=(coverage_from, coverage_to), families=(FAMILY_EXECUTED,), assets=(target.asset,),
        ),
    )
    # The cursor advances regardless of whether anything in this batch was large
    # enough to report: every trade up to it was genuinely seen.
    if ingest_report.coverage_withheld or ingest_report.quarantined:
        raise ProviderSchemaError("trade ingestion incomplete; cursor unchanged")
    store.cursors.record(adapter.name, target.market_symbol, str(cursor_after), recorded_at=now)
    store.cursors.record(adapter.name, time_key, str(coverage_to), recorded_at=now)

    return PollReport(
        "OK", target, trades_seen=len(trades), events_inserted=ingest_report.events_inserted,
        duplicates=ingest_report.duplicates, quarantined=ingest_report.quarantined,
        coverage_from=coverage_from, coverage_to=coverage_to,
        cursor_before=cursor_before, cursor_after=cursor_after,
        detail=("Page limit reached: more trades may remain. Poll again to continue; coverage ends at the last returned trade."
                if len(trades) == 1000 else ""),
    )


class _FixedNow:
    """A ``Clock`` fixed at receipt after the network request completed."""

    def __init__(self, now: int) -> None:
        self._now = now

    def now_ms(self) -> int:
        return self._now
