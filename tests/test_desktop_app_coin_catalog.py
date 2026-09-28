"""desktop_app.coin_catalog -- dynamic coin discovery, against a throwaway DB."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crypto_research.decimals import dec
from crypto_research.models import EXCHANGE_BINANCE_US, MarketBar
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import DAY_MS, parse_time

from desktop_app import coin_catalog

START = parse_time("2024-01-01T00:00:00Z")


def _bar(symbol: str, open_time: int) -> MarketBar:
    price = dec("100")
    return MarketBar(
        exchange=EXCHANGE_BINANCE_US,
        symbol=symbol,
        interval="1d",
        open_time=open_time,
        close_time=open_time + DAY_MS - 1,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=dec("1"),
        source_received_at=open_time + DAY_MS - 1,
    )


def _option(base: str, first: int, last: int) -> coin_catalog.CoinOption:
    return coin_catalog.CoinOption(base, base, f"{base}USDT", 10, first, last)


class DiscoverAvailableCoinsTests(unittest.TestCase):
    def _discover(self, *symbols: str) -> list[coin_catalog.CoinOption]:
        db = open_database(":memory:")
        try:
            bars = BarRepository(db)
            for symbol in symbols:
                bars.upsert_many(_bar(symbol, START + i * DAY_MS) for i in range(5))
            return coin_catalog.discover_available_coins_from_db(db)
        finally:
            db.close()

    def test_only_symbols_with_stored_bars_are_offered(self) -> None:
        symbols = {option.symbol for option in self._discover("BTCUSDT")}
        self.assertIn("BTCUSDT", symbols)
        self.assertNotIn("ETHUSDT", symbols)

    def test_an_empty_database_offers_no_coins(self) -> None:
        self.assertEqual(self._discover(), [])

    def test_only_the_curated_friendly_names_are_ever_offered(self) -> None:
        self.assertEqual(self._discover("SHIBUSDT"), [])

    def test_a_dollar_quoted_pair_is_not_mistaken_for_the_usdt_one(self) -> None:
        # USD and USDT are different assets everywhere in this project.
        self.assertEqual(self._discover("BTCUSD"), [])

    def test_the_reported_range_is_the_stored_range(self) -> None:
        (option,) = self._discover("BTCUSDT")
        self.assertEqual(option.first_open_ms, START)
        self.assertEqual(option.last_open_ms, START + 4 * DAY_MS)
        self.assertEqual(option.bar_count, 5)


class LookingLeavesNothingBehindTests(unittest.TestCase):
    def test_a_missing_database_is_reported_as_no_coins_and_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fresh_clone" / "data" / "market.sqlite"
            self.assertEqual(coin_catalog.discover_available_coins(str(target)), [])
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())

    def test_an_existing_database_is_still_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.sqlite"
            db = open_database(str(path))
            try:
                BarRepository(db).upsert_many(_bar("BTCUSDT", START + i * DAY_MS) for i in range(3))
            finally:
                db.close()
            symbols = [o.symbol for o in coin_catalog.discover_available_coins(str(path))]
            self.assertEqual(symbols, ["BTCUSDT"])


class OverlappingRangeTests(unittest.TestCase):
    def test_overlap_is_the_intersection_of_available_history(self) -> None:
        early = _option("BTC", START, START + 9 * DAY_MS)
        late = _option("ETH", START + 3 * DAY_MS, START + 12 * DAY_MS)
        self.assertEqual(
            coin_catalog.overlapping_range([early, late]), (START + 3 * DAY_MS, START + 9 * DAY_MS)
        )

    def test_a_single_coin_overlaps_with_itself(self) -> None:
        only = _option("BTC", START, START + 9 * DAY_MS)
        self.assertEqual(coin_catalog.overlapping_range([only]), (START, START + 9 * DAY_MS))

    def test_no_overlap_returns_none(self) -> None:
        first = _option("BTC", START, START + 5 * DAY_MS)
        second = _option("ETH", START + 20 * DAY_MS, START + 30 * DAY_MS)
        self.assertIsNone(coin_catalog.overlapping_range([first, second]))

    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(coin_catalog.overlapping_range([]))


if __name__ == "__main__":
    unittest.main()
