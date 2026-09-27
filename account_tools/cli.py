"""Command line entry point for the read-only account reader.

    python -m account_tools --help

Credentials come from the environment, never from an argument: a command-line
argument lands in shell history and in process listings.

No subcommand here can place, amend or cancel an order, or move funds. The
transport issues GET only, against five read-only endpoints.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from crypto_research.timeutil import now_ms, to_iso

from .client import BinanceUSAccountClient, fees_for_symbols
from .credentials import (
    MissingCredentials,
    load_credentials,
    prompt_for_credentials,
)
from .reporting import (
    render_fee_block,
    render_holdings_block,
    render_key_check,
    summarize_balances,
)
from .signing import AccountTransportError, SignedReadOnlyTransport

DEFAULT_SYMBOLS = "BTCUSD,ETHUSD,SOLUSD"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="account_tools",
        description=(
            "Read-only Binance.US account reader. Reports commission rates and "
            "balances as configuration text. Cannot trade or move funds."
        ),
    )
    # Shared flags live on a parent parser so they work AFTER the subcommand,
    # which is where people naturally type them. Putting them only on the top
    # level would make "check --no-ask" an error.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--no-ask",
        action="store_true",
        help="never prompt; fail if the credential environment variables are unset",
    )
    shared.add_argument(
        "--recv-window-ms",
        type=int,
        default=5_000,
        help="how long the signed request stays valid (default 5000, max 60000)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "check", parents=[shared],
        help="verify the key works and report account exposure",
    )

    fees = sub.add_parser(
        "fees", parents=[shared], help="print a [costs.fees] block with real rates"
    )
    fees.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    fees.add_argument("--window", default="", help="the backtest window these apply to")
    fees.add_argument("--write", help="also write the block to this path")

    holdings = sub.add_parser(
        "holdings", parents=[shared],
        help="print a [portfolio] block from current balances",
    )
    holdings.add_argument("--write", help="also write the block to this path")

    balances = sub.add_parser(
        "balances", parents=[shared], help="list non-zero balances"
    )
    balances.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        credentials = load_credentials()
    except MissingCredentials as env_error:
        # Prompting is the normal path for a human at a terminal, the same way
        # ssh asks for a password. --no-ask is the escape hatch for scripts.
        #
        # Note sys.stdin.isatty() is NOT a reliable guard here: on Windows
        # getpass reads the console handle directly, so redirecting stdin does
        # not stop the prompt. Hence an explicit flag rather than a sniff.
        if args.no_ask:
            print(f"error: {env_error}", file=sys.stderr)
            return 2
        try:
            credentials = prompt_for_credentials()
        except (MissingCredentials, EOFError, KeyboardInterrupt) as exc:
            print(f"\nerror: {exc or 'cancelled'}", file=sys.stderr)
            return 2

    transport = SignedReadOnlyTransport(
        credentials, recv_window_ms=args.recv_window_ms
    )
    client = BinanceUSAccountClient(transport)
    observed_iso = to_iso(now_ms())

    print(f"key {credentials.fingerprint}  read at {observed_iso}\n", file=sys.stderr)

    try:
        return _dispatch(args, client, observed_iso)
    except AccountTransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        _hint(exc)
        return 2


def _dispatch(args, client: BinanceUSAccountClient, observed_iso: str) -> int:
    if args.command == "check":
        snapshot = client.account()
        try:
            trading_status = client.api_trading_status()
        except AccountTransportError:
            # Not fatal: the key may simply lack access to this endpoint, which
            # for a read-only key is a good sign rather than a problem.
            trading_status = {}
        print(render_key_check(snapshot, trading_status))
        print(f"\nnon-zero balances:\n{summarize_balances(snapshot)}")
        return 0

    if args.command == "fees":
        snapshot = client.account()
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        per_symbol = fees_for_symbols(client, symbols)
        block = render_fee_block(
            snapshot, per_symbol, observed_iso=observed_iso, window=args.window
        )
        print(block)
        if snapshot.commission.is_zero:
            print(
                "\n# NOTE: both rates read as zero. That can be a genuine "
                "promotion,\n# but check it before running: a zero-fee backtest "
                "flatters every\n# strategy that trades often.",
            )
        _maybe_write(args.write, block)
        return 0

    if args.command == "holdings":
        snapshot = client.account()
        block = render_holdings_block(snapshot, observed_iso=observed_iso)
        print(block)
        _maybe_write(args.write, block)
        return 0

    if args.command == "balances":
        snapshot = client.account()
        if args.json:
            print(
                json.dumps(
                    {
                        b.asset: {"free": str(b.free), "locked": str(b.locked)}
                        for b in snapshot.non_zero_balances()
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(summarize_balances(snapshot))
        return 0

    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


def _maybe_write(path: str | None, block: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(block + "\n", encoding="utf-8")
    print(f"\n# written to {target}", file=sys.stderr)


def _hint(exc: Exception) -> None:
    text = str(exc)
    if "-1022" in text or "Signature" in text:
        print(
            "hint: signature rejected. Check the secret was pasted whole, with no "
            "trailing newline or quotes.",
            file=sys.stderr,
        )
    elif "-1021" in text or "recvWindow" in text or "timestamp" in text.lower():
        print(
            "hint: timestamp outside recvWindow. Your system clock is likely off; "
            "sync it, or retry with --recv-window-ms 60000.",
            file=sys.stderr,
        )
    elif "-2015" in text or "-2014" in text:
        print(
            "hint: key rejected. Either the key is wrong, or an IP allowlist on the "
            "key does not include this machine.",
            file=sys.stderr,
        )
    elif "401" in text or "403" in text:
        print(
            "hint: the key may lack permission for this endpoint. For a read-only "
            "key that is expected on the status endpoints; `fees` and `holdings` "
            "need only reading permission.",
            file=sys.stderr,
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
