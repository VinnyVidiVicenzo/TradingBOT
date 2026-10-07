"""Binance.US's own public trade feed: a free, live source of real executed trades.

Unlike Whale Alert and CoinLobster (user-provided exports only -- see
``whale_alert/`` and ``coinlobster/``), this adapter genuinely fetches live: the
aggregate-trade endpoint (``/api/v3/aggTrades``) is public, free, and already on
this project's permitted-endpoint list (``crypto_research/binanceus/constants.py``).
Every record it reports is a real trade that really executed on the venue this
project actually trades, with a real, observed receipt time -- the first
provider in this zone for which that is true (checked against a live response,
2026-10-01).

What it reports and what it does not
-------------------------------------
Binance's own ``isBuyerMaker`` flag states which side was the taker (the side
that crossed the spread and caused the trade): ``isBuyerMaker=true`` means the
seller was the taker (a sell), ``isBuyerMaker=false`` means the buyer was the
taker (a buy). This is reported as ``side``, exactly as the venue states it --
never inferred from price movement or anything else.

Only USD-quoted pairs are supported. For a USDT-quoted pair the same arithmetic
would produce a USDT notional, not a USD one, and this project never treats the
two as the same asset; add USDT-quoted support only alongside a conversion this
project can defend, not by relabelling the number.

Size matters, not direction: a trade's size says nothing about whether the
market will go up or down (see docs/trading-bot/EXTERNAL_INTELLIGENCE.md,
"why whale data is not inherently directional"). Nothing here derives a
bullish or bearish label from any of this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from crypto_research.decimals import dec

from ..base.executed_activity import ExecutedActivityAdapter
from ..base.provider import FetchedPayload
from ..base.transport import FetchRequest, Fetcher, FetchResponse
from ..errors import ProviderSchemaError

HOST = "api.binance.us"
BASE_URL = "https://" + HOST
AGG_TRADES_PATH = "/api/v3/aggTrades"

DEFAULT_LARGE_NOTIONAL_USD = Decimal("50000")
DEFAULT_WHALE_NOTIONAL_USD = Decimal("250000")
MAX_PAGE = 1000


@dataclass(frozen=True, slots=True)
class RawTrade:
    """One parsed aggregate trade, before any size threshold is applied."""

    trade_id: int
    price: Decimal
    quantity: Decimal
    time_ms: int
    is_buyer_maker: bool

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def side(self) -> str:
        """Which side was the taker -- the side that actually crossed the spread."""
        return "sell" if self.is_buyer_maker else "buy"


class BinanceUSTradesAdapter(ExecutedActivityAdapter):
    """Reads Binance.US's own public trade feed and classifies trades by size.

    ``normalize_record`` is inherited unchanged from ``ExecutedActivityAdapter``:
    this adapter's own job is only to fetch the venue's trades and convert them
    into the project's ``executed_activity_flat_v1`` shape, so the same tested
    classification code runs whether a record came from here, from CoinLobster,
    or from a hand-written test.
    """

    name = "binanceus_trades"
    live_ingestion_status = (
        "on: reads /api/v3/aggTrades, a public Binance.US endpoint already on this "
        "project's permitted list (crypto_research/binanceus/constants.py). No key, "
        "no subscription, no terms beyond ordinary public API use."
    )
    terms_note = (
        "Binance.US's API terms govern this endpoint -- the same terms the research "
        "engine's own downloads already operate under. Nothing here authenticates, "
        "and the documented rate limit (weight 4 per call) is respected by the "
        "project's shared rate-limit awareness; see base/ratelimit.py."
    )

    def __init__(
        self,
        *,
        large_notional_usd: Decimal = DEFAULT_LARGE_NOTIONAL_USD,
        whale_notional_usd: Decimal = DEFAULT_WHALE_NOTIONAL_USD,
    ) -> None:
        large_notional_usd, whale_notional_usd = dec(large_notional_usd), dec(whale_notional_usd)
        if large_notional_usd <= 0 or whale_notional_usd <= 0:
            raise ValueError("notional thresholds must be positive")
        if whale_notional_usd < large_notional_usd:
            raise ValueError("whale_notional_usd must be at least large_notional_usd")
        self.large_notional_usd = large_notional_usd
        self.whale_notional_usd = whale_notional_usd
        self.last_response: FetchResponse | None = None

    # -- fetching -------------------------------------------------------------

    def fetch_raw(
        self, fetcher: Fetcher, market_symbol: str, *,
        from_id: int | None = None, limit: int = MAX_PAGE,
    ) -> list[RawTrade]:
        """The venue's own trades, unfiltered, oldest first.

        ``from_id`` asks for everything at or after that trade id (Binance.US's
        own convention); omit it for the most recent ``limit`` trades. This is
        the one place that talks to the network; everything else in this module
        works from what it returns.
        """
        if not 1 <= limit <= MAX_PAGE:
            raise ValueError(f"limit must be 1..{MAX_PAGE}")
        self.last_response = None
        if not market_symbol.endswith("USD") or not market_symbol[:-3].isalnum():
            raise ValueError("only USD market symbols are supported")
        if from_id is not None:
            _integer(from_id)
        params = {"symbol": market_symbol, "limit": str(limit)}
        if from_id is not None:
            params["fromId"] = str(from_id)
        self.last_response = fetcher.fetch(FetchRequest(BASE_URL + AGG_TRADES_PATH, params))
        try:
            payload = json.loads(self.last_response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise ProviderSchemaError("invalid trade response JSON") from exc
        if not isinstance(payload, list):
            raise ProviderSchemaError(f"expected a list of trades, got {type(payload).__name__}")
        if len(payload) > limit:
            raise ProviderSchemaError("trade response exceeds requested page size")
        # Refuse the whole page on schema loss. Advancing past an unreadable
        # trade would turn missing history into a falsely complete window.
        trades: list[RawTrade] = []
        for row in payload:
            trades.append(_parse_trade(row))
        return trades

    def fetch_payload(self, fetcher: Fetcher, request: FetchRequest) -> FetchedPayload:
        """The generic single-page path, for a caller using ``ingest.poll_once`` directly.

        The dedicated poller (``poller.py``) calls ``fetch_raw`` itself instead,
        because it also needs the *un*filtered trades to advance its cursor and
        declare coverage honestly -- see ``poller.py`` for why that matters.
        """
        market_symbol = request.params.get("symbol")
        asset = request.params.get("asset")
        if not market_symbol or not asset:
            raise ValueError(
                "fetch_payload needs params={'symbol': <Binance.US market symbol>, "
                "'asset': <base asset>}, e.g. {'symbol': 'BTCUSD', 'asset': 'BTC'}"
            )
        quote_asset = request.params.get("quote", "USD")
        trades_to_flat_records([], self.large_notional_usd, self.whale_notional_usd,
                              asset=asset, quote_asset=quote_asset, market_symbol=market_symbol)
        limit = int(request.params.get("limit", MAX_PAGE))
        from_id = request.params.get("fromId")
        trades = self.fetch_raw(
            fetcher, market_symbol, from_id=int(from_id) if from_id else None, limit=limit
        )
        records = trades_to_flat_records(
            trades, self.large_notional_usd, self.whale_notional_usd,
            asset=asset, quote_asset=quote_asset, market_symbol=market_symbol,
        )
        text = records_to_jsonl(records)
        return FetchedPayload(
            text, ".jsonl", source_ref=f"{BASE_URL}{AGG_TRADES_PATH}?symbol={market_symbol}"
        )


# --------------------------------------------------------------------------
# turning a raw trade into the project's own executed_activity_flat_v1 shape
# --------------------------------------------------------------------------


def trades_to_flat_records(
    trades: Sequence[RawTrade], large_notional_usd: Decimal, whale_notional_usd: Decimal,
    *, asset: str = "", quote_asset: str = "USD", market_symbol: str = "",
) -> list[dict[str, Any]]:
    """Trades at or above ``large_notional_usd``, as ``executed_activity_flat_v1`` records.

    A trade below the threshold is not reported here at all -- it was still seen
    (the caller's cursor and coverage accounting cover it), it simply was not
    large enough to be worth an event of its own.
    """
    records: list[dict[str, Any]] = []
    if quote_asset != "USD" or (market_symbol and market_symbol != asset + "USD"):
        raise ValueError("trade market must match the base asset and USD quote")
    for trade in trades:
        if trade.notional < large_notional_usd:
            continue
        kind = "whale_trade" if trade.notional >= whale_notional_usd else "large_trade"
        records.append(
            {
                "kind": kind,
                "id": f"{market_symbol or asset + 'USD'}:{trade.trade_id}",
                "time_ms": trade.time_ms,
                "symbol": asset,
                "quote": quote_asset,
                "exchange": "Binance.US",
                "market_symbol": market_symbol,
                "side": trade.side,
                "notional_usd": str(trade.notional),
                "quantity": str(trade.quantity),
                "price": str(trade.price),
            }
        )
    return records


def records_to_jsonl(records: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(json.dumps(record, sort_keys=True) for record in records)


def _parse_trade(row: Mapping[str, Any]) -> RawTrade:
    try:
        if not isinstance(row, Mapping) or not isinstance(row.get("m"), bool):
            raise ValueError("trade row and maker flag have invalid types")
        return RawTrade(
            trade_id=_integer(row["a"]),
            price=_positive_decimal(row["p"], "p"),
            quantity=_positive_decimal(row["q"], "q"),
            time_ms=_integer(row["T"]),
            is_buyer_maker=row["m"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderSchemaError(f"unrecognized trade row: {exc}") from exc


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("trade identity/time must be an integer")
    if len(str(value)) > 19:
        raise ValueError("trade identity/time exceeds supported range")
    number = int(value)
    if not 0 <= number <= 2**63 - 1:
        raise ValueError("trade identity/time exceeds supported range")
    return number


def _positive_decimal(value: Any, name: str) -> Decimal:
    try:
        number = dec(value)
    except (ValueError, InvalidOperation) as exc:
        raise ProviderSchemaError(f"{name} is not a number: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ProviderSchemaError(f"{name} must be a positive, finite number, got {value!r}")
    return number
