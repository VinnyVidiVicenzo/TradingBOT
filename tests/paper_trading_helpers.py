"""Fixtures shared by the paper-trading tests.

``FakeClient`` implements only the two methods ``Downloader`` ever calls
(``exchange_info``, ``iter_klines``) plus ``depth`` for the cost measurement.
Tests exercise ``paper_trading``'s own logic -- idempotency, the shared-data
rule, the scorecard, the report -- through the real ``Downloader`` and the real
``run_experiment``/``ExperimentRegistry``, exactly as the running program would;
only the network boundary is replaced. Parsing Binance.US's JSON is already
covered by ``tests/test_public_client.py`` and is not re-tested here.

Every start time in these tests is safely in the past (2024), because
``Downloader.backfill_klines`` itself refuses a bar whose close time is after
the *real* wall clock -- deliberately, so paper trading can never be fooled
into treating a bar as closed before it has really closed. A start time near
"today" would make these tests flicker depending on when they happen to run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from crypto_research.binanceus.parsing import parse_symbol_rules
from crypto_research.binanceus.public_client import ExchangeInfoResult
from crypto_research.errors import TransportError
from crypto_research.timeutil import parse_time

from tests.helpers import make_bars, make_rule_entry

START = parse_time("2024-01-01T00:00:00Z")
SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD")


class FakeClient:
    """A ``BinanceUSPublicClient`` stand-in with an editable, in-memory price history."""

    def __init__(
        self, closes_by_symbol: Mapping[str, Sequence[str]] | None = None,
        *, symbols: Sequence[str] = SYMBOLS, start_time: int = START, interval: str = "1d",
    ) -> None:
        self.closes_by_symbol: dict[str, list[str]] = {s: list(closes_by_symbol.get(s, [])) if closes_by_symbol else [] for s in symbols}
        self.symbols = tuple(symbols)
        self.start_time = start_time
        self.interval = interval
        self.exchange_info_calls = 0
        self.kline_calls = 0

    def add_day(self, prices: Sequence[str]) -> None:
        for symbol, price in zip(self.symbols, prices):
            self.closes_by_symbol[symbol].append(price)

    def exchange_info(self, symbols: Sequence[str] | None = None) -> ExchangeInfoResult:
        self.exchange_info_calls += 1
        wanted = symbols or self.symbols
        entries = [make_rule_entry(s, base=s[:-3], quote="USD", min_notional="1") for s in wanted]
        snapshots = tuple(parse_symbol_rules(entry, received_at=0, valid_from=0) for entry in entries)
        return ExchangeInfoResult(
            raw_payload={"symbols": entries}, snapshots=snapshots, server_time=0, received_at=0,
        )

    def iter_klines(
        self, symbol: str, interval: str, *, start_time: int, end_time: int, page_limit: int = 1000,
    ):
        self.kline_calls += 1
        bars = make_bars(
            symbol, self.closes_by_symbol.get(symbol, ()), start_time=self.start_time, interval=interval,
        )
        for bar in bars:
            if start_time <= bar.open_time <= end_time:
                yield bar

    def depth(self, symbol: str, limit: int = 100):
        return {
            "asks": [["100.00", "1"], ["100.50", "1"], ["101.00", "10"]],
            "bids": [["99.50", "1"], ["99.00", "5"]],
        }


class BrokenClient:
    """Raises on every call, to exercise the "the feed could not be reached" path."""

    def exchange_info(self, symbols=None):
        raise TransportError("simulated network failure")

    def iter_klines(self, *args, **kwargs):
        raise TransportError("simulated network failure")
        yield  # pragma: no cover - makes this a generator function

    def depth(self, symbol, limit=100):
        raise TransportError("simulated network failure")


def write_config(
    path: Path, template: Path, *, db_path: str, experiment_dir: str,
    start: str = "2024-01-01T00:00:00Z", end: str = "2024-01-02T00:00:00Z", **replacements: str,
) -> Path:
    """Copy an example config, pointing it at a temp db/experiment dir and a safe past window."""
    text = template.read_text(encoding="utf-8")
    text = text.replace('db_path = "data/paper_market.sqlite"', f'db_path = "{_forward(db_path)}"')
    text = text.replace('start = "2026-09-28T00:00:00Z"', f'start = "{start}"')
    text = text.replace('end = "2026-09-29T00:00:00Z"', f'end = "{end}"')
    text = text.replace("[experiment]", f'[experiment]\nexperiment_dir = "{_forward(experiment_dir)}"', 1)
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return path


def _forward(path: str) -> str:
    """Forward slashes only: a Windows backslash inside a TOML string is an escape sequence."""
    return path.replace("\\", "/")


CONFIGS_DIR = Path(__file__).parent.parent / "configs"
REHEARSAL_TEMPLATE = CONFIGS_DIR / "paper_rehearsal_example.toml"
BENCHMARK_TEMPLATE = CONFIGS_DIR / "paper_benchmark_example.toml"
