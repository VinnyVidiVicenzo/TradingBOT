"""exchangeInfo filter parsing."""

from __future__ import annotations

import unittest

from crypto_research.binanceus.parsing import parse_exchange_info, parse_symbol_rules
from crypto_research.decimals import dec
from crypto_research.errors import FilterParseError

from .helpers import BTCUSD_EXCHANGE_INFO_ENTRY, make_rule_entry


class ParseRealBinanceUsEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = parse_symbol_rules(
            BTCUSD_EXCHANGE_INFO_ENTRY, received_at=1_700_000_000_000, valid_from=1_699_999_999_000
        )

    def test_identity_and_status(self) -> None:
        self.assertEqual(self.snapshot.symbol, "BTCUSD")
        self.assertEqual(self.snapshot.base_asset, "BTC")
        self.assertEqual(self.snapshot.quote_asset, "USD")
        self.assertEqual(self.snapshot.status, "TRADING")
        self.assertTrue(self.snapshot.is_trading)
        self.assertTrue(self.snapshot.is_spot_trading_allowed)

    def test_price_filter(self) -> None:
        self.assertEqual(self.snapshot.price_tick, dec("0.01"))
        self.assertEqual(self.snapshot.min_price, dec("0.01"))
        self.assertEqual(self.snapshot.max_price, dec("1000000"))

    def test_lot_size(self) -> None:
        self.assertEqual(self.snapshot.quantity_step, dec("0.00001"))
        self.assertEqual(self.snapshot.min_quantity, dec("0.00001"))
        self.assertEqual(self.snapshot.max_quantity, dec("9000"))

    def test_market_lot_size_zero_step_is_preserved_not_normalized(self) -> None:
        # Binance.US really returns 0 here. It must survive as 0 and be
        # interpreted as "unconstrained" downstream, never as a real step.
        self.assertEqual(self.snapshot.market_quantity_step, dec("0"))
        self.assertEqual(self.snapshot.market_max_quantity, dec("11.07305291"))

    def test_legacy_min_notional_filter(self) -> None:
        self.assertEqual(self.snapshot.min_notional, dec("1"))
        self.assertIsNone(self.snapshot.max_notional)
        self.assertTrue(self.snapshot.min_notional_applies_to_market)
        self.assertEqual(self.snapshot.notional_avg_price_mins, 5)

    def test_order_count_filters(self) -> None:
        self.assertEqual(self.snapshot.max_num_orders, 200)
        self.assertEqual(self.snapshot.max_num_algo_orders, 5)

    def test_raw_payload_is_retained_verbatim(self) -> None:
        self.assertEqual(self.snapshot.raw_payload["symbol"], "BTCUSD")
        self.assertEqual(len(self.snapshot.raw_payload["filters"]), 8)

    def test_timestamps_are_separate(self) -> None:
        self.assertEqual(self.snapshot.source_received_at, 1_700_000_000_000)
        self.assertEqual(self.snapshot.valid_from, 1_699_999_999_000)


class NotionalFilterVariantTests(unittest.TestCase):
    def test_modern_notional_filter_supplies_both_bounds(self) -> None:
        entry = make_rule_entry(
            "ETHUSD", base="ETH", quote="USD",
            min_notional="10", max_notional="9000000", use_modern_notional=True,
        )
        snapshot = parse_symbol_rules(entry, received_at=0, valid_from=0)
        self.assertEqual(snapshot.min_notional, dec("10"))
        self.assertEqual(snapshot.max_notional, dec("9000000"))
        self.assertTrue(snapshot.min_notional_applies_to_market)
        self.assertTrue(snapshot.max_notional_applies_to_market)

    def test_both_filters_present_takes_the_tightest_bound(self) -> None:
        entry = make_rule_entry("ADAUSD", base="ADA", quote="USD", min_notional="1")
        entry["filters"].append(
            {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "1000",
             "applyMinToMarket": True, "applyMaxToMarket": False}
        )
        snapshot = parse_symbol_rules(entry, received_at=0, valid_from=0)
        self.assertEqual(snapshot.min_notional, dec("5"), "tightest minimum should win")
        self.assertEqual(snapshot.max_notional, dec("1000"))

    def test_absent_notional_filter_leaves_bounds_unset(self) -> None:
        entry = make_rule_entry("XRPUSD", base="XRP", quote="USD", min_notional=None)
        snapshot = parse_symbol_rules(entry, received_at=0, valid_from=0)
        self.assertIsNone(snapshot.min_notional)
        self.assertIsNone(snapshot.max_notional)


class MalformedPayloadTests(unittest.TestCase):
    def test_missing_symbols_array(self) -> None:
        with self.assertRaises(FilterParseError):
            parse_exchange_info({"serverTime": 1}, received_at=0)

    def test_filter_without_type(self) -> None:
        entry = make_rule_entry("BTCUSD", base="BTC", quote="USD")
        entry["filters"].append({"minQty": "1"})
        with self.assertRaises(FilterParseError):
            parse_symbol_rules(entry, received_at=0, valid_from=0)

    def test_symbol_entry_without_symbol(self) -> None:
        with self.assertRaises(FilterParseError):
            parse_symbol_rules({"status": "TRADING"}, received_at=0, valid_from=0)

    def test_unknown_filter_type_is_preserved_but_not_interpreted(self) -> None:
        entry = make_rule_entry("BTCUSD", base="BTC", quote="USD")
        entry["filters"].append({"filterType": "SOME_FUTURE_FILTER", "limit": 3})
        snapshot = parse_symbol_rules(entry, received_at=0, valid_from=0)
        types = {f["filterType"] for f in snapshot.raw_payload["filters"]}
        self.assertIn("SOME_FUTURE_FILTER", types)


class FullPayloadTests(unittest.TestCase):
    def test_parses_every_symbol_and_defaults_valid_from_to_server_time(self) -> None:
        payload = {
            "serverTime": 1_800_000_000_000,
            "symbols": [
                BTCUSD_EXCHANGE_INFO_ENTRY,
                make_rule_entry("ETHUSD", base="ETH", quote="USD"),
            ],
        }
        snapshots = parse_exchange_info(payload, received_at=1_800_000_001_000)
        self.assertEqual([s.symbol for s in snapshots], ["BTCUSD", "ETHUSD"])
        self.assertTrue(all(s.valid_from == 1_800_000_000_000 for s in snapshots))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
