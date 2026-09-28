"""The external-intelligence store: point-in-time reads, duplicates, append-only history."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from crypto_research.errors import ConfigError

from external_intelligence.attribution import EntityType, build_attribution
from external_intelligence.base.coverage import CoverageIndex, CoverageInterval
from external_intelligence.events import EventClass, EventType
from external_intelligence.storage import IntelligenceStore, open_intelligence_database

from .ei_helpers import HOUR, MINUTE, T0, make_event

PACKAGE = Path(__file__).parent.parent / "external_intelligence"


class PointInTimeReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = IntelligenceStore.open(":memory:")
        self.addCleanup(self.store.close)

    def ids(self, **kwargs: object) -> list[str]:
        return sorted(e.provider_event_id for e in self.store.events.load_available(**kwargs))  # type: ignore[arg-type]

    def test_an_event_is_returned_only_from_its_receipt_time(self) -> None:
        self.store.events.insert(make_event(1, event_time=T0, received=T0 + HOUR), inserted_at=0)
        self.assertEqual(self.ids(as_of=T0 + HOUR - 1), [])
        self.assertEqual(self.ids(as_of=T0 + HOUR), ["1"])

    def test_event_time_before_the_cutoff_is_not_enough(self) -> None:
        # It happened long ago, but our system only got it later: not usable yet.
        self.store.events.insert(make_event(1, event_time=T0, received=T0 + 5 * HOUR), inserted_at=0)
        self.assertEqual(self.ids(as_of=T0 + 4 * HOUR), [])

    def test_estimates_are_returned_only_when_allowed(self) -> None:
        self.store.events.insert(
            make_event(1, event_time=T0, estimated=(T0 + MINUTE, MINUTE, "assumed")), inserted_at=0
        )
        self.assertEqual(self.ids(as_of=T0 + HOUR), [])
        self.assertEqual(self.ids(as_of=T0 + HOUR, allow_estimated=True), ["1"])

    def test_an_event_with_no_receipt_time_is_stored_but_never_returned(self) -> None:
        self.store.events.insert(make_event(1, event_time=T0), inserted_at=0)
        self.assertEqual(self.store.events.count(), 1)
        self.assertEqual(self.ids(as_of=T0 + 1000 * HOUR, allow_estimated=True), [])

    def test_filters_by_asset_type_provider_and_event_window(self) -> None:
        self.store.events.insert(make_event(1, asset="BTC", received=T0), inserted_at=0)
        self.store.events.insert(make_event(2, asset="ETH", received=T0), inserted_at=0)
        self.store.events.insert(make_event(3, event_type="EXCHANGE_OUTFLOW", received=T0), inserted_at=0)
        self.store.events.insert(make_event(4, provider="other", received=T0), inserted_at=0)
        self.store.events.insert(make_event(5, event_time=T0 - 10 * HOUR, received=T0), inserted_at=0)
        got = self.ids(
            as_of=T0 + HOUR, assets=["BTC"], event_types=[EventType.EXCHANGE_INFLOW],
            providers=["whale_alert"], min_event_time=T0 - HOUR,
        )
        self.assertEqual(got, ["1"])
        classes = self.store.events.load_available(as_of=T0, event_classes=[EventClass.ON_CHAIN_TRANSFER])
        self.assertEqual(len(classes), 5)


class DuplicateAndAppendOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = IntelligenceStore.open(":memory:")
        self.addCleanup(self.store.close)

    def test_a_repeated_source_event_is_detected_and_stored_once(self) -> None:
        event = make_event(1, received=T0)
        self.assertTrue(self.store.events.insert(event, inserted_at=1))
        self.assertFalse(self.store.events.insert(event, inserted_at=2))
        self.assertEqual(self.store.events.count(), 1)

    def test_the_same_provider_id_from_two_providers_is_two_events(self) -> None:
        self.assertTrue(self.store.events.insert(make_event(1, provider="a", received=T0), inserted_at=1))
        self.assertTrue(self.store.events.insert(make_event(1, provider="b", received=T0), inserted_at=1))
        self.assertEqual(self.store.events.count(), 2)

    def test_the_package_never_updates_or_deletes_rows(self) -> None:
        pattern = re.compile(
            r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bTRUNCATE\s+TABLE\b|\bALTER\s+TABLE\b",
            re.IGNORECASE,
        )
        for path in sorted(PACKAGE.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), f"{path.relative_to(PACKAGE)} rewrites or removes rows")

    def test_the_append_only_check_can_actually_fail(self) -> None:
        pattern = re.compile(
            r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bTRUNCATE\s+TABLE\b|\bALTER\s+TABLE\b",
            re.IGNORECASE,
        )
        for sql in ("UPDATE external_events SET x = 1", "delete from external_events", "DROP TABLE quarantine"):
            self.assertIsNotNone(pattern.search(sql), sql)

    def test_the_store_refuses_to_be_the_market_store(self) -> None:
        with self.assertRaises(ConfigError):
            open_intelligence_database("data/market.sqlite")

    def test_raw_payloads_are_kept_and_addressable_by_their_hash(self) -> None:
        payload_id = self.store.raw.record(
            provider="p", source_kind="file", source_ref="x.json", body='{"a": 1}', received_at=5
        )
        self.assertEqual(self.store.raw.load(payload_id), '{"a": 1}')
        again = self.store.raw.record(
            provider="p", source_kind="file", source_ref="y.json", body='{"a": 1}', received_at=6
        )
        self.assertEqual(again, payload_id)
        self.assertEqual(self.store.raw.count(), 1)

    def test_quarantine_keeps_the_reason_and_the_record(self) -> None:
        self.store.quarantine.add(
            provider="p", reason="future_event_time", detail="d", raw_payload_reference="raw:1#0",
            recorded_at=1, record={"a": 1},
        )
        self.assertEqual(self.store.quarantine.reasons(provider="p"), {"future_event_time": 1})

    def test_health_snapshots_round_trip(self) -> None:
        self.store.health.record({"provider": "p", "healthy": True}, recorded_at=1)
        self.store.health.record({"provider": "p", "healthy": False}, recorded_at=2)
        self.assertFalse(self.store.health.latest("p")["healthy"])
        self.assertEqual(len(self.store.health.history("p")), 2)


class AttributionHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = IntelligenceStore.open(":memory:")
        self.addCleanup(self.store.close)

    def attribution(self, label: str, kind: str | None, at: int):
        return build_attribution(
            provider="whale_alert", address="0xabc", raw_label=label, raw_entity_type=kind,
            attributed_at=at, time_basis="observed", blockchain="ethereum",
        )

    def test_a_repeated_claim_adds_nothing_and_a_changed_one_is_kept(self) -> None:
        self.assertTrue(self.store.attributions.record(self.attribution("x", "exchange", 1), recorded_at=1))
        self.assertFalse(self.store.attributions.record(self.attribution("x", "exchange", 2), recorded_at=2))
        self.assertFalse(self.store.attributions.changed_later("whale_alert", "0xabc", "ethereum"))
        self.assertTrue(self.store.attributions.record(self.attribution("y", "fund", 3), recorded_at=3))
        self.assertTrue(self.store.attributions.changed_later("whale_alert", "0xabc", "ethereum"))
        history = self.store.attributions.history("whale_alert", "0xabc", "ethereum")
        self.assertEqual([h.raw_label for h in history], ["x", "y"])

    def test_no_confidence_is_invented(self) -> None:
        recorded = self.attribution("x", "exchange", 1)
        self.assertIsNone(recorded.provider_confidence)
        supplied = build_attribution(
            provider="p", address="a", raw_label="l", raw_entity_type=None, attributed_at=1,
            time_basis="observed", provider_confidence="0.8",
        )
        self.assertEqual(supplied.provider_confidence, "0.8")  # exactly as the provider sent it

    def test_unknown_attribution_records_unknown_not_a_guess(self) -> None:
        recorded = build_attribution(
            provider="p", address="a", raw_label=None, raw_entity_type="unknown", attributed_at=1,
            time_basis="observed",
        )
        self.assertIs(recorded.entity_type, EntityType.UNKNOWN)
        self.assertIsNone(build_attribution(
            provider="p", address="a", raw_label=None, raw_entity_type=None, attributed_at=1,
            time_basis="observed",
        ))


class CoverageTests(unittest.TestCase):
    def test_coverage_needs_the_whole_window(self) -> None:
        index = CoverageIndex([CoverageInterval("p", "transfers", T0, T0 + 10 * HOUR, "declared_import_window")])
        self.assertTrue(index.covers("p", "transfers", T0 + HOUR, T0 + 2 * HOUR))
        self.assertFalse(index.covers("p", "transfers", T0 - 1, T0 + HOUR))
        self.assertFalse(index.covers("p", "executed_trades", T0, T0 + HOUR))
        self.assertFalse(index.covers("q", "transfers", T0, T0 + HOUR))

    def test_adjacent_intervals_are_continuous_but_a_gap_is_not(self) -> None:
        index = CoverageIndex([
            CoverageInterval("p", "transfers", T0, T0 + HOUR, "declared_import_window"),
            CoverageInterval("p", "transfers", T0 + HOUR + 1, T0 + 2 * HOUR, "declared_import_window"),
            CoverageInterval("p", "transfers", T0 + 5 * HOUR, T0 + 6 * HOUR, "declared_import_window"),
        ])
        self.assertTrue(index.covers("p", "transfers", T0, T0 + 2 * HOUR))
        self.assertFalse(index.covers("p", "transfers", T0, T0 + 6 * HOUR))

    def test_coverage_resting_on_estimated_receipts_counts_only_when_estimates_are_allowed(self) -> None:
        index = CoverageIndex([CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", receipts="estimated")])
        self.assertFalse(index.covers("p", "transfers", T0, T0 + HOUR))
        self.assertTrue(index.covers("p", "transfers", T0, T0 + HOUR, allow_estimated=True))

    def test_coverage_known_only_from_the_import_moment_does_not_reach_back_in_time(self) -> None:
        index = CoverageIndex([CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", known_from=T0 + 5 * HOUR)])
        self.assertFalse(index.covers("p", "transfers", T0, T0 + HOUR, as_of=T0 + 2 * HOUR))
        self.assertTrue(index.covers("p", "transfers", T0, T0 + HOUR, as_of=T0 + 5 * HOUR))
        self.assertTrue(index.covers("p", "transfers", T0, T0 + HOUR))  # no decision time given: not judged

    def test_coverage_on_events_with_no_receipt_time_never_counts(self) -> None:
        index = CoverageIndex([CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", receipts="none")])
        self.assertFalse(index.covers("p", "transfers", T0, T0 + HOUR, allow_estimated=True, as_of=T0 + 9 * HOUR))

    def test_an_unusable_interval_cannot_bridge_a_gap(self) -> None:
        index = CoverageIndex([
            CoverageInterval("p", "transfers", T0, T0 + HOUR, "d"),
            CoverageInterval("p", "transfers", T0 + HOUR + 1, T0 + 2 * HOUR, "d", receipts="estimated"),
        ])
        self.assertFalse(index.covers("p", "transfers", T0, T0 + 2 * HOUR))
        self.assertTrue(index.covers("p", "transfers", T0, T0 + 2 * HOUR, allow_estimated=True))

    def test_the_receipt_dependence_survives_a_round_trip_through_the_store(self) -> None:
        with IntelligenceStore.open(":memory:") as store:
            store.coverage.record(
                CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", None, "estimated", T0 + 3 * HOUR), recorded_at=1
            )
            (interval,) = store.coverage.index().intervals("p", "transfers")
            self.assertEqual((interval.receipts, interval.known_from), ("estimated", T0 + 3 * HOUR))
        with self.assertRaises(ValueError):
            CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", receipts="guessed")

    def test_coverage_declared_for_some_assets_says_nothing_about_the_others(self) -> None:
        index = CoverageIndex([CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", assets=("btc", " eth "))])
        self.assertTrue(index.covers("p", "transfers", T0, T0 + HOUR, asset="BTC"))
        self.assertTrue(index.covers("p", "transfers", T0, T0 + HOUR, asset="eth"))
        self.assertFalse(index.covers("p", "transfers", T0, T0 + HOUR, asset="SOL"))
        self.assertFalse(index.covers("p", "transfers", T0, T0 + HOUR))  # a market-wide feature needs all assets
        everything = CoverageIndex([CoverageInterval("p", "transfers", T0, T0 + HOUR, "d")])
        self.assertTrue(everything.covers("p", "transfers", T0, T0 + HOUR, asset="SOL"))
        self.assertTrue(everything.covers("p", "transfers", T0, T0 + HOUR))
        with self.assertRaises(ValueError):
            CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", assets=("  ",))

    def test_the_asset_scope_survives_a_round_trip_through_the_store(self) -> None:
        with IntelligenceStore.open(":memory:") as store:
            store.coverage.record(
                CoverageInterval("p", "transfers", T0, T0 + HOUR, "d", assets=("ETH", "BTC")), recorded_at=1
            )
            store.coverage.record(CoverageInterval("p", "executed_trades", T0, T0 + HOUR, "d"), recorded_at=1)
            index = store.coverage.index()
            self.assertEqual(index.intervals("p", "transfers")[0].assets, ("BTC", "ETH"))
            self.assertIsNone(index.intervals("p", "executed_trades")[0].assets)

    def test_coverage_round_trips_through_the_store(self) -> None:
        with IntelligenceStore.open(":memory:") as store:
            store.coverage.record(
                CoverageInterval("p", "transfers", T0, T0 + HOUR, "declared_import_window"), recorded_at=1
            )
            self.assertTrue(store.coverage.index().covers("p", "transfers", T0, T0 + HOUR))


if __name__ == "__main__":
    unittest.main()
