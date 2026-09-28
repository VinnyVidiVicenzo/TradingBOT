"""The normalized event model: timestamps, receipt basis, classes and validation."""

from __future__ import annotations

import unittest
from decimal import Decimal

from external_intelligence.attribution import EntityType, normalize_entity_type
from external_intelligence.errors import EventValidationError
from external_intelligence.events import (
    EventClass,
    ExternalEvent,
    ReceiptBasis,
    Side,
    make_event_id,
    timestamp_problems,
)

from .ei_helpers import HOUR, MINUTE, T0, make_event


class TimestampOrderingTests(unittest.TestCase):
    def test_a_well_ordered_event_has_no_problems(self) -> None:
        event = make_event(1, event_time=T0, received=T0 + MINUTE, provider_publish_time=T0 + 10_000)
        self.assertEqual(timestamp_problems(event, ingest_time_ms=T0 + HOUR), [])

    def test_publish_before_the_event_is_flagged(self) -> None:
        event = make_event(1, event_time=T0, received=T0 + MINUTE, provider_publish_time=T0 - HOUR)
        self.assertIn("publish_before_event", timestamp_problems(event, ingest_time_ms=T0 + HOUR))

    def test_receipt_before_publish_is_flagged(self) -> None:
        event = make_event(1, event_time=T0, received=T0 + 20_000, provider_publish_time=T0 + HOUR)
        self.assertIn("received_before_publish", timestamp_problems(event, ingest_time_ms=T0 + 2 * HOUR))

    def test_receipt_before_the_event_is_flagged(self) -> None:
        event = make_event(1, event_time=T0, received=T0 - HOUR)
        self.assertIn("received_before_event", timestamp_problems(event, ingest_time_ms=T0 + HOUR))

    def test_small_clock_skew_is_tolerated_but_the_future_is_not(self) -> None:
        skewed = make_event(1, event_time=T0, received=T0 - 2_000)
        self.assertEqual(timestamp_problems(skewed, ingest_time_ms=T0 + HOUR, tolerance_ms=5_000), [])
        future = make_event(2, event_time=T0 + 10 * HOUR, received=T0 + 10 * HOUR)
        problems = timestamp_problems(future, ingest_time_ms=T0 + HOUR)
        self.assertIn("future_event_time", problems)
        self.assertIn("future_received_time", problems)

    def test_staleness_is_only_checked_when_a_limit_is_given(self) -> None:
        old = make_event(1, event_time=T0, received=T0 + MINUTE)
        self.assertEqual(timestamp_problems(old, ingest_time_ms=T0 + 30 * HOUR), [])
        self.assertIn(
            "stale_at_ingest",
            timestamp_problems(old, ingest_time_ms=T0 + 30 * HOUR, max_staleness_ms=HOUR),
        )

    def test_the_three_timestamps_stay_separate(self) -> None:
        event = make_event(1, event_time=T0, received=T0 + 5 * MINUTE, provider_publish_time=T0 + MINUTE)
        self.assertEqual(
            (event.event_time, event.provider_publish_time, event.system_received_time),
            (T0, T0 + MINUTE, T0 + 5 * MINUTE),
        )


class ReceiptBasisTests(unittest.TestCase):
    def test_an_observed_event_is_usable_from_its_receipt_time_only(self) -> None:
        event = make_event(1, event_time=T0, received=T0 + HOUR)
        self.assertFalse(event.is_available_at(T0 + HOUR - 1))
        self.assertTrue(event.is_available_at(T0 + HOUR))

    def test_event_time_alone_never_makes_an_event_usable(self) -> None:
        event = make_event(1, event_time=T0, received=T0 + HOUR)
        self.assertFalse(event.is_available_at(T0 + 30 * MINUTE))

    def test_an_estimate_needs_the_caller_to_allow_estimates(self) -> None:
        event = make_event(1, event_time=T0, estimated=(T0 + MINUTE, MINUTE, "test"))
        self.assertFalse(event.is_available_at(T0 + HOUR))
        self.assertTrue(event.is_available_at(T0 + HOUR, allow_estimated=True))
        self.assertFalse(event.is_available_at(T0, allow_estimated=True))

    def test_an_event_with_no_receipt_time_is_never_usable(self) -> None:
        event = make_event(1, event_time=T0)
        self.assertIsNone(event.available_time)
        self.assertFalse(event.is_available_at(T0 + 100 * HOUR, allow_estimated=True))

    def test_the_basis_must_match_the_times_present(self) -> None:
        with self.assertRaises(EventValidationError):
            make_event(1, received=T0, received_time_basis=ReceiptBasis.UNKNOWN)
        with self.assertRaises(EventValidationError):  # an estimate must state its reason
            make_event(2, estimated=(T0 + 1, 1, "  "))
        with self.assertRaises(EventValidationError):  # an estimate cannot precede the event
            make_event(3, event_time=T0, estimated=(T0 - 1, 0, "why"))


class EventClassAndSideTests(unittest.TestCase):
    def test_transfers_and_executed_trades_are_different_classes(self) -> None:
        self.assertIs(make_event(1, event_type="EXCHANGE_INFLOW").event_class, EventClass.ON_CHAIN_TRANSFER)
        trade = make_event(2, provider="coinlobster", event_type="WHALE_TRADE_BUY", side=Side.BUY)
        self.assertIs(trade.event_class, EventClass.EXECUTED_TRADE)
        self.assertIsNot(trade.event_class, EventClass.ON_CHAIN_TRANSFER)

    def test_a_transfer_can_never_carry_a_side(self) -> None:
        for kind in ("EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW", "LARGE_TRANSFER", "STABLECOIN_MINT"):
            with self.assertRaises(EventValidationError, msg=kind):
                make_event(1, event_type=kind, side=Side.SELL)

    def test_an_executed_buy_or_sell_must_say_which(self) -> None:
        with self.assertRaises(EventValidationError):
            make_event(1, event_type="WHALE_TRADE_BUY")
        with self.assertRaises(EventValidationError):
            make_event(2, event_type="WHALE_TRADE_SELL", side=Side.BUY)
        self.assertIsNone(make_event(3, event_type="LARGE_EXECUTED_TRADE").side)

    def test_funding_needs_a_value_and_open_interest_needs_an_amount(self) -> None:
        with self.assertRaises(EventValidationError):
            make_event(1, event_type="FUNDING_UPDATE")
        with self.assertRaises(EventValidationError):
            make_event(2, event_type="OPEN_INTEREST_UPDATE", usd=None)
        make_event(3, event_type="FUNDING_UPDATE", value=Decimal("-0.0001"), value_unit="x")

    def test_there_is_no_directional_label_on_an_event(self) -> None:
        names = {f for f in ExternalEvent.__dataclass_fields__}
        for forbidden in ("bullish", "bearish", "signal", "score", "sentiment", "direction", "confidence"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)


class ValidationTests(unittest.TestCase):
    def test_amounts_must_be_finite_and_not_negative(self) -> None:
        with self.assertRaises(EventValidationError):
            make_event(1, usd="-5")
        with self.assertRaises(EventValidationError):
            make_event(2, usd="NaN")

    def test_a_missing_amount_stays_missing(self) -> None:
        event = make_event(1, usd=None, received=T0)
        self.assertIsNone(event.amount_usd)
        self.assertIsNone(event.amount_asset)

    def test_usd_and_a_stablecoin_remain_distinct_assets(self) -> None:
        usdt = make_event(1, asset="USDT", event_type="STABLECOIN_MINT")
        usd = make_event(2, asset="USD", event_type="LARGE_TRANSFER")
        self.assertNotEqual(usdt.asset, usd.asset)
        self.assertEqual(usdt.asset, "USDT")

    def test_ids_are_stable_and_provider_specific(self) -> None:
        self.assertEqual(make_event_id("a", "1"), make_event_id("a", "1"))
        self.assertNotEqual(make_event_id("a", "1"), make_event_id("b", "1"))

    def test_round_trip_through_a_dict(self) -> None:
        event = make_event(
            1, received=T0 + 1, source_entity_type=EntityType.UNKNOWN,
            destination_entity_type=EntityType.EXCHANGE, metadata={"k": "v"},
        )
        self.assertEqual(ExternalEvent.from_dict(event.to_dict()), event)

    def test_entity_types_map_by_exact_token_only(self) -> None:
        self.assertIs(normalize_entity_type("Exchange"), EntityType.EXCHANGE)
        self.assertIs(normalize_entity_type("market maker"), EntityType.MARKET_MAKER)
        self.assertIs(normalize_entity_type("unknown wallet"), EntityType.UNKNOWN)
        self.assertIs(normalize_entity_type(None), EntityType.UNKNOWN)
        # A label that merely contains a known word is not that thing.
        self.assertIs(normalize_entity_type("exchange_wallet_of_someone"), EntityType.OTHER)
        self.assertIs(normalize_entity_type("binance"), EntityType.OTHER)


if __name__ == "__main__":
    unittest.main()
