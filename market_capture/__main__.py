"""Run public recording or inspect a saved capture, independently of practice."""
import argparse
import json
from pathlib import Path
import threading

from .service import run, snapshot
from .execution import replay


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Binance.US market recording and hypothetical execution research")
    sub = parser.add_subparsers(dest="command", required=True)
    continuous = sub.add_parser("collect", help="Independent public collection with bounded rotating files")
    continuous.add_argument("--directory", default="data/learning_capture")
    continuous.add_argument("--seconds", type=int, default=None)
    request = sub.add_parser("stop", help="Ask the independent collector to stop")
    request.add_argument("--directory", default="data/learning_capture")
    for name in ("record", "status", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("--database", default="data/market_capture.sqlite")
        if name == "record":
            p.add_argument("--seconds", type=int, default=1800)
        if name == "evaluate":
            from crypto_research.market_universe import SYMBOLS
            p.add_argument("--symbol", choices=SYMBOLS, default="BTCUSD")
            p.add_argument("--notional", required=True)
            p.add_argument("--hold-seconds", type=int, default=60)
            p.add_argument("--latency-ms", type=int, default=100)
            p.add_argument("--fee", default="0.004")
            p.add_argument("--slippage", default="0.001")
    args = parser.parse_args(argv)
    stop = threading.Event()
    try:
        if args.command in ("collect", "stop"):
            from . import background
            if args.command == "collect":
                result = background.collect(args.directory, seconds=args.seconds)
            else:
                background.stop(args.directory)
                result = {"message": "Stop requested; wait for the collector's stopped heartbeat."}
        elif args.command == "record":
            run(Path(args.database), stop, seconds=args.seconds)
            result = snapshot(args.database)
        elif args.command == "status":
            result = snapshot(args.database)
        else:
            result = replay(args.database, args.symbol, args.notional, hold_ms=args.hold_seconds * 1000,
                            latency_ms=args.latency_ms, fee=args.fee, slippage=args.slippage)
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        stop.set()
        return 0
    except Exception as exc:
        print(f"Recording/research error: {type(exc).__name__}: {str(exc)[:200]}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
