"""Binance.US REST surface used by V1.

Source of truth: the official Binance.US API documentation at
https://docs.binance.us/ . Values were additionally confirmed against a live
call to the public endpoints on 2026-09-26.

Binance.US only. Binance.com base URLs, testnets and pairs are intentionally
absent: the two exchanges list different symbols, publish different filters
and charge different fees, so a Binance.com figure would quietly invalidate
every experiment run here.
"""

from __future__ import annotations

from types import MappingProxyType

BASE_URL = "https://api.binance.us"

# Endpoint path -> documented request weight (IP weight, per call).
# Where the documented weight varies with parameters, the value here is the
# cheapest case and `weight_hint` in the client adjusts upward.
PUBLIC_ENDPOINTS = MappingProxyType(
    {
        "/api/v3/ping": 1,
        "/api/v3/time": 1,
        "/api/v3/exchangeInfo": 20,
        "/api/v3/depth": 5,
        "/api/v3/trades": 25,
        "/api/v3/aggTrades": 4,
        "/api/v3/klines": 1,
        "/api/v3/ticker/price": 1,
        "/api/v3/ticker/bookTicker": 1,
        "/api/v3/ticker/24hr": 1,
        "/api/v3/ticker": 2,
    }
)

# Documented IP rate limits. The live exchangeInfo response also reports these
# under `rateLimits`, and the downloader prefers the live values when present.
DEFAULT_REQUEST_WEIGHT_PER_MINUTE = 6000
DEFAULT_RAW_REQUESTS_PER_5_MIN = 61000

# Maximum rows a single /api/v3/klines call returns.
MAX_KLINES_LIMIT = 1000
DEFAULT_KLINES_LIMIT = 1000

# Depth limits accepted by /api/v3/depth.
VALID_DEPTH_LIMITS = (1, 5, 10, 20, 50, 100, 500, 1000, 5000)

# Symbol filter types this codebase understands. Anything else that appears in
# exchangeInfo is preserved in raw_payload but not interpreted, so an unknown
# filter can never be silently treated as satisfied.
KNOWN_FILTER_TYPES = frozenset(
    {
        "PRICE_FILTER",
        "PERCENT_PRICE",
        "PERCENT_PRICE_BY_SIDE",
        "LOT_SIZE",
        "MARKET_LOT_SIZE",
        "MIN_NOTIONAL",
        "NOTIONAL",
        "ICEBERG_PARTS",
        "MAX_NUM_ORDERS",
        "MAX_NUM_ALGO_ORDERS",
        "MAX_NUM_ORDER_LISTS",
        "MAX_NUM_ORDER_AMENDS",
        "MAX_ICEBERG_ORDERS",
        "MAX_POSITION",
        "TRAILING_DELTA",
    }
)

# Symbol statuses. Only TRADING accepts orders.
STATUS_TRADING = "TRADING"

USER_AGENT = "crypto-research/0.1 (research-only; no order placement)"
