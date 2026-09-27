"""Optional live checks against api.binance.us.

Skipped unless CRYPTO_RESEARCH_LIVE_TESTS=1, so the default test run stays
offline, fast and deterministic. These exist to catch the case where
Binance.US changes a response shape under us -- which the unit tests, working
from recorded fixtures, cannot notice.

Read-only. Public endpoints only. No credentials.

    CRYPTO_RESEARCH_LIVE_TESTS=1 python -m unittest tests.test_live_binanceus
"""

from __future__ import annotations

import os
import unittest

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.decimals import ZERO
from crypto_research.timeutil import DAY_MS, now_ms

LIVE = os.environ.get("CRYPTO_RESEARCH_LIVE_TESTS") == "1"


@unittest.skipUnless(LIVE, "set CRYPTO_RESEARCH_LIVE_TESTS=1 to run live checks")
class LiveBinanceUsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = BinanceUSPublicClient()

    def test_ping(self) -> None:
        self.assertTrue(self.client.ping())

    def test_server_time_is_close_to_local_time(self) -> None:
        skew = abs(self.client.clock_skew_ms())
        self.assertLess(skew, 60_000, f"clock skew of {skew}ms is too large to trust")

    def test_exchange_info_still_supplies_the_filters_we_parse(self) -> None:
        result = self.client.exchange_info(["BTCUSD"])
        rules = result.rule_book().get("BTCUSD")
        snapshot = rules.snapshot
        self.assertEqual(snapshot.quote_asset, "USD")
        self.assertIsNotNone(snapshot.price_tick, "PRICE_FILTER tickSize disappeared")
        self.assertIsNotNone(snapshot.quantity_step, "LOT_SIZE stepSize disappeared")
        self.assertIsNotNone(
            snapshot.min_notional,
            "neither MIN_NOTIONAL nor NOTIONAL supplied a minimum",
        )
        self.assertGreater(snapshot.price_tick, ZERO)
        self.assertGreater(snapshot.quantity_step, ZERO)

    def test_candidate_symbols_are_listed_and_trading(self) -> None:
        symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "USDTUSD"]
        result = self.client.exchange_info(symbols)
        book = result.rule_book()
        for symbol in symbols:
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, book, f"{symbol} is not listed on Binance.US")

    def test_klines_have_the_expected_layout(self) -> None:
        bars = self.client.klines("BTCUSD", "1d", limit=3)
        self.assertEqual(len(bars), 3)
        for bar in bars:
            self.assertEqual(bar.symbol, "BTCUSD")
            self.assertEqual(bar.interval, "1d")
            self.assertGreater(bar.close, ZERO)
            self.assertGreaterEqual(bar.high, bar.low)
            self.assertGreater(bar.close_time, bar.open_time)
            self.assertIsNotNone(bar.quote_volume)

    def test_kline_pagination_is_ordered_and_unique(self) -> None:
        end = now_ms()
        bars = list(
            self.client.iter_klines(
                "BTCUSD", "1d", start_time=end - 40 * DAY_MS, end_time=end
            )
        )
        open_times = [bar.open_time for bar in bars]
        self.assertEqual(open_times, sorted(open_times))
        self.assertEqual(len(set(open_times)), len(open_times))

    def test_book_ticker_is_not_crossed(self) -> None:
        quote = self.client.book_ticker("BTCUSD")
        self.assertLessEqual(quote.bid, quote.ask)
        self.assertGreater(quote.mid, ZERO)

    def test_ticker_24h(self) -> None:
        ticker = self.client.ticker_24h("BTCUSD")
        self.assertGreater(ticker.last_price, ZERO)
        self.assertGreaterEqual(ticker.high_price, ticker.low_price)

    def test_rate_limit_headers_are_reported(self) -> None:
        self.client.ping()
        self.assertGreaterEqual(self.client.transport.rate_limit.used_weight_1m, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
