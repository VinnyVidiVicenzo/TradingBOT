"""Daily return series shared by the mix, savings and possible-futures studies.

Three sleeves on one calendar-day grid:

  crypto : a point-in-time "three biggest coins" basket. At the start of every
           month it holds the three Binance.US USDT pairs with the most trading
           over the PRIOR 90 days (no data from on or after the month start),
           equal-weighted, then held without touching until the next month.
           Changing members costs a 0.6% taker fee plus 0.1% for spread and
           slippage on every unit traded.
  stocks : SPY, dividend-adjusted, already net of the fund's own expenses.
           Flat on weekends and market holidays, when it does not trade.
  cash   : the 3-month US Treasury bill yield, accrued daily. A stand-in for a
           good savings account or money-market fund; real savings accounts
           usually pay a little less.

These analyses use floats rather than Decimals. The Decimal discipline in the
engine exists for ledger accounting -- cash balances, lot sizes, filters -- and
these are statistical summaries of return series, where float precision is far
below every other source of error.

Stock and rate data come from Yahoo Finance and are cached in data/reference/.
They are deliberately kept out of the Binance.US market store.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import DAY_MS, parse_time

REPO = Path(__file__).parent.parent
SPY_CACHE = REPO / "data/reference/spy_adjusted.csv"
TBILL_CACHE = REPO / "data/reference/tbill_3m.csv"
UNIVERSE_FILE = REPO / "results/usdt_universe.txt"
DB_PATH = REPO / "data/market.sqlite"

EXCLUDE = {"USDCUSDT", "USD1USDT", "USDUCUSDT", "PAXGUSDT"}
RANK_DAYS = 90
PICK = 3
CRYPTO_TRADE_COST = 0.006 + 0.001   # taker fee + spread/slippage, per unit traded

START = date(2020, 10, 1)
END = date(2026, 9, 25)             # last day with a stock-fund close


@dataclass
class Series:
    days: list[date]
    crypto: list[float]
    stocks: list[float]
    cash: list[float]
    crypto_members: dict[date, tuple[str, ...]]   # month start -> holdings


def _calendar(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _ms(d: date) -> int:
    return parse_time(d.isoformat())


def load_stocks(days: list[date]) -> list[float]:
    if not SPY_CACHE.exists():
        from vs_stock_fund import fetch_spy   # populates the cache

        fetch_spy()
    closes: dict[date, float] = {}
    for row in csv.DictReader(SPY_CACHE.open(encoding="utf-8")):
        closes[date.fromisoformat(row["date"])] = float(row["adjusted_close"])
    prev = None
    for d in sorted(closes):
        if d < days[0]:
            prev = closes[d]
    returns = []
    for d in days:
        if d in closes:
            returns.append(0.0 if prev is None else closes[d] / prev - 1)
            prev = closes[d]
        else:
            returns.append(0.0)
    return returns


def load_cash(days: list[date]) -> list[float]:
    yields: dict[date, float] = {}
    for row in csv.DictReader(TBILL_CACHE.open(encoding="utf-8")):
        yields[date.fromisoformat(row["date"])] = float(row["annual_yield_pct"])
    known = sorted(yields)
    returns, current, i = [], yields[known[0]], 0
    for d in days:
        while i < len(known) and known[i] <= d:
            current = yields[known[i]]
            i += 1
        returns.append(current / 100 / 365)
    return returns


def _yahoo_fallback_price(symbol: str) -> dict[int, float]:
    """Real-world daily closes for a symbol, for the rare day Binance.US has none.

    Found during review: Binance.US suspended XRPUSDT trading for 912 days
    (2021-01-13 to 2023-07-14) during the SEC lawsuit. XRP was legitimately held
    in the rotating basket when the gap opened (picked 2021-01-01), so for the
    rest of that month `closes[symbol].get(now)` returned None and the code
    below silently scored those days as a 0% return. XRP actually rose 61% in
    that window (Yahoo XRP-USD), so one month's return was understated by about
    20 percentage points -- not a rounding error, and it compounds into every
    multi-year path that includes that month. This pulls the real published
    price for exactly the days Binance.US has none, and only ever for a symbol
    actually held that day; it does not change which symbols get picked.
    """
    ticker = symbol[:-4] + "-USD"  # "XRPUSDT" -> "XRP-USD"
    cache = REPO / "data/reference/prices" / f"{ticker}.csv"
    if not cache.exists():
        import json
        import time as _time
        import urllib.request

        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
               "?period1=946684800&period2=1790640000&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                r = json.loads(resp.read().decode())["chart"]["result"][0]
        except Exception:
            return {}
        from datetime import datetime, timezone as _tz

        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value"])
            for t, v in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                if v is not None:
                    d = datetime.fromtimestamp(t, tz=_tz.utc).date()
                    w.writerow([d.isoformat(), v])
        _time.sleep(1)
    if not cache.exists():
        return {}
    return {_ms(date.fromisoformat(r["date"])): float(r["value"])
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def load_crypto(days: list[date]) -> tuple[list[float], dict[date, tuple[str, ...]]]:
    universe = [s for s in UNIVERSE_FILE.read_text().split() if s not in EXCLUDE]
    closes: dict[str, dict[int, float]] = {}
    volume: dict[str, dict[int, float]] = {}
    fallback: dict[str, dict[int, float]] = {}
    with open_database(DB_PATH) as db:
        repo = BarRepository(db)
        for symbol in universe:
            bars = repo.load(symbol, "1d", start_time=parse_time("2020-06-01"),
                             end_time=_ms(days[-1]))
            closes[symbol] = {b.open_time: float(b.close) for b in bars}
            volume[symbol] = {b.open_time: float(b.quote_volume or 0) for b in bars}

    def top(as_of: int) -> tuple[str, ...]:
        window = [as_of - k * DAY_MS for k in range(1, RANK_DAYS + 1)]
        scored = []
        for symbol in universe:
            vols = [volume[symbol][t] for t in window if t in volume[symbol]]
            if len(vols) >= RANK_DAYS:
                scored.append((sum(vols) / len(vols), symbol))
        scored.sort(key=lambda p: (-p[0], p[1]))
        return tuple(s for _, s in scored[:PICK])

    returns: list[float] = []
    members: dict[date, tuple[str, ...]] = {}
    weights: dict[str, float] = {}
    for d in days:
        now = _ms(d)
        cost = 0.0
        if d.day == 1 or not weights:
            chosen = top(now)
            members[d] = chosen
            target = {s: 1 / len(chosen) for s in chosen}
            names = set(target) | set(weights)
            traded = sum(abs(target.get(s, 0) - weights.get(s, 0)) for s in names)
            cost = traded * CRYPTO_TRADE_COST if weights else 1.0 * CRYPTO_TRADE_COST
            weights = target
        day_return = 0.0
        grown = {}
        for symbol, w in weights.items():
            today = closes[symbol].get(now)
            yesterday = closes[symbol].get(now - DAY_MS)
            if today is None or yesterday is None:
                # Binance.US has no bar for a symbol we are actually holding
                # (e.g. XRPUSDT during its 2021-2023 US delisting). Falling
                # back to 0% here understated one real month by ~20 points --
                # see _yahoo_fallback_price. Use the real published price for
                # exactly this symbol and this day instead of inventing "flat".
                if symbol not in fallback:
                    fallback[symbol] = _yahoo_fallback_price(symbol)
                today = today if today is not None else fallback[symbol].get(now)
                yesterday = yesterday if yesterday is not None else fallback[symbol].get(now - DAY_MS)
            r = today / yesterday - 1 if today and yesterday else 0.0
            day_return += w * r
            grown[symbol] = w * (1 + r)
        total = sum(grown.values())
        weights = {s: v / total for s, v in grown.items()} if total > 0 else weights
        returns.append((1 + day_return) * (1 - cost) - 1)
    return returns, members


def build(start: date = START, end: date = END) -> Series:
    days = _calendar(start, end)
    crypto, members = load_crypto(days)
    return Series(days=days, crypto=crypto, stocks=load_stocks(days),
                  cash=load_cash(days), crypto_members=members)


# --------------------------------------------------------------------------
# portfolios
# --------------------------------------------------------------------------

MIXES: dict[str, dict[str, float]] = {
    "Savings account only":      {"cash": 1.0},
    "Savings 90% + crypto 10%":  {"cash": 0.9, "crypto": 0.1},
    "Stock fund only":           {"stocks": 1.0},
    "Fund 95% + crypto 5%":      {"stocks": 0.95, "crypto": 0.05},
    "Fund 90% + crypto 10%":     {"stocks": 0.9, "crypto": 0.1},
    "Fund 80% + crypto 20%":     {"stocks": 0.8, "crypto": 0.2},
    "Crypto only":               {"crypto": 1.0},
}


def simulate(mix: dict[str, float], crypto: list[float], stocks: list[float],
             cash: list[float], stake: float = 200.0,
             rebalance_every: int = 365) -> list[float]:
    """Portfolio value each day, rebalanced back to target once a year.

    Rebalancing needs selling, which the engine deliberately cannot do, so this
    is an analysis of return series rather than a simulation through the engine.
    Crypto trades pay CRYPTO_TRADE_COST; the stock fund and cash trade free,
    which is roughly true at most brokers today.
    """
    sleeves = {k: stake * w for k, w in mix.items()}
    if "crypto" in sleeves:
        sleeves["crypto"] *= 1 - CRYPTO_TRADE_COST
    values = []
    series = {"crypto": crypto, "stocks": stocks, "cash": cash}
    for i in range(len(crypto)):
        for k in sleeves:
            sleeves[k] *= 1 + series[k][i]
        total = sum(sleeves.values())
        if rebalance_every and i > 0 and i % rebalance_every == 0 and len(mix) > 1:
            target = {k: total * w for k, w in mix.items()}
            if "crypto" in mix:
                traded = abs(target["crypto"] - sleeves["crypto"])
                total -= traded * CRYPTO_TRADE_COST
                target = {k: total * w for k, w in mix.items()}
            sleeves = target
        values.append(sum(sleeves.values()))
    return values


def pain(values: list[float], stake: float = 200.0) -> dict[str, float]:
    peak, worst = values[0], 0.0
    longest = run = 0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak if peak else 0)
        if v < stake:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return {
        "end": values[-1],
        "worst_drop": worst,
        "share_in_red": sum(1 for v in values if v < stake) / len(values),
        "longest_in_red": longest,
    }
