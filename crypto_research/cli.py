"""Command line entry point.

    python -m crypto_research --help

No subcommand here can place an order. The only network calls are GETs
against public Binance.US market-data endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .binanceus.constants import BASE_URL
from .binanceus.public_client import BinanceUSPublicClient
from .binanceus.transport import PublicTransport
from .config import DEFAULT_DB_PATH, load_config
from .datafeed.download import Downloader
from .decimals import format_decimal
from .errors import CryptoResearchError
from .experiments.registry import ExperimentRegistry
from .experiments.runner import run_experiment
from .safety import audit_repository, permitted_endpoints
from .storage.database import open_database
from .storage.repositories import RawSnapshotRepository, RuleSnapshotRepository
from .timeutil import now_ms, parse_time, to_iso


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crypto-research",
        description=(
            "Binance.US accumulation research platform. Research only: this tool "
            "cannot place, amend or cancel an order."
        ),
    )
    parser.add_argument("--db", default=os.environ.get("CRYPTO_RESEARCH_DB_PATH", DEFAULT_DB_PATH))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CRYPTO_RESEARCH_BINANCEUS_BASE_URL", BASE_URL),
        help="Binance.US public REST base URL",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- data ------------------------------------------------------------
    data = sub.add_parser("data", help="download and inspect public market data")
    data_sub = data.add_subparsers(dest="data_command", required=True)

    refresh = data_sub.add_parser("refresh-rules", help="fetch and store exchangeInfo")
    refresh.add_argument("--symbols", help="comma-separated; omit for the whole venue")

    download = data_sub.add_parser("download", help="backfill candlesticks")
    download.add_argument("--symbols", required=True, help="comma-separated, e.g. BTCUSD,ETHUSD")
    download.add_argument("--interval", default="1d")
    download.add_argument("--start", required=True, help="ISO date or epoch ms")
    download.add_argument("--end", help="ISO date or epoch ms (default: now)")
    download.add_argument("--no-resume", action="store_true")

    from_config = data_sub.add_parser(
        "download-for", help="backfill exactly what an experiment config needs"
    )
    from_config.add_argument("config")

    coverage = data_sub.add_parser("coverage", help="what is stored locally")
    coverage.add_argument("--symbols", required=True)
    coverage.add_argument("--interval", default="1d")

    gaps = data_sub.add_parser(
        "gaps", help="report holes in stored history (Binance.US USD pairs have a big one)"
    )
    gaps.add_argument("--symbols", required=True)
    gaps.add_argument("--interval", default="1d")

    rules = data_sub.add_parser("rules", help="show parsed trading filters for a symbol")
    rules.add_argument("--symbol", required=True)
    rules.add_argument("--raw", action="store_true", help="print the raw exchangeInfo entry")

    history = data_sub.add_parser(
        "rule-history", help="every stored exchangeInfo snapshot for a symbol"
    )
    history.add_argument("--symbol", required=True)

    quotes = data_sub.add_parser("quotes", help="snapshot best bid/ask now")
    quotes.add_argument("--symbols", required=True)

    # -- run -------------------------------------------------------------
    run = sub.add_parser("run", help="run a backtest from a config file")
    run.add_argument("config")
    run.add_argument("--experiment-dir", default=None)
    run.add_argument("--quiet", action="store_true")

    # -- experiments -------------------------------------------------------
    experiments = sub.add_parser("experiments", help="browse recorded runs")
    exp_sub = experiments.add_subparsers(dest="experiments_command", required=True)
    listing = exp_sub.add_parser("list")
    listing.add_argument("--experiment-dir", default="experiments")
    listing.add_argument("--limit", type=int, default=30)
    show = exp_sub.add_parser("show")
    show.add_argument("run_id")
    show.add_argument("--experiment-dir", default="experiments")

    # -- safety ------------------------------------------------------------
    sub.add_parser("safety-check", help="verify no live-trading path exists")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except CryptoResearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "data":
        return _data_command(args)
    if args.command == "run":
        return _run_command(args)
    if args.command == "experiments":
        return _experiments_command(args)
    if args.command == "safety-check":
        return _safety_command()
    raise CryptoResearchError(f"unhandled command {args.command!r}")  # pragma: no cover


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------


def _client(args: argparse.Namespace) -> BinanceUSPublicClient:
    return BinanceUSPublicClient(PublicTransport(base_url=args.base_url))


def _symbols(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _data_command(args: argparse.Namespace) -> int:
    with open_database(args.db) as db:
        if args.data_command == "refresh-rules":
            downloader = Downloader(_client(args), db)
            written, digest = downloader.refresh_exchange_info(_symbols(args.symbols) or None)
            print(f"stored {written} symbol rule snapshot(s)")
            print(f"raw exchangeInfo sha256: {digest}")
            return 0

        if args.data_command == "download":
            downloader = Downloader(_client(args), db)
            report = downloader.backfill_klines(
                _symbols(args.symbols),
                args.interval,
                start_time=parse_time(args.start),
                end_time=parse_time(args.end) if args.end else None,
                resume=not args.no_resume,
            )
            print(report.summary())
            return 0

        if args.data_command == "download-for":
            config = load_config(args.config)
            downloader = Downloader(_client(args), open_database(config.data.db_path))
            report = downloader.backfill_klines(
                list(config.data.symbols),
                config.data.interval,
                start_time=config.data.start_time,
                end_time=config.data.end_time,
            )
            print(report.summary())
            return 0

        if args.data_command == "coverage":
            downloader = Downloader(_client(args), db)
            print(downloader.coverage_report(_symbols(args.symbols), args.interval))
            return 0

        if args.data_command == "gaps":
            return _print_gaps(db, args)

        if args.data_command == "rules":
            return _print_rules(db, args)

        if args.data_command == "rule-history":
            snapshots = RuleSnapshotRepository(db).history(args.symbol.upper())
            if not snapshots:
                print(f"no stored rules for {args.symbol.upper()}")
                return 1
            for snapshot in snapshots:
                print(
                    f"{to_iso(snapshot.source_received_at)}  {snapshot.status:<10} "
                    f"tick={_fmt(snapshot.price_tick)} step={_fmt(snapshot.quantity_step)} "
                    f"minNotional={_fmt(snapshot.min_notional)}"
                )
            hashes = RawSnapshotRepository(db).distinct_hashes()
            print(f"\n{len(hashes)} distinct venue-wide exchangeInfo payload(s) stored")
            return 0

        if args.data_command == "quotes":
            downloader = Downloader(_client(args), db)
            count = downloader.snapshot_book_tickers(_symbols(args.symbols))
            print(f"stored {count} quote snapshot(s)")
            return 0

    raise CryptoResearchError(f"unhandled data command {args.data_command!r}")


def _print_gaps(db, args: argparse.Namespace) -> int:
    """Report missing bars, which is how a suspended market becomes visible."""
    from .datafeed.dataset import find_gaps
    from .storage.repositories import BarRepository

    repo = BarRepository(db)
    bars_by_symbol = {
        symbol: repo.load(symbol, args.interval) for symbol in _symbols(args.symbols)
    }
    empty = [symbol for symbol, bars in bars_by_symbol.items() if not bars]
    for symbol in empty:
        print(f"{symbol:<12} no stored {args.interval} bars")
        bars_by_symbol.pop(symbol)
    if not bars_by_symbol:
        return 1

    found = find_gaps(bars_by_symbol, args.interval)
    for symbol, bars in sorted(bars_by_symbol.items()):
        symbol_gaps = [gap for gap in found if gap.symbol == symbol]
        print(
            f"{symbol:<12} {len(bars):>6} bars  "
            f"{to_iso(bars[0].open_time)[:10]} .. {to_iso(bars[-1].open_time)[:10]}  "
            f"{len(symbol_gaps)} gap(s)"
        )
        for gap in symbol_gaps:
            print(f"    {gap.describe()}")
    if found:
        print(
            "\nA gap means the market was not trading. Binance.US suspended USD "
            "trading from 2023-07-14 to 2025-02-19, so USD-quoted pairs have no "
            "history across it."
        )
    return 0


def _print_rules(db, args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    snapshots = RuleSnapshotRepository(db).latest()
    match = next((s for s in snapshots if s.symbol == symbol), None)
    if match is None:
        print(f"no stored rules for {symbol}; run `data refresh-rules --symbols {symbol}`")
        return 1
    if args.raw:
        print(json.dumps(match.raw_payload, indent=2, sort_keys=True))
        return 0
    rows = [
        ("status", match.status),
        ("base / quote", f"{match.base_asset} / {match.quote_asset}"),
        ("spot allowed", match.is_spot_trading_allowed),
        ("price tick", _fmt(match.price_tick)),
        ("price min / max", f"{_fmt(match.min_price)} / {_fmt(match.max_price)}"),
        ("qty step", _fmt(match.quantity_step)),
        ("qty min / max", f"{_fmt(match.min_quantity)} / {_fmt(match.max_quantity)}"),
        ("market qty step", _fmt(match.market_quantity_step)),
        ("market qty min / max",
         f"{_fmt(match.market_min_quantity)} / {_fmt(match.market_max_quantity)}"),
        ("min notional", _fmt(match.min_notional)),
        ("max notional", _fmt(match.max_notional)),
        ("min notional -> market", match.min_notional_applies_to_market),
        ("notional avgPriceMins", match.notional_avg_price_mins),
        ("order types", ", ".join(match.order_types)),
        ("observed at", to_iso(match.source_received_at)),
    ]
    for label, value in rows:
        print(f"{label:<24} {value}")
    return 0


def _fmt(value) -> str:
    return "-" if value is None else format_decimal(value)


# --------------------------------------------------------------------------
# run / experiments
# --------------------------------------------------------------------------


def _run_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = ExperimentRegistry(args.experiment_dir or config.experiment_dir)
    record, result, metrics = run_experiment(config, registry=registry)

    print(f"run id : {record.run_id}")
    print(f"status : {record.status.value}")
    print(f"config : {record.config_sha256[:16]}")
    if record.dataset_version:
        print(f"dataset: {record.dataset_version['sha256'][:16]} "
              f"({record.dataset_version['bar_count']} bars)")
    if record.git_commit:
        dirty = " (dirty working tree)" if record.git_dirty else ""
        print(f"commit : {record.git_commit[:12]}{dirty}")

    if record.status.value == "FAILED":
        print(f"\nFAILED: {record.error}", file=sys.stderr)
        print(f"recorded at experiments/runs/{record.run_id}/", file=sys.stderr)
        return 1

    assert metrics is not None and result is not None
    if not args.quiet:
        print()
        _print_metrics(metrics)
        for warning in record.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    print(f"\nwritten to {registry.runs_dir / record.run_id}")
    return 0


def _print_metrics(metrics) -> None:
    payload = metrics.to_dict()
    order = [
        "starting_portfolio_value",
        "total_external_contributions",
        "total_capital_in",
        "ending_portfolio_value",
        "net_profit",
        "net_return",
        "time_weighted_return",
        "max_drawdown",
        "total_purchased_capital",
        "total_fees_in_quote",
        "estimated_spread_cost",
        "estimated_slippage_cost",
        "total_cost_drag",
        "turnover",
        "time_weighted_exposure",
        "uninvested_cash_value",
        "uninvested_cash_fraction",
        "attempted_orders",
        "accepted_orders",
        "partially_filled_orders",
        "rejected_orders",
    ]
    for key in order:
        print(f"{key:<30} {payload[key]}")
    if payload["average_entry_price_by_asset"]:
        print("average entry price")
        for asset, price in payload["average_entry_price_by_asset"].items():
            print(f"  {asset:<10} {price}")
    if payload["rejections_by_reason"]:
        print("rejections")
        for reason, count in payload["rejections_by_reason"].items():
            print(f"  {reason:<34} {count}")


def _experiments_command(args: argparse.Namespace) -> int:
    registry = ExperimentRegistry(args.experiment_dir)
    if args.experiments_command == "list":
        entries = registry.list_runs()[-args.limit :]
        if not entries:
            print("no runs recorded yet")
            return 0
        print(f"{'run id':<28}{'status':<11}{'strategy':<34}{'net return':>12}")
        for entry in entries:
            net = entry.get("net_return")
            net_text = f"{float(net) * 100:.2f}%" if net not in (None, "") else "-"
            print(
                f"{entry['run_id']:<28}{entry['status']:<11}"
                f"{entry['strategy']:<34}{net_text:>12}"
            )
        return 0

    if args.experiments_command == "show":
        print(json.dumps(registry.load(args.run_id), indent=2, sort_keys=True))
        return 0

    raise CryptoResearchError(  # pragma: no cover
        f"unhandled experiments command {args.experiments_command!r}"
    )


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------


def _safety_command() -> int:
    report = audit_repository(Path(__file__).parent.parent)
    print(report.summary())
    for zone, endpoints in permitted_endpoints().items():
        print(f"\npermitted endpoints -- {zone}:")
        for endpoint in endpoints:
            print(f"  {endpoint}")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
