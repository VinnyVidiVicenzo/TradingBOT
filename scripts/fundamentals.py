"""Analyst step 1: what is underneath each coin?

For BTC, ETH, XRP, SOL, ZEC, DOGE and BNB:

  usage   daily active addresses and transactions (Coin Metrics community data)
          -- now versus one and three years ago
  supply  how many coins exist, how fast new ones are created, and how close
          the coin is to any hard cap (CoinGecko + Coin Metrics)
  value   market value per active address, now versus before -- a crude
          "is the price running ahead of the use?" gauge
  health  distance from all-time high (CoinGecko)

Honest limits, stated because they change how the numbers should be read:
  * An "active address" is not a person. One person can use many; an exchange
    uses a few for millions of customers. Trends mean more than levels, and
    comparing levels across coins with different designs is unreliable.
  * Ethereum activity has moved heavily onto "layer 2" networks built on top of
    it, which are not counted here. Ethereum usage is understated.
  * Solana and BNB have no free usage history in these sources (Coin Metrics
    lists BNB but returns no rows). Developer activity is no longer in
    CoinGecko's free tier, so it is not reported.
  * XRP: all 100 billion coins already exist. The part not "in circulation" is
    locked in escrow by Ripple, the company, and released over time -- so
    "share of cap issued" does not mean the same thing as for Bitcoin.
  * Free data only. Paid sources measure more (holder concentration, fees,
    real user estimates).

Raw responses cached in data/reference/fundamentals/.

    python scripts/fundamentals.py
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from crypto_research.http_policy import no_redirect_opener, pagination_url

OUT = Path(__file__).parent.parent / "data/reference/fundamentals"
RESULTS = Path(__file__).parent.parent / "results"

COINS = {  # our symbol: (CoinGecko id, Coin Metrics id or None)
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "eth"),
    "XRP": ("ripple", "xrp"),
    "SOL": ("solana", None),
    "ZEC": ("zcash", "zec"),
    "DOGE": ("dogecoin", "doge"),
    "BNB": ("binancecoin", None),
}
TODAY = date(2026, 9, 26)


def get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)",
                                               "Accept": "application/json"})
    for attempt in range(5):
        try:
            with no_redirect_opener().open(req, timeout=60) as r:
                body = r.read(5_000_001)
                if len(body) > 5_000_000:
                    raise ValueError("reference response too large")
                return json.loads(body.decode())
        except urllib.error.HTTPError as exc:
            # Free tiers rate-limit; waiting is the polite and correct response.
            if exc.code != 429 or attempt == 4:
                raise
            time.sleep(30 * (attempt + 1))
    raise RuntimeError("unreachable")


def coingecko(coin_id: str) -> dict:
    cache = OUT / f"coingecko_{coin_id}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    data = get(f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false"
               "&tickers=false&market_data=true&community_data=false&developer_data=true")
    cache.write_text(json.dumps(data, indent=1), encoding="utf-8")
    time.sleep(6)  # stay well inside the free rate limit
    return data


def coinmetrics(asset: str) -> list[dict]:
    cache = OUT / f"coinmetrics_{asset}.csv"
    if cache.exists():
        return list(csv.DictReader(cache.open(encoding="utf-8")))
    rows, url = [], (
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        f"?assets={asset}&metrics=AdrActCnt,TxCnt,SplyCur,CapMrktCurUSD"
        "&frequency=1d&start_time=2020-01-01&page_size=10000"
    )
    seen = set()
    while url:
        if url in seen or len(seen) >= 100:
            raise ValueError("pagination repeated or exceeded 100 pages")
        seen.add(url)
        page = get(url)
        rows.extend(page["data"])
        url = pagination_url(page.get("next_page_url"), url, host="community-api.coinmetrics.io")
    with cache.open("w", newline="", encoding="utf-8") as fh:
        fields = ["time", "AdrActCnt", "TxCnt", "SplyCur", "CapMrktCurUSD"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r["time"] = r["time"][:10]
            w.writerow(r)
    return list(csv.DictReader(cache.open(encoding="utf-8")))


def avg(rows: list[dict], field: str, end: date, days: int = 90) -> float | None:
    start = end - timedelta(days=days)
    vals = [float(r[field]) for r in rows
            if r.get(field) and start < date.fromisoformat(r["time"]) <= end]
    return statistics.mean(vals) if vals else None


def value_at(rows: list[dict], field: str, when: date) -> float | None:
    best = None
    for r in rows:
        if r.get(field) and date.fromisoformat(r["time"]) <= when:
            best = float(r[field])
    return best


def change(now: float | None, then: float | None) -> str:
    if not now or not then:
        return "n/a"
    return f"{(now / then - 1) * 100:+.0f}%"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    for sym, (cg_id, cm_id) in COINS.items():
        cg = coingecko(cg_id)
        md = cg["market_data"]
        dev = cg.get("developer_data") or {}
        row = {
            "coin": sym,
            "started": cg.get("genesis_date") or "n/a",
            "price": md["current_price"]["usd"],
            "market_cap_bn": md["market_cap"]["usd"] / 1e9,
            "below_ath": md["ath_change_percentage"]["usd"],
            "ath_date": (md.get("ath_date") or {}).get("usd", "")[:10],
            "circulating": md.get("circulating_supply"),
            "max_supply": md.get("max_supply"),
            "commits_4w": dev.get("commit_count_4_weeks"),
        }
        if cm_id:
            cm = coinmetrics(cm_id)
            one, three = TODAY - timedelta(days=365), TODAY - timedelta(days=3 * 365)
            addr_now, addr_1y, addr_3y = (avg(cm, "AdrActCnt", d) for d in (TODAY, one, three))
            tx_now, tx_1y, tx_3y = (avg(cm, "TxCnt", d) for d in (TODAY, one, three))
            sup_now, sup_1y = value_at(cm, "SplyCur", TODAY), value_at(cm, "SplyCur", one)
            cap_now, cap_3y = avg(cm, "CapMrktCurUSD", TODAY), avg(cm, "CapMrktCurUSD", three)
            row.update(
                active_addresses=addr_now, addr_vs_1y=change(addr_now, addr_1y),
                addr_vs_3y=change(addr_now, addr_3y),
                transactions=tx_now, tx_vs_1y=change(tx_now, tx_1y), tx_vs_3y=change(tx_now, tx_3y),
                new_supply_per_year=(sup_now / sup_1y - 1) * 100 if sup_now and sup_1y else None,
                value_per_address=cap_now / addr_now if cap_now and addr_now else None,
                value_per_address_3y=cap_3y / addr_3y if cap_3y and addr_3y else None,
            )
        report.append(row)

    RESULTS.mkdir(exist_ok=True)
    fields = sorted({k for r in report for k in r})
    with (RESULTS / "fundamentals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(report)

    fmt_n = lambda v: "n/a" if v is None else f"{v:,.0f}"
    print("USAGE (90-day averages; 'address' != person, trends matter more than levels)\n")
    print(f"{'coin':<6}{'active addr/day':>16}{'vs 1y':>8}{'vs 3y':>8}{'tx/day':>14}{'vs 1y':>8}{'vs 3y':>8}")
    for r in report:
        print(f"{r['coin']:<6}{fmt_n(r.get('active_addresses')):>16}{r.get('addr_vs_1y','n/a'):>8}"
              f"{r.get('addr_vs_3y','n/a'):>8}{fmt_n(r.get('transactions')):>14}"
              f"{r.get('tx_vs_1y','n/a'):>8}{r.get('tx_vs_3y','n/a'):>8}")

    print("\nSUPPLY\n")
    print(f"{'coin':<6}{'started':>12}{'coins in circulation':>22}{'hard cap':>18}"
          f"{'% of cap issued':>17}{'new coins / yr':>16}")
    for r in report:
        cap, circ = r["max_supply"], r["circulating"]
        share = f"{circ / cap * 100:.0f}%" if cap and circ else "no cap"
        if r["coin"] == "XRP":
            share += "*"
        infl = r.get("new_supply_per_year")
        print(f"{r['coin']:<6}{r['started']:>12}{fmt_n(circ):>22}{fmt_n(cap) if cap else 'none':>18}"
              f"{share:>17}{'n/a' if infl is None else f'{infl:+.2f}%':>16}")

    print("\nVALUE vs USE AND HEALTH\n")
    print(f"{'coin':<6}{'market value':>14}{'$ per active addr':>19}{'3 yrs ago':>12}"
          f"{'below all-time high':>21}")
    for r in report:
        print(f"{r['coin']:<6}{r['market_cap_bn']:>12.0f}bn{fmt_n(r.get('value_per_address')):>19}"
              f"{fmt_n(r.get('value_per_address_3y')):>12}"
              f"{r['below_ath']:>+15.0f}% ({r['ath_date'][:7]})")


if __name__ == "__main__":
    main()
