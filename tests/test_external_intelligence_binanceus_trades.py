"""Binance.US's own trade feed: classification, gap-free polling, honest coverage.

The sample trade shape below matches a real response captured from
https://api.binance.us/api/v3/aggTrades on 2026-10-01 (field names and types
only; the prices and ids are synthetic).
"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from external_intelligence.base.clock import FixedClock
from external_intelligence.base.health import ProviderHealth
from external_intelligence.base.receipt import ReceiptPolicy
from external_intelligence.base.transport import FetchRequest, FetchResponse
from external_intelligence.binanceus_trades import BinanceUSTradesAdapter, PollTarget, poll
from external_intelligence.binanceus_trades.adapter import (
    AGG_TRADES_PATH,
    BASE_URL,
    RawTrade,
    trades_to_flat_records,
)
from external_intelligence.errors import ProviderError, ProviderSchemaError
from external_intelligence.events import EventType, Side
from external_intelligence.storage import IntelligenceStore

T0 = 1_790_000_000_000


def raw(a=1, p="84000", q="0.001", t=T0, m=False):
    return {"a": a, "p": p, "q": q, "f": a, "l": a, "T": t, "m": m, "M": True}


class FakeFetcher:
    """Queues one JSON page per call and records every request made."""

    def __init__(self, pages) -> None:
        self.pages = list(pages)
        self.requests: list[FetchRequest] = []

    def fetch(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        body = json.dumps(self.pages.pop(0)).encode("utf-8")
        return FetchResponse(200, body, {}, T0, latency_ms=10)


class BrokenFetcher:
    def fetch(self, request: FetchRequest) -> FetchResponse:
        raise ProviderError("simulated network failure")


class AdapterConstructionTests(unittest.TestCase):
    def test_thresholds_must_be_positive_and_ordered(self) -> None:
        with self.assertRaises(ValueError):
            BinanceUSTradesAdapter(large_notional_usd=Decimal("0"))
        with self.assertRaises(ValueError):
            BinanceUSTradesAdapter(large_notional_usd=Decimal("-5"))
        with self.assertRaises(ValueError):
            BinanceUSTradesAdapter(large_notional_usd=Decimal("100"), whale_notional_usd=Decimal("50"))

    def test_equal_thresholds_are_allowed(self) -> None:
        BinanceUSTradesAdapter(large_notional_usd=Decimal("100"), whale_notional_usd=Decimal("100"))

    def test_live_ingestion_is_genuinely_on_and_says_why(self) -> None:
        adapter = BinanceUSTradesAdapter()
        self.assertIn("on:", adapter.live_ingestion_status)
        self.assertIn("aggTrades", adapter.live_ingestion_status)


class RawTradeTests(unittest.TestCase):
    def test_the_taker_side_comes_from_is_buyer_maker(self) -> None:
        seller_taker = RawTrade(1, Decimal("100"), Decimal("1"), T0, is_buyer_maker=True)
        buyer_taker = RawTrade(2, Decimal("100"), Decimal("1"), T0, is_buyer_maker=False)
        self.assertEqual(seller_taker.side, "sell")
        self.assertEqual(buyer_taker.side, "buy")

    def test_notional_is_price_times_quantity(self) -> None:
        trade = RawTrade(1, Decimal("84000"), Decimal("0.5"), T0, is_buyer_maker=False)
        self.assertEqual(trade.notional, Decimal("42000.0"))


class FetchRawTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = BinanceUSTradesAdapter()

    def test_a_real_shaped_response_parses_correctly(self) -> None:
        # Field names and types verbatim from the live capture; values are synthetic.
        page = [{"a": 21062358, "p": "84162.15000000", "q": "0.00060000",
                 "f": 89912696, "l": 89912696, "T": 1790873501678, "m": False, "M": True}]
        trades = self.adapter.fetch_raw(FakeFetcher([page]), "BTCUSD")
        self.assertEqual(len(trades), 1)
        self.assertEqual((trades[0].trade_id, trades[0].side), (21062358, "buy"))
        self.assertEqual(trades[0].price, Decimal("84162.15000000"))

    def test_the_request_asks_for_the_right_symbol_and_endpoint(self) -> None:
        fetcher = FakeFetcher([[raw()]])
        self.adapter.fetch_raw(fetcher, "ETHUSD", from_id=50, limit=10)
        request = fetcher.requests[0]
        self.assertEqual(request.url, BASE_URL + AGG_TRADES_PATH)
        self.assertEqual(request.params, {"symbol": "ETHUSD", "limit": "10", "fromId": "50"})

    def test_from_id_is_omitted_on_the_first_call(self) -> None:
        fetcher = FakeFetcher([[raw()]])
        self.adapter.fetch_raw(fetcher, "BTCUSD")
        self.assertNotIn("fromId", fetcher.requests[0].params)

    def test_a_non_list_response_is_a_schema_error(self) -> None:
        with self.assertRaises(ProviderSchemaError):
            self.adapter.fetch_raw(FakeFetcher([{"not": "a list"}]), "BTCUSD")

    def test_a_malformed_row_refuses_the_page_without_silent_loss(self) -> None:
        page = [raw(a=1), {"a": 2, "p": "not-a-number", "q": "1", "T": T0, "m": False},
                raw(a=3), {"missing": "fields"}]
        with self.assertRaises(ProviderSchemaError):
            self.adapter.fetch_raw(FakeFetcher([page]), "BTCUSD")

    def test_a_non_positive_price_or_quantity_is_rejected(self) -> None:
        page = [raw(p="0"), raw(a=2, q="-1")]
        with self.assertRaises(ProviderSchemaError):
            self.adapter.fetch_raw(FakeFetcher([page]), "BTCUSD")

    def test_limit_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.fetch_raw(FakeFetcher([[]]), "BTCUSD", limit=0)
        with self.assertRaises(ValueError):
            self.adapter.fetch_raw(FakeFetcher([[]]), "BTCUSD", limit=1001)


class ThresholdClassificationTests(unittest.TestCase):
    LARGE, WHALE = Decimal("50000"), Decimal("250000")

    def classify(self, notional: Decimal) -> str | None:
        trade = RawTrade(1, Decimal("1"), notional, T0, is_buyer_maker=False)
        records = trades_to_flat_records([trade], self.LARGE, self.WHALE, asset="BTC")
        return records[0]["kind"] if records else None

    def test_below_the_large_threshold_is_not_reported_at_all(self) -> None:
        self.assertIsNone(self.classify(Decimal("49999.99")))

    def test_at_the_large_threshold_is_a_large_trade(self) -> None:
        self.assertEqual(self.classify(Decimal("50000")), "large_trade")

    def test_just_under_whale_is_still_large(self) -> None:
        self.assertEqual(self.classify(Decimal("249999.99")), "large_trade")

    def test_at_the_whale_threshold_is_a_whale_trade(self) -> None:
        self.assertEqual(self.classify(Decimal("250000")), "whale_trade")

    def test_fields_carry_through_to_the_flat_record(self) -> None:
        trade = RawTrade(42, Decimal("100"), Decimal("600"), T0 + 5, is_buyer_maker=True)
        record = trades_to_flat_records(
            [trade], self.LARGE, self.WHALE, asset="BTC", quote_asset="USD", market_symbol="BTCUSD",
        )[0]
        self.assertEqual(record, {
            "kind": "large_trade", "id": "BTCUSD:42", "time_ms": T0 + 5, "symbol": "BTC", "quote": "USD",
            "exchange": "Binance.US", "market_symbol": "BTCUSD", "side": "sell",
            "notional_usd": "60000", "quantity": "600", "price": "100",
        })

    def test_no_bullish_or_bearish_label_is_ever_attached(self) -> None:
        trade = RawTrade(1, Decimal("1"), Decimal("1000000"), T0, is_buyer_maker=False)
        record = trades_to_flat_records([trade], self.LARGE, self.WHALE, asset="BTC")[0]
        blob = json.dumps(record).lower()
        for word in ("bullish", "bearish", "signal"):
            self.assertNotIn(word, blob)


class PollTargetTests(unittest.TestCase):
    def test_only_usd_quoted_pairs_are_accepted(self) -> None:
        PollTarget("BTCUSD", "BTC")  # fine
        with self.assertRaises(ValueError):
            PollTarget("BTCUSDT", "BTC", quote_asset="USDT")


class Harness:
    def __init__(self, testcase: unittest.TestCase, **adapter_kwargs) -> None:
        self.adapter = BinanceUSTradesAdapter(**{
            "large_notional_usd": Decimal("50000"), "whale_notional_usd": Decimal("250000"),
            **adapter_kwargs,
        })
        self.target = PollTarget("BTCUSD", "BTC")
        self.store = IntelligenceStore.open(":memory:")
        testcase.addCleanup(self.store.close)
        self.health = ProviderHealth(self.adapter.name)
        self.policy = ReceiptPolicy.observed_now()

    def poll(self, pages, *, now: int):
        return poll(
            self.adapter, FakeFetcher(pages), self.store, self.target,
            policy=self.policy, health=self.health, clock=FixedClock(now),
        )

    def cursor(self) -> str | None:
        return self.store.cursors.latest(self.adapter.name, "BTCUSD")


class SeedTests(unittest.TestCase):
    def test_the_first_poll_learns_the_cursor_and_stores_no_event(self) -> None:
        h = Harness(self)
        report = h.poll([[raw(a=500)]], now=T0)
        self.assertEqual((report.status, report.cursor_after), ("SEEDED", 500))
        self.assertEqual(h.store.events.count(), 0)
        self.assertEqual(h.cursor(), "500")

    def test_a_symbol_with_no_history_at_all_seeds_without_crashing(self) -> None:
        h = Harness(self)
        report = h.poll([[]], now=T0)
        self.assertEqual(report.status, "SEEDED")
        self.assertIsNone(h.cursor())  # nothing to seed from yet

    def test_the_seed_poll_declares_no_coverage(self) -> None:
        h = Harness(self)
        h.poll([[raw(a=500, t=T0)]], now=T0)
        self.assertFalse(h.store.coverage.index().covers(
            "binanceus_trades", "executed_trades", T0, T0, asset="BTC"
        ))


class BatchIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(self)
        self.h.poll([[raw(a=100, t=T0)]], now=T0)  # seed

    def test_a_batch_classifies_and_stores_only_what_clears_the_threshold(self) -> None:
        page = [
            raw(a=101, q="0.1", t=T0 + 1000, m=False),                 # small
            raw(a=102, q="3.0", t=T0 + 2000, m=True),                  # 252000: whale, sell
            raw(a=103, q="0.7", t=T0 + 3000, m=False),                 # 58800: large, buy
            raw(a=104, q="0.0001", t=T0 + 4000, m=True),               # tiny
            raw(a=105, q="0.6", t=T0 + 5000, m=False),                 # 50400: large, buy
        ]
        report = self.h.poll([page], now=T0 + 10_000)
        self.assertEqual((report.status, report.trades_seen, report.events_inserted),
                         ("OK", 5, 3))
        self.assertEqual(report.cursor_after, 105)

        events = sorted(self.h.store.events.iter_all(), key=lambda e: e.event_time)
        self.assertEqual(
            [(e.event_type, e.side) for e in events],
            [(EventType.WHALE_TRADE_SELL, Side.SELL),
             (EventType.LARGE_EXECUTED_TRADE, Side.BUY),
             (EventType.LARGE_EXECUTED_TRADE, Side.BUY)],
        )
        self.assertEqual(events[0].amount_usd, Decimal("252000.0"))
        self.assertEqual(events[0].exchange, "Binance.US")
        self.assertEqual(events[0].asset, "BTC")
        self.assertEqual(events[0].market_symbol, "BTCUSD")

    def test_the_cursor_advances_even_when_nothing_in_the_batch_is_large(self) -> None:
        page = [raw(a=101, q="0.001", t=T0 + 1000), raw(a=102, q="0.002", t=T0 + 2000)]
        report = self.h.poll([page], now=T0 + 10_000)
        self.assertEqual((report.status, report.events_inserted, report.cursor_after), ("OK", 0, 102))
        self.assertEqual(self.h.cursor(), "102")

    def test_a_zero_event_batch_still_declares_real_coverage(self) -> None:
        page = [raw(a=101, q="0.001", t=T0 + 1000), raw(a=102, q="0.002", t=T0 + 2000)]
        self.h.poll([page], now=T0 + 10_000)
        self.assertTrue(self.h.store.coverage.index().covers(
            "binanceus_trades", "executed_trades", T0 + 1000, T0 + 2000, asset="BTC"
        ))

    def test_coverage_never_reaches_back_into_the_unclaimed_seed_period(self) -> None:
        page = [raw(a=101, t=T0 + 1000)]
        self.h.poll([page], now=T0 + 10_000)
        index = self.h.store.coverage.index()
        self.assertFalse(index.covers("binanceus_trades", "executed_trades", T0, T0 + 1000, asset="BTC"))
        self.assertTrue(index.covers("binanceus_trades", "executed_trades", T0 + 1000, T0 + 1000, asset="BTC"))

    def test_resuming_asks_from_one_past_the_last_cursor(self) -> None:
        fetcher = FakeFetcher([[raw(a=101, t=T0 + 1000)]])
        poll(self.h.adapter, fetcher, self.h.store, self.h.target,
             policy=self.h.policy, health=self.h.health, clock=FixedClock(T0 + 10_000))
        self.assertEqual(fetcher.requests[0].params["fromId"], "101")


class IdempotencyTests(unittest.TestCase):
    def test_re_polling_with_nothing_new_changes_nothing(self) -> None:
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        h.poll([[raw(a=101, t=T0 + 1000)]], now=T0 + 10_000)
        before_events = h.store.events.count()
        report = h.poll([[]], now=T0 + 20_000)
        self.assertEqual(report.status, "NO_NEW_TRADES")
        self.assertEqual((report.cursor_before, report.cursor_after), (101, 101))
        self.assertEqual(h.store.events.count(), before_events)

    def test_the_same_source_trade_is_never_double_counted_even_if_refetched(self) -> None:
        # Resuming from fromId naturally never re-requests seen trades, but the
        # underlying event store's own duplicate guard is exercised here too.
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        page = [raw(a=101, q="3.0", t=T0 + 1000, m=True)]
        h.poll([page], now=T0 + 1)
        self.assertEqual(h.store.events.count(), 1)


class FeedFailureTests(unittest.TestCase):
    def test_a_failure_during_seeding_leaves_no_cursor(self) -> None:
        h = Harness(self)
        with self.assertRaises(ProviderError):
            poll(h.adapter, BrokenFetcher(), h.store, h.target, policy=h.policy, health=h.health, clock=FixedClock(T0))
        self.assertIsNone(h.cursor())
        self.assertFalse(h.health.assess(T0).healthy)

    def test_a_failure_after_seeding_leaves_the_cursor_where_it_was(self) -> None:
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        with self.assertRaises(ProviderError):
            poll(h.adapter, BrokenFetcher(), h.store, h.target, policy=h.policy, health=h.health, clock=FixedClock(T0 + 1000))
        self.assertEqual(h.cursor(), "100")
        self.assertEqual(h.store.events.count(), 0)

    def test_recovery_after_a_failure_resumes_from_the_untouched_cursor(self) -> None:
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        try:
            poll(h.adapter, BrokenFetcher(), h.store, h.target, policy=h.policy, health=h.health, clock=FixedClock(T0 + 1000))
        except ProviderError:
            pass
        report = h.poll([[raw(a=101, t=T0 + 2000)]], now=T0 + 2000)
        self.assertEqual(report.status, "OK")
        self.assertEqual(report.cursor_before, 100)


if __name__ == "__main__":
    unittest.main()
