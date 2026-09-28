"""Command line entry point.

    python -m paper_trading --help

Every subcommand here either reads public Binance.US market data or reads
back what an earlier tick recorded. Nothing places, amends or cancels an
order; see docs/trading-bot/SAFETY.md and docs/trading-bot/PAPER_TRADING.md.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from typing import Sequence

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.errors import CryptoResearchError
from crypto_research.experiments.registry import ExperimentRegistry
from crypto_research.timeutil import now_ms, to_iso

from . import depth_cost, ticklog
from .errors import PaperTradingError
from .plan import RehearsalPlan, load_plan
from .report import build_report
from .runner import latest_completed, tick
from .scorecard import evaluate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-trading",
        description=(
            "Rehearse a plan against live Binance.US prices, with pretend money. "
            "This tool cannot place, amend or cancel an order."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("tick", "report"):
        p = sub.add_parser(name)
        p.add_argument("--rehearsal", required=True, help="config file for the plan under test")
        p.add_argument("--benchmark", required=True, help="config file for what it is compared against")
        p.add_argument("--name", default=None, help="defaults to the rehearsal config's [experiment] name")

    cost = sub.add_parser(
        "measure-cost", help="read the live order book and report the cost of a hypothetical buy"
    )
    cost.add_argument("--symbol", required=True)
    cost.add_argument("--notional", required=True, help="how much quote currency to spend, e.g. 200")
    cost.add_argument("--log", default=None, help="append the measurement to this file")

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
    if args.command == "tick":
        return _tick_command(args)
    if args.command == "report":
        return _report_command(args)
    if args.command == "measure-cost":
        return _measure_cost_command(args)
    raise PaperTradingError(f"unhandled command {args.command!r}")  # pragma: no cover


def _load(args: argparse.Namespace) -> RehearsalPlan:
    return load_plan(args.rehearsal, args.benchmark, name=args.name)


def _tick_command(args: argparse.Namespace) -> int:
    plan = _load(args)
    result = tick(plan)
    print(f"status : {result.status}")
    if result.end_time is not None:
        print(f"as of  : {to_iso(result.end_time)}")
    for label, track in (("rehearsal", result.rehearsal), ("benchmark", result.benchmark)):
        if track is not None:
            reused = " (already recorded)" if track.reused else ""
            print(f"{label:<10}: {track.status}{reused}  run_id={track.run_id}")
    if result.detail:
        print(f"note   : {result.detail}")
    return 0


def _report_command(args: argparse.Namespace) -> int:
    plan = _load(args)
    registry = ExperimentRegistry(plan.experiment_dir)
    rehearsal_record = latest_completed(registry, plan.rehearsal.name)
    benchmark_record = latest_completed(registry, plan.benchmark.name)
    ticks = ticklog.read_all(ticklog.path_for(plan.experiment_dir, plan.name))
    scorecard = evaluate(ticks, rehearsal_record=rehearsal_record, benchmark_record=benchmark_record)

    print(
        build_report(
            plan.name,
            rehearsal_record=rehearsal_record,
            benchmark_record=benchmark_record,
            scorecard=scorecard,
            ticks=ticks,
        )
    )
    return 0


def _measure_cost_command(args: argparse.Namespace) -> int:
    client = BinanceUSPublicClient()
    measurement = depth_cost.measure_buy_cost(
        client, args.symbol.upper(), Decimal(args.notional), at=now_ms()
    )
    if not measurement.depth_available:
        print(f"{args.symbol}: no order book returned")
        return 1
    print(f"mid price        : {measurement.mid_price}")
    if measurement.cost_fraction is None:
        print("the book could not fill this notional")
    else:
        print(f"average price    : {measurement.average_price}")
        print(f"cost above mid   : {measurement.cost_fraction * 100:.4f}%")
    if args.log:
        depth_cost.append(args.log, measurement)
        print(f"appended to {args.log}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
