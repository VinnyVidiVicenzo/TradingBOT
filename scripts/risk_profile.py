"""How bad does it get, how long do you wait, and what if you panic?

Three questions the return numbers do not answer:

1. How far down would you have been, and for how long?
2. After the worst point, how long until you were back to even?
3. What if you had sold after a big fall, as most people do?

Coins are picked blind at each start date -- the three most-traded pairs over the
prior 90 days, using no data from on or after that date -- so nothing here is
flattered by knowing which coins turned out well.

The panic case is computed from the results rather than simulated. The engine
deliberately has no sell path (see docs/trading-bot/SAFETY.md) and weakening
that to answer a what-if would be the wrong trade. The approximation: you sell
at the close on the day the fall is first visible, pay one taker fee, and hold
cash for the rest of the period.

    python scripts/risk_profile.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from decimal import Decimal
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on the path, not the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.backtest.costs import (
    CostModels,
    FeeSchedule,
    FixedSlippageModel,
    FixedSpreadModel,
    FlatFeeModel,
    LatencyModel,
)
from crypto_research.backtest.engine import BacktestConfig, Backtester, EquityPoint
from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.binanceus.filters import SymbolRuleBook
from crypto_research.datafeed.loader import load_dataset
from crypto_research.decimals import ZERO, dec, div
from crypto_research.errors import ConfigError
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository, RuleSnapshotRepository
from crypto_research.strategies import build_strategy
from crypto_research.timeutil import DAY_MS, add_months, parse_time, to_iso

# Pegged to a dollar or to gold: holding these is not a crypto bet.
EXCLUDE = {"USDCUSDT", "USD1USDT", "USDUCUSDT", "PAXGUSDT"}
RANK_DAYS = 90
PICK = 3
STAKE = dec("200")
HORIZONS = (12, 24, 36, 60)
DATA_START = "2020-10-01"
DATA_END = "2026-09-01"

# The panic threshold and the exit cost, declared up front.
PANIC_AT = dec("0.50")
SELL_FEE = dec("0.006")

COSTS = CostModels(
    fees=FlatFeeModel(
        schedule=FeeSchedule(maker_rate=dec("0.0040"), taker_rate=dec("0.0060"))
    ),
    spread=FixedSpreadModel(default_bps=dec("5"), prefer_observed=True),
    slippage=FixedSlippageModel(bps=dec("5")),
    latency=LatencyModel(decision_to_submit_ms=500),
)


def load_universe() -> list[str]:
    path = Path("results/usdt_universe.txt")
    return [s for s in path.read_text().split() if s not in EXCLUDE]


def volume_history(repo: BarRepository, universe: list[str]) -> dict[str, dict[int, Decimal]]:
    out: dict[str, dict[int, Decimal]] = {}
    for symbol in universe:
        bars = repo.load(
            symbol, "1d",
            start_time=parse_time("2020-06-01"), end_time=parse_time(DATA_END),
        )
        out[symbol] = {b.open_time: (b.quote_volume or ZERO) for b in bars}
    return out


def pick_top(volume: dict[str, dict[int, Decimal]], as_of: int) -> list[str]:
    """Most-traded coins over the 90 days BEFORE as_of. No future data."""
    window = [as_of - d * DAY_MS for d in range(1, RANK_DAYS + 1)]
    scored: list[tuple[Decimal, str]] = []
    for symbol, history in volume.items():
        vols = [history[t] for t in window if t in history]
        if len(vols) < RANK_DAYS:
            continue
        scored.append((sum(vols) / Decimal(len(vols)), symbol))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [symbol for _, symbol in scored[:PICK]]


def analyse(curve: list[EquityPoint], stake: Decimal) -> dict[str, object]:
    values = [point.total_value for point in curve]
    times = [point.time for point in curve]

    peak = values[0]
    worst_fall_from_peak = ZERO
    panic_value: Decimal | None = None
    for moment, value in zip(times, values):
        peak = max(peak, value)
        if peak > 0:
            fall = div(peak - value, peak)
            worst_fall_from_peak = max(worst_fall_from_peak, fall)
            if panic_value is None and fall >= PANIC_AT:
                panic_value = value * (Decimal(1) - SELL_FEE)

    low = min(values)

    # The longest unbroken run of days below the stake. This is the number that
    # matters to a person: how long you would have had to hold your nerve.
    # An earlier version measured from the single lowest day to the next
    # break-even, which flatters any window whose low happens to fall late.
    longest_underwater = 0
    current_run = 0
    for value in values:
        if value < stake:
            current_run += 1
            longest_underwater = max(longest_underwater, current_run)
        else:
            current_run = 0

    return {
        "end": float(values[-1]),
        "low": float(low),
        "worst_fall_from_peak": float(worst_fall_from_peak),
        "worst_vs_stake": float(div(stake - low, stake)) if low < stake else 0.0,
        "days_underwater": sum(1 for v in values if v < stake),
        "days_held": len(values),
        "longest_underwater_days": longest_underwater,
        "still_down_at_end": values[-1] < stake,
        "panic_end": float(panic_value if panic_value is not None else values[-1]),
        "panicked": panic_value is not None,
    }


def main() -> None:
    universe = load_universe()
    with open_database("data/market.sqlite") as db:
        volume = volume_history(BarRepository(db), universe)
        rule_book = SymbolRuleBook(RuleSnapshotRepository(db).latest())

        rows: list[dict[str, object]] = []
        for horizon in HORIZONS:
            start = parse_time(DATA_START)
            while add_months(start, horizon) <= parse_time(DATA_END):
                end = add_months(start, horizon)
                chosen = pick_top(volume, start)
                try:
                    dataset = load_dataset(
                        db, chosen, "1d", start_time=start, end_time=end
                    )
                except ConfigError:
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
                result = Backtester(
                    dataset=dataset, rule_book=rule_book, config=config
                ).run(strategy)

                info = analyse(result.equity_curve, STAKE)
                info.update(
                    horizon=horizon, start=to_iso(start)[:7],
                    picked=" ".join(c[:-4] for c in chosen),
                )
                rows.append(info)
                start = add_months(start, 1)

    out = Path("results/risk_profile.csv")
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report(rows)
    print(f"\nall {len(rows)} runs written to {out}")


def report(rows: list[dict[str, object]]) -> None:
    print("A one-off $200, coins picked blind, no clever timing.\n")
    print(f"{'held for':<12}{'starts':>8}{'typical end':>13}{'lost money':>12}"
          f"{'fell >50%':>11}{'worst point':>13}")
    print("-" * 69)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        ends = sorted(float(r["end"]) for r in group)
        lost = sum(1 for r in group if float(r["end"]) < 200)
        big = sum(1 for r in group if float(r["worst_fall_from_peak"]) >= 0.5)
        worst = max(float(r["worst_vs_stake"]) for r in group)
        print(f"{f'{horizon} months':<12}{len(group):>8}{ends[len(ends)//2]:>13,.0f}"
              f"{f'{lost}/{len(group)}':>12}{f'{big}/{len(group)}':>11}"
              f"{f'-{worst*100:.0f}%':>13}")

    print("\n\nTIME SPENT IN THE RED (below what you put in)\n")
    print(f"{'held for':<12}{'typical':>10}{'worst case':>13}")
    print("-" * 35)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        share = sorted(int(r["days_underwater"]) / int(r["days_held"]) for r in group)
        print(f"{f'{horizon} months':<12}{share[len(share)//2]*100:>9.0f}%"
              f"{share[-1]*100:>12.0f}%")

    print("\n\nLONGEST UNBROKEN STRETCH IN THE RED -- how long you hold your nerve\n")
    print(f"{'held for':<12}{'typical':>12}{'worst case':>14}{'still down at end':>20}")
    print("-" * 58)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        stretches = sorted(int(r["longest_underwater_days"]) for r in group)
        still = sum(1 for r in group if r["still_down_at_end"])
        print(f"{f'{horizon} months':<12}"
              f"{f'{stretches[len(stretches)//2]} days':>12}"
              f"{f'{stretches[-1]} days':>14}{f'{still}/{len(group)}':>20}")

    print("\n\nWHY THE LONGER HOLDS ARE NOT A FAIR COMPARISON\n")
    print("Each holding length can only use start dates that leave room to finish,")
    print("so the samples cover different eras and overlap heavily. The 5-year rows")
    print("all begin inside the 2020-21 run-up; the 3-year rows are full of starts")
    print("in the 2022 crash, when everything was cheap. With six years of data")
    print("this cannot be fixed, only disclosed -- do not read these as 'three")
    print("years is safer than five'.\n")
    print(f"{'held for':<12}{'starts':>8}{'first start':>14}{'last start':>13}")
    print("-" * 47)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            continue
        starts = sorted(str(r["start"]) for r in group)
        print(f"{f'{horizon} months':<12}{len(group):>8}{starts[0]:>14}{starts[-1]:>13}")

    print("\n\nWHAT IF YOU PANIC-SOLD AFTER A 50% FALL\n")
    print(f"{'held for':<12}{'hit the trigger':>17}{'held on':>10}{'sold':>10}{'cost of selling':>18}")
    print("-" * 67)
    for horizon in HORIZONS:
        group = [r for r in rows if r["horizon"] == horizon and r["panicked"]]
        allg = [r for r in rows if r["horizon"] == horizon]
        if not allg:
            continue
        if not group:
            print(f"{f'{horizon} months':<12}{f'0/{len(allg)}':>17}{'--':>10}{'--':>10}{'--':>18}")
            continue
        held = statistics.mean(float(r["end"]) for r in group)
        sold = statistics.mean(float(r["panic_end"]) for r in group)
        print(f"{f'{horizon} months':<12}{f'{len(group)}/{len(allg)}':>17}"
              f"{held:>10,.0f}{sold:>10,.0f}{sold - held:>+18,.0f}")


if __name__ == "__main__":
    main()
