"""Command line entry point.

    python -m external_intelligence --help

Every subcommand here either reads public, permitted data or reads back what
an earlier run recorded. Nothing places, amends or cancels an order; see
docs/trading-bot/SAFETY.md and docs/trading-bot/EXTERNAL_INTELLIGENCE.md.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from typing import Sequence

from crypto_research.errors import CryptoResearchError

from .base.clock import SystemClock
from .base.health import ProviderHealth
from .base.receipt import ReceiptPolicy
from .base.ratelimit import RateLimiter
from .base.transport import UrllibGetFetcher
from .binanceus_trades import BinanceUSTradesAdapter, PollTarget, poll
from .binanceus_trades.adapter import DEFAULT_LARGE_NOTIONAL_USD, DEFAULT_WHALE_NOTIONAL_USD, HOST
from .errors import ExternalIntelligenceError, ProviderRateLimited
from crypto_research.decimals import dec
from .storage import DEFAULT_PATH, IntelligenceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="external-intelligence",
        description=(
            "Research-only market intelligence: normalized events, point-in-time "
            "features, and research tooling. This tool cannot place an order."
        ),
    )
    parser.add_argument("--db", default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    poll_cmd = sub.add_parser(
        "poll-binanceus-trades",
        help="fetch new Binance.US trades for one symbol, keep the large ones",
    )
    poll_cmd.add_argument("--symbol", required=True, help="Binance.US market symbol, e.g. BTCUSD")
    poll_cmd.add_argument("--asset", required=True, help="base asset, e.g. BTC")
    poll_cmd.add_argument(
        "--large-notional-usd", default=str(DEFAULT_LARGE_NOTIONAL_USD),
        help=f"minimum USD size to keep as a large_trade (default {DEFAULT_LARGE_NOTIONAL_USD})",
    )
    poll_cmd.add_argument(
        "--whale-notional-usd", default=str(DEFAULT_WHALE_NOTIONAL_USD),
        help=f"minimum USD size to call it a whale_trade instead (default {DEFAULT_WHALE_NOTIONAL_USD})",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (CryptoResearchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "poll-binanceus-trades":
        return _poll_command(args)
    raise ExternalIntelligenceError(f"unhandled command {args.command!r}")  # pragma: no cover


def _poll_command(args: argparse.Namespace) -> int:
    adapter = BinanceUSTradesAdapter(
        large_notional_usd=dec(args.large_notional_usd),
        whale_notional_usd=dec(args.whale_notional_usd),
    )
    target = PollTarget(args.symbol.upper(), args.asset.upper())
    clock = SystemClock()
    fetcher = UrllibGetFetcher(allowed_hosts=[HOST], clock=clock,
                              rate_limiter=RateLimiter(2, 1000, clock))
    with IntelligenceStore.open(args.db) as store:
        health = ProviderHealth.from_snapshot(adapter.name, store.health.latest(adapter.name))
        if health.last_attempt_at is not None and clock.now_ms() - health.last_attempt_at < 500:
            raise ProviderRateLimited("wait at least 500 ms between polling invocations", 500)
        report = poll(
            adapter, fetcher, store, target,
            policy=ReceiptPolicy.observed_now(), health=health, clock=clock,
        )
    print(f"status          : {report.status}")
    print(f"trades seen     : {report.trades_seen}")
    print(f"events inserted : {report.events_inserted}")
    print(f"duplicates      : {report.duplicates}")
    print(f"quarantined     : {report.quarantined}")
    if report.coverage_from is not None:
        print(f"covered         : {report.coverage_from} .. {report.coverage_to}")
    print(f"cursor          : {report.cursor_before} -> {report.cursor_after}")
    if report.detail:
        print(f"note            : {report.detail}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
