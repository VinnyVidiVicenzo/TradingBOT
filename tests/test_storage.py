"""Local store round-trips and the exchangeInfo audit trail."""

from __future__ import annotations

import unittest

from crypto_research.binanceus.parsing import parse_exchange_info
from crypto_research.datafeed.loader import load_dataset
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError
from crypto_research.storage.database import SCHEMA_VERSION, open_database
from crypto_research.storage.repositories import (
    BarRepository,
    QuoteRepository,
    RawSnapshotRepository,
    RuleSnapshotRepository,
)
from crypto_research.timeutil import DAY_MS

from .helpers import (
    BTCUSD_EXCHANGE_INFO_ENTRY,
    EPOCH_2024,
    make_bars,
    make_quote,
    make_rule_entry,
)


class SchemaTests(unittest.TestCase):
    def test_schema_version_is_recorded(self) -> None:
        with open_database(":memory:") as db:
            row = db.connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            self.assertEqual(row["value"], str(SCHEMA_VERSION))


class BarStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = open_database(":memory:")
        self.repo = BarRepository(self.db)
        self.bars = make_bars("BTCUSD", ["100.12345678", "101", "102"])

    def tearDown(self) -> None:
        self.db.close()

    def test_round_trip_preserves_exact_decimals(self) -> None:
        self.repo.upsert_many(self.bars)
        loaded = self.repo.load("BTCUSD", "1d")
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded[0].close, dec("100.12345678"))
        self.assertEqual(loaded[0].open_time, self.bars[0].open_time)
        self.assertEqual(loaded[0].quote_volume, dec("1000000"))

    def test_upsert_is_idempotent(self) -> None:
        self.repo.upsert_many(self.bars)
        self.repo.upsert_many(self.bars)
        self.assertEqual(self.repo.coverage("BTCUSD", "1d")[0], 3)

    def test_upsert_replaces_revised_history(self) -> None:
        self.repo.upsert_many(self.bars)
        revised = make_bars("BTCUSD", ["999", "101", "102"])
        self.repo.upsert_many(revised)
        self.assertEqual(self.repo.load("BTCUSD", "1d")[0].close, dec("999"))
        self.assertEqual(self.repo.coverage("BTCUSD", "1d")[0], 3)

    def test_coverage_reports_the_stored_range(self) -> None:
        self.repo.upsert_many(self.bars)
        count, lo, hi = self.repo.coverage("BTCUSD", "1d")
        self.assertEqual(count, 3)
        self.assertEqual(lo, EPOCH_2024)
        self.assertEqual(hi, EPOCH_2024 + 2 * DAY_MS)

    def test_coverage_of_an_empty_table(self) -> None:
        self.assertEqual(self.repo.coverage("BTCUSD", "1d"), (0, 0, 0))

    def test_range_filtering(self) -> None:
        self.repo.upsert_many(self.bars)
        loaded = self.repo.load(
            "BTCUSD", "1d",
            start_time=EPOCH_2024 + DAY_MS, end_time=EPOCH_2024 + DAY_MS,
        )
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].close, dec("101"))

    def test_intervals_do_not_collide(self) -> None:
        self.repo.upsert_many(self.bars)
        self.repo.upsert_many(make_bars("BTCUSD", ["7"], interval="1h", width_ms=3_600_000))
        self.assertEqual(self.repo.coverage("BTCUSD", "1d")[0], 3)
        self.assertEqual(self.repo.coverage("BTCUSD", "1h")[0], 1)
        self.assertEqual(self.repo.symbols(), [("BTCUSD", "1d"), ("BTCUSD", "1h")])


class QuoteStorageTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with open_database(":memory:") as db:
            repo = QuoteRepository(db)
            repo.insert_many([
                make_quote("BTCUSD", "99.9", "100.1", received_at=1_000),
                make_quote("BTCUSD", "99.8", "100.2", received_at=2_000),
            ])
            loaded = repo.load("BTCUSD")
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].bid, dec("99.9"))
            self.assertEqual(loaded[1].source_received_at, 2_000)


class RuleSnapshotStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = open_database(":memory:")
        self.repo = RuleSnapshotRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _snapshots(self, received_at: int, min_notional: str = "1"):
        payload = {
            "serverTime": received_at,
            "symbols": [
                BTCUSD_EXCHANGE_INFO_ENTRY,
                make_rule_entry("ETHUSD", base="ETH", quote="USD",
                                min_notional=min_notional),
            ],
        }
        return parse_exchange_info(payload, received_at=received_at)

    def test_round_trip_including_order_count_filters(self) -> None:
        self.repo.upsert_many(self._snapshots(1_000))
        latest = self.repo.latest()
        btc = next(s for s in latest if s.symbol == "BTCUSD")
        self.assertEqual(btc.price_tick, dec("0.01"))
        self.assertEqual(btc.market_quantity_step, dec("0"))
        self.assertEqual(btc.max_num_orders, 200)
        self.assertEqual(btc.max_num_algo_orders, 5)
        self.assertEqual(btc.raw_payload["symbol"], "BTCUSD")

    def test_rule_history_is_preserved_when_a_rule_changes(self) -> None:
        self.repo.upsert_many(self._snapshots(1_000, min_notional="1"))
        self.repo.upsert_many(self._snapshots(2_000, min_notional="10"))
        history = self.repo.history("ETHUSD")
        self.assertEqual([s.min_notional for s in history], [dec("1"), dec("10")])

    def test_latest_returns_the_newest_row_per_symbol(self) -> None:
        self.repo.upsert_many(self._snapshots(1_000, min_notional="1"))
        self.repo.upsert_many(self._snapshots(2_000, min_notional="10"))
        latest = {s.symbol: s for s in self.repo.latest()}
        self.assertEqual(latest["ETHUSD"].min_notional, dec("10"))

    def test_latest_can_be_asked_as_of_an_earlier_time(self) -> None:
        self.repo.upsert_many(self._snapshots(1_000, min_notional="1"))
        self.repo.upsert_many(self._snapshots(2_000, min_notional="10"))
        latest = {s.symbol: s for s in self.repo.latest(at_or_before=1_500)}
        self.assertEqual(latest["ETHUSD"].min_notional, dec("1"))


class RawSnapshotAuditTests(unittest.TestCase):
    def test_every_fetch_is_kept_and_hashed(self) -> None:
        with open_database(":memory:") as db:
            repo = RawSnapshotRepository(db)
            first = {"serverTime": 1, "symbols": [BTCUSD_EXCHANGE_INFO_ENTRY]}
            changed = {
                "serverTime": 2,
                "symbols": [
                    make_rule_entry("BTCUSD", base="BTC", quote="USD", min_notional="25")
                ],
            }
            hash_a = repo.record(first, fetched_at=1_000, server_time=1)
            hash_b = repo.record(first, fetched_at=2_000, server_time=1)
            hash_c = repo.record(changed, fetched_at=3_000, server_time=2)

            self.assertEqual(hash_a, hash_b, "identical payloads hash identically")
            self.assertNotEqual(hash_a, hash_c)
            self.assertEqual(len(repo.list_snapshots()), 3, "nothing is overwritten")
            self.assertEqual(
                [h for h, _ in repo.distinct_hashes()], [hash_a, hash_c]
            )

    def test_a_stored_payload_comes_back_byte_identical_in_content(self) -> None:
        with open_database(":memory:") as db:
            repo = RawSnapshotRepository(db)
            payload = {"serverTime": 7, "symbols": [BTCUSD_EXCHANGE_INFO_ENTRY]}
            repo.record(payload, fetched_at=1_000, server_time=7)
            self.assertEqual(repo.load(1_000), payload)


class DatasetLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = open_database(":memory:")
        BarRepository(self.db).upsert_many(make_bars("BTCUSD", ["100", "101", "102"]))

    def tearDown(self) -> None:
        self.db.close()

    def test_loads_and_hashes_a_dataset(self) -> None:
        dataset = load_dataset(
            self.db, ["BTCUSD"], "1d",
            start_time=EPOCH_2024, end_time=EPOCH_2024 + 5 * DAY_MS,
        )
        self.assertEqual(dataset.symbols, ("BTCUSD",))
        self.assertEqual(dataset.version.bar_count, 3)
        self.assertEqual(dataset.version.interval, "1d")
        self.assertEqual(len(dataset.version.sha256), 64)

    def test_a_missing_symbol_is_an_error_not_a_silent_reduction(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_dataset(
                self.db, ["BTCUSD", "ETHUSD"], "1d",
                start_time=EPOCH_2024, end_time=EPOCH_2024 + 5 * DAY_MS,
            )
        self.assertIn("ETHUSD", str(ctx.exception))

    def test_a_missing_symbol_can_be_tolerated_explicitly(self) -> None:
        dataset = load_dataset(
            self.db, ["BTCUSD", "ETHUSD"], "1d",
            start_time=EPOCH_2024, end_time=EPOCH_2024 + 5 * DAY_MS,
            require_all_symbols=False,
        )
        self.assertEqual(dataset.symbols, ("BTCUSD",))

    def test_an_empty_range_is_an_error(self) -> None:
        with self.assertRaises(ConfigError):
            load_dataset(
                self.db, ["BTCUSD"], "1d",
                start_time=EPOCH_2024 + 500 * DAY_MS,
                end_time=EPOCH_2024 + 600 * DAY_MS,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
