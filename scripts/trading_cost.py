"""Analyst step: what does it really cost to buy and sell on Binance.US?

Reads the live order book -- the list of people waiting to buy and sell at each
price -- for each coin, and works out:

  gap         the difference between the cheapest seller and the best buyer,
              which you lose immediately if you buy and then sell straight back
  cost to buy how far above the middle price you would pay, on average, for an
              order of $200, $1,000, $10,000 and $50,000, by walking up the list
              of sellers until the order is filled
  depth       how much is on offer within 1% of the middle price

The exchange's trading fee comes on top of all of this and is not included.

Taken as three snapshots about 20 seconds apart and averaged. That is one
moment on one day: books are thinner at night, at weekends and in a crash,
exactly when you would most want to sell.

Read-only public data; uses the project's own GET-only Binance.US client.

    python scripts/trading_cost.py
"""

from __future__ import annotations

import csv
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.binanceus.public_client import BinanceUSPublicClient

COINS = ["BTC", "ETH", "XRP", "SOL", "ZEC", "DOGE", "BNB"]
QUOTES = ["USD", "USDT"]
SIZES = [200, 1_000, 10_000, 50_000]
SNAPSHOTS = 3


def walk(levels: list[tuple[float, float]], dollars: float) -> float | None:
    """Average price paid to fill `dollars`, or None if the book runs out."""
    remaining, cost, qty = dollars, 0.0, 0.0
    for price, size in levels:
        take = min(size, remaining / price)
        cost += take * price
        qty += take
        remaining -= take * price
        if remaining <= 1e-9:
            return cost / qty
    return None


def main() -> None:
    client = BinanceUSPublicClient()
    listed = {s.symbol for s in client.exchange_info().snapshots if s.is_trading}
    pairs = [c + q for c in COINS for q in QUOTES if c + q in listed]

    results: dict[str, list[dict]] = {p: [] for p in pairs}
    for snap in range(SNAPSHOTS):
        for pair in pairs:
            book = client.depth(pair, limit=500)
            asks = [(float(p), float(q)) for p, q in book["asks"]]
            bids = [(float(p), float(q)) for p, q in book["bids"]]
            if not asks or not bids:
                continue
            mid = (asks[0][0] + bids[0][0]) / 2
            row = {"gap": (asks[0][0] - bids[0][0]) / mid,
                   "depth_1pct": sum(p * q for p, q in asks if p <= mid * 1.01)
                                 + sum(p * q for p, q in bids if p >= mid * 0.99)}
            for size in SIZES:
                avg = walk(asks, size)
                row[f"buy_{size}"] = None if avg is None else avg / mid - 1
                avg_s = walk(bids, size)
                row[f"sell_{size}"] = None if avg_s is None else 1 - avg_s / mid
            results[pair].append(row)
        if snap < SNAPSHOTS - 1:
            time.sleep(20)

    out_rows = []
    print("Cost of crossing the market on Binance.US, excluding the trading fee.")
    print("Average of 3 live snapshots. 'too big' = not enough on offer to fill it.\n")
    header = f"{'pair':<9}{'gap':>8}" + "".join(f"{'buy $'+format(s, ','):>12}" for s in SIZES) \
             + f"{'on offer within 1%':>21}"
    print(header)
    print("-" * len(header))
    for pair in pairs:
        snaps = results[pair]
        if not snaps:
            continue
        def mean_of(key):
            vals = [s[key] for s in snaps if s[key] is not None]
            return statistics.mean(vals) if len(vals) == len(snaps) else None
        gap = mean_of("gap")
        cells = []
        rec = {"pair": pair, "gap": gap, "depth_1pct": mean_of("depth_1pct")}
        for size in SIZES:
            b = mean_of(f"buy_{size}")
            rec[f"buy_{size}"] = b
            rec[f"sell_{size}"] = mean_of(f"sell_{size}")
            cells.append("too big" if b is None else f"{b * 100:.2f}%")
        out_rows.append(rec)
        print(f"{pair:<9}{gap * 100:>7.2f}%" + "".join(f"{c:>12}" for c in cells)
              + f"{'$' + format(rec['depth_1pct'], ',.0f'):>21}")

    path = Path(__file__).parent.parent / "results/trading_cost.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nwritten to {path}")


if __name__ == "__main__":
    main()
