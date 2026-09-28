"""Executed-activity adapter, provider failures, the GET-only transport, health, placeholders."""

from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_research.backtest.portfolio import Portfolio
from crypto_research.decimals import dec
from crypto_research.models import PortfolioHolding

from external_intelligence.base.clock import FixedClock
from external_intelligence.base.health import HealthPolicy, ProviderHealth
from external_intelligence.base.provider import FetchedPayload, NormalizeContext, NormalizedRecord, Provider
from external_intelligence.base.ratelimit import RateLimiter
from external_intelligence.base.receipt import ReceiptPolicy
from external_intelligence.base.transport import FetchRequest, FetchResponse, UrllibGetFetcher, fetch_json
from external_intelligence.coinlobster import CoinLobsterAdapter
from external_intelligence.errors import (
    LiveIngestionNotEnabled,
    ProviderError,
    ProviderNotIntegrated,
    ProviderRateLimited,
    ProviderSchemaError,
    ProviderTimeout,
)
from external_intelligence.events import EventClass, EventType, Side
from external_intelligence.ingest import IngestOptions, IngestStatus, import_file, poll_once
from external_intelligence.placeholders import ALL_PLACEHOLDERS, ALL_SPECS
from external_intelligence.whale_alert import WhaleAlertAdapter
from external_intelligence.sanitize import clean_text, sanitize_metadata
from external_intelligence.storage import IntelligenceStore

from .ei_helpers import HOUR, MINUTE, T0, Temp, make_event, write_temp

NOW = T0 + HOUR


def executed(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "time_ms": T0 + MINUTE, "symbol": "BTC", **fields}


class Harness:
    def __init__(self, testcase: unittest.TestCase, provider: Provider | None = None) -> None:
        self.store = IntelligenceStore.open(":memory:")
        testcase.addCleanup(self.store.close)
        self.provider = provider or CoinLobsterAdapter()
        self.health = ProviderHealth(self.provider.name)

    def run(self, records: list[dict], policy: ReceiptPolicy = ReceiptPolicy.observed_now(),
            options: IngestOptions = IngestOptions(), now: int = NOW):
        with Temp() as tmp:
            path = write_temp(tmp, "x.json", json.dumps(records))
            return import_file(self.provider, path, store=self.store, policy=policy,
                               health=self.health, clock=FixedClock(now), options=options)

    def events(self):
        return list(self.store.events.iter_all())


class ExecutedActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(self)

    def test_an_executed_whale_trade_needs_an_explicit_side(self) -> None:
        report = self.h.run([executed("whale_trade", notional_usd="2500000")])
        self.assertEqual(report.events_inserted, 0)
        self.assertEqual(report.quarantine_reasons, {"invalid_record": 1})

    def test_buys_and_sells_become_distinct_event_types_with_their_side(self) -> None:
        self.h.run([
            executed("whale_trade", id="b", side="buy", notional_usd="2500000"),
            executed("whale_trade", id="s", side="SELL", notional_usd="1000000"),
        ])
        got = {e.provider_event_id: (e.event_type, e.side) for e in self.h.events()}
        self.assertEqual(got["b"], (EventType.WHALE_TRADE_BUY, Side.BUY))
        self.assertEqual(got["s"], (EventType.WHALE_TRADE_SELL, Side.SELL))

    def test_a_large_trade_without_a_reported_side_carries_none(self) -> None:
        self.h.run([executed("large_trade", id="x", notional_usd="900000")])
        event = self.h.events()[0]
        self.assertIs(event.event_type, EventType.LARGE_EXECUTED_TRADE)
        self.assertIsNone(event.side)

    def test_executed_activity_is_a_different_class_from_a_transfer(self) -> None:
        self.h.run([executed("whale_trade", side="buy", notional_usd="1")])
        self.assertIs(self.h.events()[0].event_class, EventClass.EXECUTED_TRADE)

    def test_liquidations_say_which_side_was_liquidated_and_carry_no_trade_side(self) -> None:
        self.h.run([
            executed("liquidation", id="l", liquidated_side="long", notional_usd="900000"),
            executed("liquidation", id="s", liquidated_side="short", notional_usd="100000"),
        ])
        got = {e.provider_event_id: (e.event_type, e.side) for e in self.h.events()}
        self.assertEqual(got["l"], (EventType.LONG_LIQUIDATION, None))
        self.assertEqual(got["s"], (EventType.SHORT_LIQUIDATION, None))
        report = self.h.run([executed("liquidation", id="bad", notional_usd="1")])
        self.assertEqual(report.quarantine_reasons, {"invalid_record": 1})

    def test_funding_keeps_its_sign_and_open_interest_needs_an_amount(self) -> None:
        self.h.run([
            executed("funding", id="f", funding_rate="-0.0001", interval_hours="8"),
            executed("open_interest", id="o", open_interest_usd="5000000000"),
        ])
        got = {e.provider_event_id: e for e in self.h.events()}
        self.assertEqual(got["f"].value, Decimal("-0.0001"))
        self.assertEqual(got["f"].value_unit, "funding_rate_per_interval")
        self.assertEqual(got["o"].amount_usd, Decimal("5000000000"))
        report = self.h.run([executed("open_interest", id="none"), executed("funding", id="nofund")])
        self.assertEqual(report.quarantine_reasons, {"invalid_record": 2})

    def test_the_venue_is_kept_as_the_source_names_it_and_never_becomes_binance_us(self) -> None:
        self.h.run([executed("whale_trade", id="v", side="buy", notional_usd="1", exchange="SomeVenue",
                             market_symbol="BTCUSDT")])
        event = self.h.events()[0]
        self.assertEqual(event.exchange, "SomeVenue")
        self.assertNotIn("BINANCE", (event.exchange or "").upper())

    def test_unknown_kinds_are_quarantined_not_guessed(self) -> None:
        report = self.h.run([executed("sentiment_score", id="q")])
        self.assertEqual(report.quarantine_reasons, {"unsupported_kind": 1})

    def test_time_in_seconds_is_accepted_and_milliseconds_in_the_seconds_field_are_not(self) -> None:
        good = {"kind": "large_trade", "time_s": T0 // 1000, "symbol": "BTC", "id": "g", "notional_usd": "1"}
        bad = {"kind": "large_trade", "time_s": T0, "symbol": "BTC", "id": "b", "notional_usd": "1"}
        report = self.h.run([good, bad])
        self.assertEqual((report.events_inserted, report.quarantine_reasons), (1, {"invalid_record": 1}))

    def test_records_without_an_id_get_a_deterministic_one_and_repeat_as_duplicates(self) -> None:
        record = executed("whale_trade", side="buy", notional_usd="1")
        self.h.run([record])
        second = self.h.run([record])
        self.assertEqual((second.events_inserted, second.duplicates), (0, 1))

    def test_no_directional_label_appears_in_what_is_stored(self) -> None:
        self.h.run([executed("whale_trade", side="buy", notional_usd="1")])
        blob = json.dumps(self.h.events()[0].to_dict()).lower()
        for word in ("bullish", "bearish", "signal", "sentiment"):
            self.assertNotIn(word, blob)

    def test_live_ingestion_is_off_and_the_reasons_are_stated(self) -> None:
        adapter = CoinLobsterAdapter()
        with self.assertRaises(LiveIngestionNotEnabled) as caught:
            adapter.fetch_payload(object(), FetchRequest("https://example.invalid/"))  # type: ignore[arg-type]
        text = str(caught.exception)
        self.assertIn("bulk extraction", text)
        self.assertIn("POST", text)
        self.assertIn("section 8", adapter.terms_note)


# --------------------------------------------------------------------------
# provider failure handling
# --------------------------------------------------------------------------


class FakeProvider(Provider):
    name = "fake"
    families = ("transfers",)

    def normalize_record(self, record: Any, ctx: NormalizeContext) -> NormalizedRecord:
        event = make_event(record["id"], provider="fake", event_time=record["t"], received=record["t"] + 1000)
        return NormalizedRecord(events=[event])

    def fetch_payload(self, fetcher: Any, request: FetchRequest) -> FetchedPayload:
        response = fetcher.fetch(request)
        return FetchedPayload(response.body.decode("utf-8"), ".jsonl", latency_ms=response.latency_ms)


class ScriptedFetcher:
    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def fetch(self, request: FetchRequest) -> FetchResponse:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def ok_response(*records: dict) -> FetchResponse:
    body = "\n".join(json.dumps(r) for r in records).encode("utf-8")
    return FetchResponse(200, body, {}, NOW, 42)


class ProviderFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(self, FakeProvider())
        self.request = FetchRequest("https://provider.example/feed")

    def poll(self, fetcher: ScriptedFetcher, options: IngestOptions = IngestOptions()):
        return poll_once(self.h.provider, fetcher, self.request, store=self.h.store,
                         policy=ReceiptPolicy.observed_now(), health=self.h.health,
                         clock=FixedClock(NOW), options=options)

    def test_a_timeout_becomes_a_failed_report_and_a_health_mark_never_an_exception(self) -> None:
        report = self.poll(ScriptedFetcher(ProviderTimeout("slow")))
        self.assertIs(report.status, IngestStatus.FAILED)
        self.assertIn("timeout", report.error)
        self.assertEqual((self.h.health.timeouts, self.h.health.consecutive_failures), (1, 1))
        self.assertEqual(self.h.store.events.count(), 0)

    def test_a_rate_limit_reports_when_to_come_back(self) -> None:
        report = self.poll(ScriptedFetcher(ProviderRateLimited("slow down", retry_after_ms=5000)))
        self.assertEqual(report.retry_after_ms, 5000)
        self.assertEqual(self.h.health.rate_limit_errors, 1)

    def test_an_unreadable_reply_and_a_provider_error_are_told_apart(self) -> None:
        self.poll(ScriptedFetcher(ProviderSchemaError("garbled")))
        self.poll(ScriptedFetcher(ProviderError("HTTP 500")))
        self.assertEqual((self.h.health.schema_failures, self.h.health.provider_errors), (1, 1))

    def test_repeated_failures_make_the_provider_unhealthy_and_success_recovers_it(self) -> None:
        for _ in range(3):
            self.poll(ScriptedFetcher(ProviderTimeout("slow")))
        status = self.h.health.assess(NOW)
        self.assertFalse(status.healthy)
        self.assertIn("consecutive_failures>=3", status.reasons)
        self.poll(ScriptedFetcher(ok_response({"id": 1, "t": T0})))
        self.assertTrue(self.h.health.assess(NOW).healthy)

    def test_a_good_poll_stores_events_and_records_latency(self) -> None:
        report = self.poll(ScriptedFetcher(ok_response({"id": 1, "t": T0}, {"id": 2, "t": T0 + 1})))
        self.assertEqual((report.status, report.events_inserted), (IngestStatus.OK, 2))
        self.assertEqual(self.h.health.mean_fetch_latency_ms, 42)

    def test_stale_data_is_rejected_at_ingest_when_a_limit_is_set(self) -> None:
        old = {"id": 1, "t": T0 - 10 * HOUR}
        report = self.poll(ScriptedFetcher(ok_response(old)), IngestOptions(max_staleness_ms=HOUR))
        self.assertEqual(report.quarantine_reasons, {"stale_at_ingest": 1})
        self.assertEqual(self.h.store.events.count(), 0)

    def test_health_snapshots_are_persisted_after_every_attempt(self) -> None:
        self.poll(ScriptedFetcher(ProviderTimeout("slow")))
        self.poll(ScriptedFetcher(ok_response({"id": 1, "t": T0})))
        self.assertEqual(len(self.h.store.health.history("fake")), 2)

    def test_a_provider_outage_leaves_a_portfolio_exactly_as_it_was(self) -> None:
        portfolio = Portfolio(
            starting_holdings=[PortfolioHolding(asset="BTC", quantity=dec("0.002"))],
            valuation_asset="USD",
        )
        before = json.dumps(portfolio.snapshot(), sort_keys=True, default=str)
        for _ in range(5):
            self.poll(ScriptedFetcher(ProviderTimeout("down")))
        self.assertEqual(json.dumps(portfolio.snapshot(), sort_keys=True, default=str), before)
        self.assertFalse(self.h.health.assess(NOW).healthy)
        # And nothing in a report could even express an action.
        names = " ".join(self.poll(ScriptedFetcher(ProviderTimeout("down"))).__dataclass_fields__)
        for word in ("order", "buy", "sell", "position", "portfolio"):
            self.assertNotIn(word, names)

    def test_a_provider_that_is_switched_off_is_a_programming_error_not_an_outage(self) -> None:
        with self.assertRaises(LiveIngestionNotEnabled):
            poll_once(CoinLobsterAdapter(), ScriptedFetcher(), self.request, store=self.h.store,
                      policy=ReceiptPolicy.observed_now(), health=self.h.health, clock=FixedClock(NOW))


# --------------------------------------------------------------------------
# what an import does with damaged or awkward input
# --------------------------------------------------------------------------


class CrashyProvider(FakeProvider):
    def normalize_record(self, record: Any, ctx: NormalizeContext) -> NormalizedRecord:
        if record.get("id") == 2:
            raise AttributeError("an adapter bug triggered by an odd record")
        return super().normalize_record(record, ctx)


class ImportRobustnessTests(unittest.TestCase):
    def import_lines(self, provider, lines, options=IngestOptions(), name="x.jsonl"):
        store = IntelligenceStore.open(":memory:")
        self.addCleanup(store.close)
        with Temp() as tmp:
            path = write_temp(tmp, name, "\n".join(lines))
            report = import_file(provider, path, store=store, policy=ReceiptPolicy.observed_now(),
                                 health=ProviderHealth(provider.name), clock=FixedClock(NOW), options=options)
        return store, report

    @staticmethod
    def good(pid):
        return json.dumps({"id": pid, "t": T0})

    def test_an_unexpected_error_on_one_record_quarantines_that_record_and_keeps_the_rest(self) -> None:
        store, report = self.import_lines(CrashyProvider(), [self.good(1), self.good(2), self.good(3)])
        self.assertEqual((report.events_inserted, report.quarantine_reasons), (2, {"invalid_record": 1}))
        self.assertEqual(store.events.count(), 2)

    def test_a_placeholder_provider_is_a_programming_error_and_is_not_swallowed(self) -> None:
        with self.assertRaises(ProviderNotIntegrated):
            self.import_lines(ALL_PLACEHOLDERS[0](), [self.good(1)])

    def test_coverage_is_withheld_when_records_were_lost_because_zero_would_then_be_a_guess(self) -> None:
        window = IngestOptions(covers=(T0, T0 + HOUR))
        store, report = self.import_lines(FakeProvider(), [self.good(1), "{not json", self.good(3)], window)
        self.assertEqual(report.coverage_recorded, [])
        self.assertIn("unparseable_record=1", report.coverage_withheld)
        self.assertFalse(store.coverage.index().covers("fake", "transfers", T0, T0 + HOUR))

    def test_a_gap_can_be_accepted_knowingly_and_a_clean_import_declares_its_window(self) -> None:
        window = IngestOptions(covers=(T0, T0 + HOUR), accept_coverage_with_losses=True)
        store, report = self.import_lines(FakeProvider(), [self.good(1), "{not json"], window)
        self.assertEqual(report.coverage_recorded, [("transfers", T0, T0 + HOUR)])
        self.assertTrue(store.coverage.index().covers("fake", "transfers", T0, T0 + HOUR))
        clean_store, clean = self.import_lines(FakeProvider(), [self.good(1)], IngestOptions(covers=(T0, T0 + HOUR)))
        self.assertIsNone(clean.coverage_withheld)
        self.assertTrue(clean_store.coverage.index().covers("fake", "transfers", T0, T0 + HOUR))

    def test_records_that_are_deliberately_out_of_scope_do_not_stop_a_window_being_declared(self) -> None:
        freeze = {"hash": "0xabc", "timestamp": T0 // 1000, "sub_transactions": [
            {"symbol": "BTC", "transaction_type": "freeze",
             "inputs": [{"amount": "1", "address": "a"}], "outputs": [{"amount": "1", "address": "b"}]}]}
        store, report = self.import_lines(WhaleAlertAdapter(), [json.dumps(freeze)], IngestOptions(covers=(T0, T0 + HOUR)))
        self.assertEqual(report.quarantine_reasons, {"unsupported_transaction_type": 1})
        self.assertIsNone(report.coverage_withheld)
        self.assertTrue(store.coverage.index().covers("whale_alert", "transfers", T0, T0 + HOUR))

    def flat_record(self):
        return json.dumps({"id": "c1", "timestamp": T0 // 1000, "symbol": "btc", "amount": 10, "amount_usd": 600000,
                           "from_address": "0xaaa", "to_address": "0xbbb"})

    def import_with(self, policy, options):
        store = IntelligenceStore.open(":memory:")
        self.addCleanup(store.close)
        with Temp() as tmp:
            path = write_temp(tmp, "x.jsonl", self.flat_record())
            report = import_file(WhaleAlertAdapter(), path, store=store, policy=policy,
                                 health=ProviderHealth("whale_alert"), clock=FixedClock(NOW), options=options)
        return store, report

    def test_coverage_records_how_the_window_depends_on_receipt_times(self) -> None:
        window = IngestOptions(covers=(T0, T0 + HOUR))
        estimated, _ = self.import_with(ReceiptPolicy.assumed_delay(30, "test assumption"), window)
        self.assertFalse(estimated.coverage.index().covers("whale_alert", "transfers", T0, T0 + HOUR))
        self.assertTrue(estimated.coverage.index().covers("whale_alert", "transfers", T0, T0 + HOUR, allow_estimated=True))
        now_store, _ = self.import_with(ReceiptPolicy.observed_now(), window)
        index = now_store.coverage.index()
        self.assertFalse(index.covers("whale_alert", "transfers", T0, T0 + HOUR, as_of=NOW - 1))  # not yet received
        self.assertTrue(index.covers("whale_alert", "transfers", T0, T0 + HOUR, as_of=NOW))

    def test_events_with_no_receipt_time_withhold_the_window_and_can_never_be_relied_on(self) -> None:
        window = IngestOptions(covers=(T0, T0 + HOUR))
        store, report = self.import_with(ReceiptPolicy.none(), window)
        self.assertIn("event_without_receipt_time=1", report.coverage_withheld)
        self.assertEqual(store.coverage.index().intervals("whale_alert", "transfers"), ())
        accepted, _ = self.import_with(
            ReceiptPolicy.none(), IngestOptions(covers=(T0, T0 + HOUR), accept_coverage_with_losses=True)
        )
        (interval,) = accepted.coverage.index().intervals("whale_alert", "transfers")
        self.assertEqual(interval.receipts, "none")
        self.assertFalse(accepted.coverage.index().covers("whale_alert", "transfers", T0, T0 + HOUR, allow_estimated=True))

    def test_an_export_filtered_to_some_assets_declares_coverage_for_those_only(self) -> None:
        store, report = self.import_with(
            ReceiptPolicy.observed_now(), IngestOptions(covers=(T0, T0 + HOUR), assets=("btc",))
        )
        index = store.coverage.index()
        self.assertEqual(index.intervals("whale_alert", "transfers")[0].assets, ("BTC",))
        self.assertTrue(index.covers("whale_alert", "transfers", T0, T0 + HOUR, asset="BTC", as_of=NOW))
        self.assertFalse(index.covers("whale_alert", "transfers", T0, T0 + HOUR, asset="ETH", as_of=NOW))

    def test_a_report_never_keeps_a_query_string_or_embedded_credentials(self) -> None:
        h = Harness(self, FakeProvider())
        request = FetchRequest("https://someone:hunter2@provider.example/feed?limit=5#frag")
        report = poll_once(h.provider, ScriptedFetcher(ProviderTimeout("slow")), request, store=h.store,
                           policy=ReceiptPolicy.observed_now(), health=h.health, clock=FixedClock(NOW))
        self.assertEqual(report.source_ref, "https://provider.example/feed")
        for secret in ("hunter2", "someone", "limit"):
            self.assertNotIn(secret, repr(report))

    def test_a_byte_order_mark_from_a_spreadsheet_does_not_hide_the_first_column(self) -> None:
        header = "timestamp,symbol,amount,amount_usd,from_address,to_address,id"
        row = f"{T0 // 1000},btc,10,600000,0xaaa,0xbbb,csv-1"
        store = IntelligenceStore.open(":memory:")
        self.addCleanup(store.close)
        with Temp() as tmp:
            path = Path(tmp) / "export.csv"
            path.write_bytes(("﻿" + header + "\n" + row + "\n").encode("utf-8"))
            report = import_file(WhaleAlertAdapter(), path, store=store, policy=ReceiptPolicy.observed_now(),
                                 health=ProviderHealth("whale_alert"), clock=FixedClock(NOW))
        self.assertEqual((report.events_inserted, report.quarantined), (1, 0))


# --------------------------------------------------------------------------
# the fetcher
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: bytes = b"{}", status: int = 200, headers: dict | None = None) -> None:
        self._body, self.status, self.headers = body, status, headers or {}
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self._body if amount < 0 else self._body[:amount]

    def close(self) -> None:
        self.closed = True


class TransportTests(unittest.TestCase):
    def fetcher(self, opener, **kwargs) -> UrllibGetFetcher:
        return UrllibGetFetcher(allowed_hosts=["provider.example"], clock=FixedClock(NOW), opener=opener, **kwargs)

    def test_it_sends_one_get_with_only_a_fixed_user_agent(self) -> None:
        seen: list = []

        def opener(request, timeout):
            seen.append((request, timeout))
            return FakeResponse(b'{"a": 1}')

        response = self.fetcher(opener).fetch(FetchRequest("https://provider.example/feed", {"limit": "5"}, 3.0))
        request, timeout = seen[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 3.0)
        self.assertEqual(list(request.headers), ["User-agent"])
        self.assertIn("limit=5", request.full_url)
        self.assertEqual(json.loads(response.body), {"a": 1})

    def test_it_refuses_anything_that_looks_like_authentication_before_sending(self) -> None:
        def opener(request, timeout):  # pragma: no cover - must not be reached
            raise AssertionError("a request was sent")

        f = self.fetcher(opener)
        for url in (
            "http://provider.example/feed",
            "https://other.example/feed",
            "https://user:pass@provider.example/feed",
            "https://provider.example/feed?api_key=abc",
            "https://provider.example/feed?Token=abc",
        ):
            with self.assertRaises(ProviderError, msg=url):
                f.fetch(FetchRequest(url))
        with self.assertRaises(ProviderError):
            f.fetch(FetchRequest("https://provider.example/feed", {"signature": "x"}))

    def test_timeouts_are_mapped_whichever_way_they_arrive(self) -> None:
        for error in (TimeoutError(), socket.timeout(), urllib.error.URLError(socket.timeout())):
            def opener(request, timeout, error=error):
                raise error

            with self.assertRaises(ProviderTimeout):
                self.fetcher(opener).fetch(FetchRequest("https://provider.example/feed"))

    def test_http_429_carries_retry_after_and_other_errors_are_provider_errors(self) -> None:
        def limited(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {"Retry-After": "5"}, io.BytesIO())

        with self.assertRaises(ProviderRateLimited) as caught:
            self.fetcher(limited).fetch(FetchRequest("https://provider.example/feed"))
        self.assertEqual(caught.exception.retry_after_ms, 5000)

        def broken(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 500, "Boom", {}, io.BytesIO())

        with self.assertRaises(ProviderError):
            self.fetcher(broken).fetch(FetchRequest("https://provider.example/feed"))
        with self.assertRaises(ProviderError):
            self.fetcher(lambda r, timeout: FakeResponse(status=503)).fetch(FetchRequest("https://provider.example/f"))

    def test_an_oversized_reply_is_refused(self) -> None:
        f = self.fetcher(lambda r, timeout: FakeResponse(b"x" * 100), max_bytes=10)
        with self.assertRaises(ProviderError):
            f.fetch(FetchRequest("https://provider.example/feed"))

    def test_a_reply_that_is_not_json_is_a_schema_error(self) -> None:
        f = self.fetcher(lambda r, timeout: FakeResponse(b"<html>"))
        with self.assertRaises(ProviderSchemaError):
            fetch_json(f, FetchRequest("https://provider.example/feed"))

    def test_the_local_rate_limiter_stops_a_call_before_it_is_made(self) -> None:
        clock = FixedClock(NOW)
        calls: list = []
        f = UrllibGetFetcher(
            allowed_hosts=["provider.example"], clock=clock, rate_limiter=RateLimiter(2, 1000, clock),
            opener=lambda r, timeout: calls.append(1) or FakeResponse(),
        )
        f.fetch(FetchRequest("https://provider.example/a"))
        f.fetch(FetchRequest("https://provider.example/a"))
        with self.assertRaises(ProviderRateLimited) as caught:
            f.fetch(FetchRequest("https://provider.example/a"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(caught.exception.retry_after_ms, 1000)
        clock.advance(1000)
        f.fetch(FetchRequest("https://provider.example/a"))
        self.assertEqual(len(calls), 3)


# --------------------------------------------------------------------------
# health, placeholders, sanitising
# --------------------------------------------------------------------------


class HealthMetricTests(unittest.TestCase):
    def test_a_provider_that_never_succeeded_is_not_healthy(self) -> None:
        self.assertIn("never_succeeded", ProviderHealth("p").assess(NOW).reasons)

    def test_silence_beyond_the_limit_makes_it_unhealthy(self) -> None:
        health = ProviderHealth("p", HealthPolicy(max_silence_ms=HOUR))
        health.record_success(NOW)
        self.assertTrue(health.assess(NOW + HOUR).healthy)
        self.assertIn("no_recent_successful_update", health.assess(NOW + HOUR + 1).reasons)

    def test_schema_failures_only_count_once_there_are_enough_records(self) -> None:
        health = ProviderHealth("p", HealthPolicy(min_records_for_fractions=10))
        health.record_success(NOW)
        health.record_failure(NOW, "schema")
        self.assertTrue(health.assess(NOW).healthy)  # one failure in one record proves nothing
        for _ in range(9):
            health.record_failure(NOW, "schema")
            health.record_success(NOW)
        self.assertIn("schema_failure_fraction_too_high", health.assess(NOW).reasons)

    def test_the_snapshot_carries_every_metric_the_spec_lists(self) -> None:
        health = ProviderHealth("p", HealthPolicy(expected_max_gap_ms=HOUR))
        health.record_success(NOW, latency_ms=30)
        health.record_ingest(
            [make_event(1, event_time=T0, received=T0 + 5000), make_event(2, event_time=T0 + 3 * HOUR, usd=None, received=T0 + 3 * HOUR)],
            duplicates=1, quarantined=2,
        )
        snapshot = health.snapshot(NOW)
        for key in (
            "last_success_at", "last_event_time", "gaps", "schema_failures", "rate_limit_errors",
            "mean_fetch_latency_ms", "mean_ingest_lag_ms", "duplicate_events", "dropped_events",
            "unknown_entity_fraction", "missing_amount_fraction",
        ):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["gap_count"], 1)
        self.assertEqual((snapshot["duplicate_events"], snapshot["dropped_events"]), (1, 2))
        self.assertEqual(snapshot["missing_amount_fraction"], 0.5)
        self.assertEqual(snapshot["unknown_entity_fraction"], 1.0)
        json.dumps(snapshot)  # JSON-safe


class PlaceholderTests(unittest.TestCase):
    def test_each_placeholder_documents_what_the_spec_asks_for(self) -> None:
        self.assertEqual({s.name for s in ALL_SPECS}, {"arkham", "nansen", "cryptoquant", "glassnode"})
        for spec in ALL_SPECS:
            described = spec.to_dict()
            for key in ("intended_role", "candidate_features", "update_frequency",
                        "historical_data_requirements", "likely_cost_category",
                        "attribution_uncertainty", "justifying_hypothesis"):
                self.assertTrue(described[key], f"{spec.name}.{key} is empty")
            self.assertIn("placeholder", spec.status)

    def test_a_placeholder_refuses_to_do_real_work(self) -> None:
        for cls in ALL_PLACEHOLDERS:
            provider = cls()
            with self.assertRaises(ProviderNotIntegrated):
                provider.normalize_record({}, None)  # type: ignore[arg-type]
            with self.assertRaises(LiveIngestionNotEnabled):
                provider.fetch_payload(object(), FetchRequest("https://example.invalid/"))  # type: ignore[arg-type]

    def test_costs_are_categories_not_prices(self) -> None:
        for spec in ALL_SPECS:
            self.assertNotRegex(spec.likely_cost_category, r"[$]\s?\d")


class SanitiseTests(unittest.TestCase):
    def test_control_and_direction_override_characters_are_removed(self) -> None:
        self.assertEqual(clean_text("a\x00b‮c​d\te\nf"), "abcd e f")

    def test_empty_and_overlong_text(self) -> None:
        self.assertIsNone(clean_text("  \x00 "))
        self.assertEqual(len(clean_text("x" * 999, max_length=10)), 10)

    def test_metadata_is_bounded_and_json_safe(self) -> None:
        nested: Any = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        cleaned = sanitize_metadata({"n": nested, "big": list(range(500)), "bad": object(), "nan": float("nan")})
        self.assertLessEqual(len(cleaned["big"]), 50)
        self.assertIsNone(cleaned["nan"])
        json.dumps(cleaned)


if __name__ == "__main__":
    unittest.main()
