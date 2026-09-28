"""Was the crypto worth it, compared with a plain stock-market fund?

Puts $200 into each on the same day, holds for the same length of time, and
compares outcome AND pain across every start month available.

  crypto : the three most-traded Binance.US USDT pairs as of the start date,
           chosen using only data from before that date, bought in one go.
           Trading fees, spread and slippage all applied.
  stocks : SPY, the oldest S&P 500 index fund, bought in one go and held.
           Dividend-adjusted closes, which are already net of the fund's own
           expenses, so no extra fee needs subtracting.

Honest wrinkles, all disclosed rather than smoothed over:

* The crypto figures are USDT, the fund is US dollars. Over the window we can
  measure, USDT ranged 0.9715 to 1.0176, so equating them is off by up to 2.85%.
  That is small next to the differences here but it is not zero, and USDT is
  never silently treated as a dollar elsewhere in this project.
* Stocks trade about 252 days a year, crypto 365. Each side is measured on its
  own trading days over the same calendar window.
* SPY price data comes from Yahoo Finance, not Binance.US. It is cached to
  data/reference/ and deliberately kept OUT of the Binance.US market store, so
  that store stays exactly what it claims to be.

    python scripts/vs_stock_fund.py
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.backtest.costs import (
    CostModels,
    FeeSchedule,
    FixedSlippageModel,
    FixedSpreadModel,
    FlatFeeModel,
    LatencyModel,
)
from crypto_research.backtest.engine import BacktestConfig, Backtester
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.binanceus.filters import SymbolRuleBook
from crypto_research.datafeed.loader import load_dataset
from crypto_research.decimals import ZERO, dec, div
from crypto_research.errors import ConfigError
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository, RuleSnapshotRepository
from crypto_research.strategies import build_strategy
from crypto_research.timeutil import DAY_MS, add_months, parse_time, to_iso

EXCLUDE = {"USDCUSDT", "USD1USDT", "USDUCUSDT", "PAXGUSDT"}
RANK_DAYS, PICK, STAKE = 90, 3, dec("200")
HORIZONS = (12, 24, 36, 60)
DATA_START, DATA_END = "2020-10-01", "2026-09-26"
SPY_CACHE = Path("data/reference/spy_adjusted.csv")

COSTS = CostModels(
    fees=FlatFeeModel(
        schedule=FeeSchedule(maker_rate=dec("0.0040"), taker_rate=dec("0.0060"))
    ),
    spread=FixedSpreadModel(default_bps=dec("5"), prefer_observed=True),
    slippage=FixedSlippageModel(bps=dec("5")),
    latency=LatencyModel(decision_to_submit_ms=500),
)


# --------------------------------------------------------------------------
# the stock fund
# --------------------------------------------------------------------------


def fetch_spy() -> list[tuple[int, Decimal]]:
    """Dividend-adjusted SPY closes, cached after the first fetch."""
    if SPY_CACHE.exists():
        rows = list(csv.DictReader(SPY_CACHE.open(encoding="utf-8")))
        return [(int(r["time"]), dec(r["adjusted_close"])) for r in rows]

    start = int(parse_time(DATA_START) / 1000)
    end = int(parse_time("2026-09-28") / 1000)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/SPY"
        f"?period1={start}&period2={end}&interval=1d&events=div%7Csplit"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    adjusted = result["indicators"]["adjclose"][0]["adjclose"]

    series: list[tuple[int, Decimal]] = []
    for stamp, value in zip(stamps, adjusted):
        if value is None:
            continue
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).date()
        series.append((parse_time(day.isoformat()), dec(repr(round(value, 6)))))

    SPY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with SPY_CACHE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "date", "adjusted_close"])
        for stamp, value in series:
            writer.writerow([stamp, to_iso(stamp)[:10], value])
    return series


def hold_stocks(series: list[tuple[int, Decimal]], start: int, end: int) -> dict | None:
    window = [(t, v) for t, v in series if start <= t <= end]
    if len(window) < 30:
        return None
    entry = window[0][1]
    units = div(STAKE, entry)
    values = [units * v for _, v in window]
    return risk_facts(values)


# --------------------------------------------------------------------------
# shared measurement
# --------------------------------------------------------------------------


def risk_facts(values: list[Decimal]) -> dict:
    peak = values[0]
    worst_fall = ZERO
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst_fall = max(worst_fall, div(peak - value, peak))
    low = min(values)
    longest, run = 0, 0
    for value in values:
        if value < STAKE:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return {
        "end": float(values[-1]),
        "worst_fall_from_peak": float(worst_fall),
        "worst_vs_stake": float(div(STAKE - low, STAKE)) if low < STAKE else 0.0,
        "share_underwater": sum(1 for v in values if v < STAKE) / len(values),
        "longest_underwater_days": longest,
    }


def pick_top(volume: dict[str, dict[int, Decimal]], as_of: int) -> list[str]:
    window = [as_of - d * DAY_MS for d in range(1, RANK_DAYS + 1)]
    scored = []
    for symbol, history in volume.items():
        vols = [history[t] for t in window if t in history]
        if len(vols) >= RANK_DAYS:
            scored.append((sum(vols) / Decimal(len(vols)), symbol))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [symbol for _, symbol in scored[:PICK]]


def main() -> None:
    spy = fetch_spy()
    print(f"SPY: {len(spy)} trading days, "
          f"{to_iso(spy[0][0])[:10]} to {to_iso(spy[-1][0])[:10]}\n")

    universe = [s for s in Path("results/usdt_universe.txt").read_text().split()
                if s not in EXCLUDE]

    rows = []
    with open_database("data/market.sqlite") as db:
        repo = BarRepository(db)
        volume = {}
        for symbol in universe:
            bars = repo.load(symbol, "1d", start_time=parse_time("2020-06-01"),
                             end_time=parse_time(DATA_END))
            volume[symbol] = {b.open_time: (b.quote_volume or ZERO) for b in bars}
        rule_book = SymbolRuleBook(RuleSnapshotRepository(db).latest())

        for horizon in HORIZONS:
            start = parse_time(DATA_START)
            while add_months(start, horizon) <= parse_time(DATA_END):
                end = add_months(start, horizon)
                stocks = hold_stocks(spy, start, end)
                chosen = pick_top(volume, start)
                try:
                    dataset = load_dataset(db, chosen, "1d",
                                           start_time=start, end_time=end)
                except ConfigError:
                    start = add_months(start, 1)
                    continue
                if stocks is None:
                    start = add_months(start, 1)
                    continue

                config = BacktestConfig(
                    start_time=start, end_time=end, valuation_asset="USDT",
                    initial_cash={"USDT": STAKE}, costs=COSTS,
                    execution=ExecutionConfig(max_reference_age_ms=90_000),
                )
                strategy = build_strategy(
                    "benchmark1_staged_deployment",
                    {"symbols": chosen, "quote_asset": "USDT",
                     "tranche_count": 1, "cadence_days": 0},
                )
                result = Backtester(dataset=dataset, rule_book=rule_book,
                                    config=config).run(strategy)
                crypto = risk_facts([p.total_value for p in result.equity_curve])

                rows.append({"horizon": horizon, "start": to_iso(start)[:10],
                             "crypto": crypto, "stocks": stocks})
                start = add_months(start, 1)

    report(rows)
    out = Path("results/crypto_vs_stocks.csv")
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["horizon", "start", "side", "end", "worst_fall_from_peak",
                         "worst_vs_stake", "share_underwater", "longest_underwater_days"])
        for row in rows:
            for side in ("crypto", "stocks"):
                f = row[side]
                writer.writerow([row["horizon"], row["start"], side, f["end"],
                                 f["worst_fall_from_peak"], f["worst_vs_stake"],
                                 f["share_underwater"], f["longest_underwater_days"]])
    print(f"\n\nall {len(rows) * 2} results written to {out}")


def report(rows: list[dict]) -> None:
    print("$200 into each on the same day. Every start month. Crypto pays its")
    print("trading costs; the fund's expenses are already inside its prices.\n")

    print("WHAT YOU ENDED UP WITH\n")
    print(f"{'held':<11}{'starts':>7}{'crypto typical':>16}{'fund typical':>14}"
          f"{'crypto won':>12}{'crypto lost $':>15}{'fund lost $':>13}")
    print("-" * 88)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        c = sorted(r["crypto"]["end"] for r in group)
        s = sorted(r["stocks"]["end"] for r in group)
        won = sum(1 for r in group if r["crypto"]["end"] > r["stocks"]["end"])
        cl = sum(1 for r in group if r["crypto"]["end"] < 200)
        sl = sum(1 for r in group if r["stocks"]["end"] < 200)
        print(f"{f'{horizon} months':<11}{len(group):>7}{statistics.median(c):>16,.0f}"
              f"{statistics.median(s):>14,.0f}{f'{won}/{len(group)}':>12}"
              f"{f'{cl}/{len(group)}':>15}{f'{sl}/{len(group)}':>13}")

    print("\n\nWHAT YOU WENT THROUGH TO GET IT\n")
    print(f"{'held':<11}{'worst drop crypto':>19}{'worst drop fund':>17}"
          f"{'days in red crypto':>20}{'days in red fund':>18}")
    print("-" * 85)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        cw = max(r["crypto"]["worst_fall_from_peak"] for r in group)
        sw = max(r["stocks"]["worst_fall_from_peak"] for r in group)
        cu = statistics.median(r["crypto"]["share_underwater"] for r in group)
        su = statistics.median(r["stocks"]["share_underwater"] for r in group)
        print(f"{f'{horizon} months':<11}{f'-{cw*100:.0f}%':>19}{f'-{sw*100:.0f}%':>17}"
              f"{f'{cu*100:.0f}%':>20}{f'{su*100:.0f}%':>18}")

    print("\n\nLONGEST UNBROKEN STRETCH BELOW WHAT YOU PUT IN\n")
    print(f"{'held':<11}{'crypto typical':>16}{'crypto worst':>15}"
          f"{'fund typical':>15}{'fund worst':>13}")
    print("-" * 70)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        c = sorted(r["crypto"]["longest_underwater_days"] for r in group)
        s = sorted(r["stocks"]["longest_underwater_days"] for r in group)
        print(f"{f'{horizon} months':<11}{f'{statistics.median(c):.0f}d':>16}{f'{c[-1]}d':>15}"
              f"{f'{statistics.median(s):.0f}d':>15}{f'{s[-1]}d':>13}")

    print("\n\nRETURN PER UNIT OF PAIN (ending gain divided by worst drop)\n")
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        def ratio(side):
            vals = []
            for r in group:
                gain = r[side]["end"] - 200
                drop = r[side]["worst_fall_from_peak"]
                if drop > 0.01:
                    vals.append(gain / (drop * 200))
            return statistics.median(vals) if vals else float("nan")
        print(f"  held {horizon:>2} months:  crypto {ratio('crypto'):+.2f}"
              f"   fund {ratio('stocks'):+.2f}")


if __name__ == "__main__":
    main()
