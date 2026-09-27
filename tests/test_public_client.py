"""The Binance.US public client and its transport, against a fake opener.

No test here touches the network. Live connectivity is exercised by
``tests/test_live_binanceus.py``, which skips unless explicitly enabled.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from email.message import Message
from typing import Any

from crypto_research.binanceus.constants import BASE_URL
from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.binanceus.transport import PublicTransport, _encode_params
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError, RateLimitError, TransportError
from crypto_research.timeutil import DAY_MS

from .helpers import BTCUSD_EXCHANGE_INFO_ENTRY


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        message = Message()
        for key, value in (headers or {}).items():
            message[key] = value
        self.headers = message

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class FakeOpener:
    """Returns queued responses and records the URLs it was asked for."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []
        self.methods: list[str] = []
        self.headers: list[dict[str, str]] = []

    def open(self, request, timeout=None):  # noqa: ANN001 - urllib signature
        self.urls.append(request.full_url)
        self.methods.append(request.get_method())
        self.headers.append(dict(request.headers))
        if not self._responses:
            raise AssertionError(f"no queued response for {request.full_url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_with(responses: list[Any]) -> tuple[BinanceUSPublicClient, FakeOpener]:
    opener = FakeOpener(responses)
    transport = PublicTransport(opener=opener, sleep=lambda _seconds: None, max_retries=2)
    return BinanceUSPublicClient(transport), opener


class TransportSafetyTests(unittest.TestCase):
    def test_only_get_is_ever_issued(self) -> None:
        client, opener = client_with([FakeResponse({})])
        client.ping()
        self.assertEqual(opener.methods, ["GET"])

    def test_no_credential_headers_are_sent(self) -> None:
        client, opener = client_with([FakeResponse({})])
        client.ping()
        sent = {key.lower() for key in opener.headers[0]}
        self.assertNotIn("authorization", sent)
        self.assertFalse(any(key.startswith("x-mbx-api") for key in sent))

    def test_a_non_whitelisted_path_is_refused_before_any_request(self) -> None:
        opener = FakeOpener([])
        transport = PublicTransport(opener=opener, sleep=lambda _s: None)
        with self.assertRaises(TransportError) as ctx:
            transport.get("/api/v3/somethingElse")
        self.assertIn("not a permitted public endpoint", str(ctx.exception))
        self.assertEqual(opener.urls, [], "nothing should have been sent")

    def test_the_base_url_is_binance_us(self) -> None:
        self.assertEqual(BASE_URL, "https://api.binance.us")


class TransportBehaviourTests(unittest.TestCase):
    def test_query_parameters_are_sorted_for_stable_urls(self) -> None:
        self.assertEqual(
            _encode_params({"symbol": "BTCUSD", "interval": "1d", "limit": 5}),
            "interval=1d&limit=5&symbol=BTCUSD",
        )

    def test_none_parameters_are_dropped(self) -> None:
        self.assertEqual(_encode_params({"a": 1, "b": None}), "a=1")

    def test_used_weight_header_is_tracked(self) -> None:
        client, _ = client_with([FakeResponse({}, headers={"x-mbx-used-weight-1m": "137"})])
        client.ping()
        self.assertEqual(client.transport.rate_limit.used_weight_1m, 137)

    def test_a_429_is_retried_then_surfaces_as_a_rate_limit_error(self) -> None:
        error = urllib.error.HTTPError(
            url="https://api.binance.us/api/v3/ping", code=429,
            msg="Too Many Requests", hdrs=Message(),
            fp=_BytesLike(json.dumps({"code": -1003, "msg": "way too many"}).encode()),
        )
        opener = FakeOpener([error, error])
        transport = PublicTransport(opener=opener, sleep=lambda _s: None, max_retries=2)
        with self.assertRaises(TransportError):
            transport.get("/api/v3/ping")
        self.assertEqual(len(opener.urls), 2, "both attempts should have been made")

    def test_a_structured_api_error_is_parsed(self) -> None:
        error = urllib.error.HTTPError(
            url="https://api.binance.us/api/v3/klines", code=400,
            msg="Bad Request", hdrs=Message(),
            fp=_BytesLike(json.dumps({"code": -1121, "msg": "Invalid symbol."}).encode()),
        )
        opener = FakeOpener([error])
        transport = PublicTransport(opener=opener, sleep=lambda _s: None, max_retries=1)
        with self.assertRaises(TransportError) as ctx:
            transport.get("/api/v3/klines", {"symbol": "NOPE", "interval": "1d"})
        self.assertIn("Invalid symbol", str(ctx.exception))


class _BytesLike:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        return None


class ClientMethodTests(unittest.TestCase):
    def test_server_time(self) -> None:
        client, opener = client_with([FakeResponse({"serverTime": 1_700_000_000_000})])
        self.assertEqual(client.server_time(), 1_700_000_000_000)
        self.assertTrue(opener.urls[0].endswith("/api/v3/time"))

    def test_exchange_info_keeps_the_raw_payload(self) -> None:
        payload = {"serverTime": 1_700_000_000_000, "symbols": [BTCUSD_EXCHANGE_INFO_ENTRY]}
        client, _ = client_with([FakeResponse(payload)])
        result = client.exchange_info(["BTCUSD"])
        self.assertEqual(result.raw_payload, payload)
        self.assertEqual(len(result.snapshots), 1)
        self.assertEqual(result.rule_book().get("BTCUSD").snapshot.price_tick, dec("0.01"))

    def test_exchange_info_encodes_a_multi_symbol_request(self) -> None:
        payload = {"serverTime": 1, "symbols": [BTCUSD_EXCHANGE_INFO_ENTRY]}
        client, opener = client_with([FakeResponse(payload)])
        client.exchange_info(["BTCUSD", "ETHUSD"])
        self.assertIn("symbols=", opener.urls[0])
        self.assertIn("BTCUSD", opener.urls[0])

    def test_symbol_status(self) -> None:
        payload = {"serverTime": 1, "symbols": [BTCUSD_EXCHANGE_INFO_ENTRY]}
        client, _ = client_with([FakeResponse(payload)])
        self.assertEqual(client.symbol_status("BTCUSD"), "TRADING")

    def test_symbol_status_for_an_unlisted_symbol_raises(self) -> None:
        client, _ = client_with([FakeResponse({"serverTime": 1, "symbols": []})])
        with self.assertRaises(ConfigError):
            client.symbol_status("NOPEUSD")

    def test_book_ticker(self) -> None:
        client, _ = client_with([
            FakeResponse({
                "symbol": "BTCUSD", "bidPrice": "84422.16", "bidQty": "0.0026",
                "askPrice": "84422.24", "askQty": "0.01966",
            })
        ])
        quote = client.book_ticker("BTCUSD")
        self.assertEqual(quote.bid, dec("84422.16"))
        self.assertEqual(quote.ask, dec("84422.24"))
        self.assertEqual(quote.mid, (dec("84422.16") + dec("84422.24")) / 2)

    def test_ticker_price(self) -> None:
        client, _ = client_with([FakeResponse({"symbol": "BTCUSD", "price": "84422.20"})])
        self.assertEqual(client.ticker_price("BTCUSD"), dec("84422.20"))

    def test_ticker_24h(self) -> None:
        client, _ = client_with([
            FakeResponse({
                "symbol": "BTCUSD", "priceChange": "1", "priceChangePercent": "0.5",
                "weightedAvgPrice": "84000", "prevClosePrice": "83900",
                "lastPrice": "84440.55", "openPrice": "84094.42", "highPrice": "84450.78",
                "lowPrice": "83788.56", "volume": "10.08663", "quoteVolume": "848303.08",
                "openTime": 1, "closeTime": 2, "count": 4753,
            })
        ])
        ticker = client.ticker_24h("BTCUSD")
        self.assertEqual(ticker.last_price, dec("84440.55"))
        self.assertEqual(ticker.trade_count, 4753)

    def test_klines_are_parsed_from_the_array_layout(self) -> None:
        row = [
            1790380800000, "84094.42", "84450.78", "83788.56", "84440.55",
            "10.08663", 1790467199999, "848303.08", 4753, "4.91060", "412961.79", "0",
        ]
        client, _ = client_with([FakeResponse([row])])
        bars = client.klines("BTCUSD", "1d", limit=1)
        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual(bar.open_time, 1790380800000)
        self.assertEqual(bar.close_time, 1790467199999)
        self.assertEqual(bar.close, dec("84440.55"))
        self.assertEqual(bar.quote_volume, dec("848303.08"))
        self.assertEqual(bar.trade_count, 4753)

    def test_an_unsupported_interval_is_refused_locally(self) -> None:
        client, opener = client_with([])
        with self.assertRaises(ConfigError):
            client.klines("BTCUSD", "7m")
        self.assertEqual(opener.urls, [])

    def test_an_out_of_range_limit_is_refused_locally(self) -> None:
        client, _ = client_with([])
        with self.assertRaises(ConfigError):
            client.klines("BTCUSD", "1d", limit=5000)

    def test_an_invalid_depth_limit_is_refused_locally(self) -> None:
        client, _ = client_with([])
        with self.assertRaises(ConfigError):
            client.depth("BTCUSD", limit=7)

    def test_iter_klines_pages_without_duplicating_or_looping(self) -> None:
        def page(start_ms: int, count: int) -> list[list[Any]]:
            return [
                [
                    start_ms + i * DAY_MS, "1", "1", "1", "1", "1",
                    start_ms + (i + 1) * DAY_MS - 1, "1", 1, "1", "1", "0",
                ]
                for i in range(count)
            ]

        first = page(0, 3)
        second = page(3 * DAY_MS, 2)
        client, _ = client_with([
            FakeResponse(first), FakeResponse(second), FakeResponse([])
        ])
        bars = list(
            client.iter_klines("BTCUSD", "1d", start_time=0, end_time=10 * DAY_MS)
        )
        open_times = [bar.open_time for bar in bars]
        self.assertEqual(open_times, sorted(open_times))
        self.assertEqual(len(set(open_times)), len(open_times))
        self.assertEqual(len(bars), 5)

    def test_iter_klines_stops_when_a_page_does_not_advance(self) -> None:
        stuck = [[0, "1", "1", "1", "1", "1", DAY_MS - 1, "1", 1, "1", "1", "0"]]
        client, opener = client_with([FakeResponse(stuck), FakeResponse(stuck)])
        bars = list(client.iter_klines("BTCUSD", "1d", start_time=0, end_time=50 * DAY_MS))
        self.assertEqual(len(bars), 1)
        self.assertLessEqual(len(opener.urls), 2)


class RateLimitClassificationTests(unittest.TestCase):
    def test_418_is_a_rate_limit_error(self) -> None:
        from crypto_research.binanceus.transport import _api_error

        error = _api_error(418, b'{"code":-1003,"msg":"banned"}', "url")
        self.assertIsInstance(error, RateLimitError)
        self.assertEqual(error.status, 418)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
