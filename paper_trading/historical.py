"""Candle-only pretraining and purged chronological evaluation, isolated from practice.

Historical candles cannot reproduce spreads, flow or fills. These models have
their own feature version and immutable inputs; no live learner is overwritten.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.decimals import dec, format_decimal
from crypto_research.jsonl import append_record, locked
from crypto_research.experiments.runner import source_identity
from crypto_research.market_universe import SYMBOLS
from crypto_research.models import EXCHANGE_BINANCE_US
from crypto_research.timeutil import now_ms
from . import research_costs

HOUR = 3_600_000
VERSION = "historical-candle-logistic-v1"
FEATURES = ("bias", "trend_20_50", "volatility_20", "volume_20", "return_1", "return_4",
            "return_24", "hour_range", *SYMBOLS)
LIMITATIONS = ("Candle-only hypothetical opportunities, not portfolio returns or actual fills. "
               "Spreads, fees and slippage are assumptions. No historical order-book or flow data is invented. "
               "Labels and coins are correlated; counts are not independent observations. "
               "Candidates remain separate from all practice trading and forward-learning records.")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(_json(payload), encoding="utf-8")
    pending.replace(path)


def normalize(bar, symbol, start, end):
    if (bar.exchange != EXCHANGE_BINANCE_US or bar.symbol != symbol or bar.interval != "1h"
            or bar.open_time % HOUR or bar.close_time != bar.open_time + HOUR - 1
            or not start <= bar.open_time < end or bar.close_time >= end):
        raise ValueError(f"{symbol}: malformed or unfinished historical bar")
    values = {name: dec(getattr(bar, name)) for name in ("open", "high", "low", "close", "volume")}
    if (min(values[k] for k in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0
            or not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]):
        raise ValueError(f"{symbol}: invalid historical prices/volume")
    return {"symbol": symbol, "at": bar.open_time, **{k: format_decimal(v) for k, v in values.items()}}


def download(directory, *, days=365, client=None, at=None):
    if type(days) is not int or not 7 <= days <= 730:
        raise ValueError("History range must be 7 to 730 days")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    at = now_ms() if at is None else at
    end = at // HOUR * HOUR
    start = end - days * 24 * HOUR
    client = client or BinanceUSPublicClient()
    result = {"version": 1, "source": "Binance.US public 1h candles", "observed_at": at,
              "start": start, "end": end, "days": days, "coins": {}, "complete": False}
    with locked(directory / "download"), closing(sqlite3.connect(directory / "history.sqlite")) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS history (symbol TEXT,at INTEGER,body TEXT,PRIMARY KEY(symbol,at))")
        try:
            for symbol in SYMBOLS:
                count, previous, gaps, batch = 0, None, 0, []
                rejected = []
                for bar in client.iter_klines(symbol, "1h", start_time=start, end_time=end - 1):
                    try:
                        row = normalize(bar, symbol, start, end)
                    except ValueError:
                        # A short historical candle is not a completed hour.
                        # Exclude it visibly, including any older cached copy.
                        rejected.append(bar.open_time)
                        continue
                    if previous is not None:
                        if row["at"] <= previous:
                            raise ValueError(f"{symbol}: duplicate or unordered history")
                        gaps += (row["at"] - previous) // HOUR - 1
                    previous = row["at"]
                    batch.append((symbol, row["at"], _json(row)))
                    count += 1
                    if len(batch) == 1000:
                        with db:
                            db.executemany("INSERT OR REPLACE INTO history VALUES (?,?,?)", batch)
                        batch = []
                with db:
                    db.executemany("INSERT OR REPLACE INTO history VALUES (?,?,?)", batch)
                result["coins"][symbol] = {"bars_received": count, "internal_missing_hours": gaps,
                                           "missing_expected_hours": days * 24 - count,
                                           "rejected_bars": len(rejected), "invalid_open_times": rejected}
                _atomic(directory / "download.json", result)
                if not count:
                    raise ValueError(f"{symbol}: no completed history was returned")
            result["complete"] = True
        except Exception as exc:
            result["error"] = str(exc)[:200]
            raise
        finally:
            _atomic(directory / "download.json", result)
    return result


def load_rows(directory):
    path = Path(directory) / "history.sqlite"
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return [json.loads(body) for (body,) in db.execute("SELECT body FROM history ORDER BY symbol,at")]
    finally:
        db.close()


def _clip(number):
    if not math.isfinite(number):
        raise ValueError("Historical feature is non-finite")
    return max(-1.0, min(1.0, number))


def features(past, symbol):
    prices = [float(p["close"]) for p in past]
    fast, slow = sum(prices[-20:]) / 20, sum(prices) / 50
    returns = [b / a - 1 for a, b in zip(prices[-21:-1], prices[-20:])]
    volume = [float(p["volume"]) for p in past[-21:]]
    average = sum(volume[:-1]) / 20
    values = [1.0, _clip((fast / slow - 1) * 20),
              _clip(math.sqrt(sum(r * r for r in returns) / 20) * 25),
              _clip((volume[-1] / average - 1) / 3) if average else 0.0,
              *(_clip((prices[-1] / prices[-1 - n] - 1) * scale) for n, scale in ((1, 20), (4, 10), (24, 5))),
              _clip((float(past[-1]["high"]) - float(past[-1]["low"])) / prices[-1] * 20),
              *(float(symbol == s) for s in SYMBOLS)]
    return values, fast > slow * 1.002


def net_return(entry, exit_price, fee, slip, spread):
    return exit_price * (1 - slip) * (1 - spread / 2) * (1 - fee) / (entry * (1 + slip) * (1 + spread / 2) * (1 + fee)) - 1


def examples(rows, horizon, profile, *, spread=0.001):
    if horizon not in (1, 4, 24) or not 0 <= spread <= 0.1:
        raise ValueError("Invalid historical scenario")
    fee, slip = float(research_costs.fee(profile)), float(profile["slippage"])
    result, coverage = [], {}
    for symbol in SYMBOLS:
        bars = sorted((p for p in rows if p["symbol"] == symbol), key=lambda p: p["at"])
        skipped = 0
        for i in range(49, len(bars) - horizon):
            window = bars[i - 49:i + horizon + 1]
            if any(b["at"] - a["at"] != HOUR for a, b in zip(window, window[1:])):
                skipped += 1
                continue
            values, trend = features(bars[i - 49:i + 1], symbol)
            entry, exit_price = float(bars[i + 1]["open"]), float(bars[i + horizon]["close"])
            net = net_return(entry, exit_price, fee, slip, spread)
            stress = net_return(entry, exit_price, min(0.1, fee * 2), min(0.1, slip * 2), min(0.1, spread * 2))
            result.append({"symbol": symbol, "at": bars[i]["at"] + HOUR,
                           "label_at": bars[i + horizon]["at"] + HOUR, "x": values,
                           "y": int(net > 0), "net": net, "stress": stress, "trend": trend,
                           "condition": "volatile" if values[2] >= 0.5 else "rising" if values[1] > 0.04 else "falling" if values[1] < -0.04 else "sideways"})
        coverage[symbol] = {"bars": len(bars), "skipped_gap_windows": skipped}
    return sorted(result, key=lambda r: (r["at"], r["symbol"])), coverage


def probability(weights, values):
    z = max(-30.0, min(30.0, sum(w * x for w, x in zip(weights, values))))
    return 1 / (1 + math.exp(-z))


def fit(rows, *, epochs=3):
    weights = [0.0] * len(FEATURES)
    count = 0
    for _ in range(epochs):
        for row in rows:
            error = probability(weights, row["x"]) - row["y"]
            rate = 0.05 / math.sqrt(1 + count / 1000)
            weights = [max(-10.0, min(10.0, w - rate * (error * x + (0.001 * w if i else 0)))) for i, (w, x) in enumerate(zip(weights, row["x"]))]
            count += 1
    return weights


def score(rows, weights, baseline):
    output = {"n": len(rows), "model_brier": 0.0, "baseline_brier": 0.0,
              "selected": 0, "selected_net_pct": None, "selected_stress_pct": None,
              "cash_pct": 0, "trend_selected": 0, "trend_net_pct": None, "coins": {}, "conditions": {}}
    net, stress, trend_net = [], [], []
    available, trend_available = {}, {}
    for row in rows:
        p = probability(weights, row["x"])
        model_loss, baseline_loss = (p - row["y"]) ** 2, (baseline - row["y"]) ** 2
        output["model_brier"] += model_loss
        output["baseline_brier"] += baseline_loss
        for group, name in (("coins", row["symbol"]), ("conditions", row["condition"])):
            item = output[group].setdefault(name, {"n": 0, "model_brier": 0.0, "baseline_brier": 0.0})
            item["n"] += 1
            item["model_brier"] += model_loss
            item["baseline_brier"] += baseline_loss
        # Avoid overlapping selected hypothetical trades within each coin.
        if p >= 0.6 and row["at"] >= available.get(row["symbol"], 0):
            net.append(row["net"])
            stress.append(row["stress"])
            available[row["symbol"]] = row["label_at"]
        if row["trend"] and row["at"] >= trend_available.get(row["symbol"], 0):
            trend_net.append(row["net"])
            trend_available[row["symbol"]] = row["label_at"]
    output["model_brier"] /= len(rows)
    output["baseline_brier"] /= len(rows)
    for group in ("coins", "conditions"):
        for item in output[group].values():
            item["model_brier"] /= item["n"]
            item["baseline_brier"] /= item["n"]
    output.update(selected=len(net), selected_net_pct=sum(net) * 100 / len(net) if net else None,
                  selected_stress_pct=sum(stress) * 100 / len(stress) if stress else None,
                  trend_selected=len(trend_net), trend_net_pct=sum(trend_net) * 100 / len(trend_net) if trend_net else None)
    return output


def walk_forward(samples, *, folds=3):
    times = sorted({r["at"] for r in samples})
    if len(times) < 300:
        raise ValueError("Need at least 300 usable hourly decision times for chronological evaluation")
    reports = []
    initial = len(times) // 2
    for number in range(folds):
        lo = initial + (len(times) - initial) * number // folds
        hi = initial + (len(times) - initial) * (number + 1) // folds
        start, end = times[lo], times[hi] if hi < len(times) else max(r["label_at"] for r in samples) + 1
        # One shared cutoff for ALL coins; purge labels crossing the test start.
        training = [r for r in samples if r["at"] < start and r["label_at"] < start]
        testing = [r for r in samples if start <= r["at"] < end and r["label_at"] < end]
        if len(training) < 100 or not testing:
            raise ValueError("Insufficient chronological training/test coverage")
        weights = fit(training)
        baseline = (sum(r["y"] for r in training) + 1) / (len(training) + 2)
        reports.append({"fold": number + 1, "training_n": len(training), "training_last_label": max(r["label_at"] for r in training),
                        "test_last_label": max(r["label_at"] for r in testing),
                        "training_coins": {s: sum(r["symbol"] == s for r in training) for s in SYMBOLS},
                        "test_start": start, "test_end": end, "weights": weights,
                        "scores": score(testing, weights, baseline)})
    return reports


def evaluate(directory, profile_path):
    directory = Path(directory)
    with locked(directory / "evaluation"), locked(directory / "download"):
        manifest = json.loads((directory / "download.json").read_text(encoding="utf-8"))
        if not manifest.get("complete"):
            raise ValueError("The last historical download did not complete; finish it before evaluation")
        rows = load_rows(directory)
        rows = [r for r in rows if manifest["start"] <= r["at"] < manifest["end"]]
        excluded = {s: set(v.get("invalid_open_times", [])) for s, v in manifest["coins"].items()}
        rows = [r for r in rows if r["at"] not in excluded.get(r["symbol"], set())]
        if not rows or set(r["symbol"] for r in rows) != set(SYMBOLS):
            raise ValueError("Download hourly history for all five coins first")
        if any(r["at"] + HOUR > now_ms() for r in rows):
            raise ValueError("Saved history contains unfinished/future candles")
        profile = research_costs.load(Path(profile_path))
        frozen = _json({"version": 1, "bars": rows})
        digest = hashlib.sha256(frozen.encode()).hexdigest()
        datasets = directory / "datasets"
        datasets.mkdir(parents=True, exist_ok=True)
        dataset = datasets / (digest + ".json")
        if dataset.exists():
            if hashlib.sha256(dataset.read_bytes()).hexdigest() != digest:
                raise ValueError("Frozen dataset changed; refusing to reuse it")
        else:
            with dataset.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(frozen)
        identity = str(now_ms()) + "_" + uuid.uuid4().hex[:10]
        report = {"id": identity, "version": VERSION, "created_at": now_ms(), "dataset_sha256": digest,
                  "code_sha256": source_identity(),
                  "source": manifest, "first_bar": min(r["at"] for r in rows), "last_bar": max(r["at"] for r in rows),
                  "dataset_file": str(dataset.relative_to(directory)), "features": FEATURES,
                  "settings": {"folds": 3, "epochs": 3, "threshold": 0.6, "assumed_full_spread": 0.001},
                  "cost_profile": profile, "role": "historical_research_only", "limitations": LIMITATIONS,
                  "tracks": {}, "status": "started"}
        journal = directory / "attempts.jsonl"
        append_record(journal, {"id": identity, "status": "started", "dataset_sha256": digest,
                                "code_sha256": report["code_sha256"],
                                "cost_sha256": profile["sha256"], "settings": report["settings"], "version": VERSION})
        try:
            for horizon in (1, 4, 24):
                samples, coverage = examples(rows, horizon, profile)
                report["tracks"][str(horizon) + "h"] = {"coverage": coverage, "folds": walk_forward(samples)}
            report["status"] = "completed"
        except Exception as exc:
            report.update(status="failed", error=str(exc)[:200])
            raise
        finally:
            artifacts = directory / "studies"
            artifacts.mkdir(parents=True, exist_ok=True)
            with (artifacts / (identity + ".json")).open("x", encoding="utf-8") as handle:
                handle.write(_json(report))
            append_record(journal, {"id": identity, "status": report["status"]})
            if report["status"] == "completed":
                _atomic(directory / "latest.json", report)
    return report


def latest(directory):
    path = Path(directory) / "latest.json"
    if not path.exists():
        return None
    if path.stat().st_size > 2_000_000:
        raise ValueError("Research report is oversized")
    return json.loads(path.read_text(encoding="utf-8"))


def describe(report):
    lines = ["Historical candidates only — practice models and balances are preserved.",
             "Three chronological tests per horizon; lower Brier prediction error is better."]
    for name, track in report["tracks"].items():
        lines.append(name + ":")
        lines.append("  Saved bars / windows excluded for gaps: " + "; ".join(f"{s.removesuffix('USD')} {v['bars']}/{v['skipped_gap_windows']}" for s, v in track["coverage"].items()))
        for fold in track["folds"]:
            s = fold["scores"]
            result = f"{s['selected_net_pct']:+.3f}%" if s["selected_net_pct"] is not None else "no selected opportunities"
            stress = f"{s['selected_stress_pct']:+.3f}%" if s["selected_stress_pct"] is not None else "unavailable"
            lines.append(f"  Test {fold['fold']}: {s['n']} outcomes | error {s['model_brier']:.4f}, baseline {s['baseline_brier']:.4f} | "
                         f"selected {s['selected']}: mean {result}; stressed {stress}")
    lines.append(report["limitations"])
    lines.append("These results do not establish live profitability or authorize model promotion. Every attempt and frozen input is saved.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate candle-only historical research; never changes practice")
    parser.add_argument("command", choices=("download", "evaluate", "status"))
    parser.add_argument("--directory", default="data/learning_research")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--profile", default="data/research_costs.json")
    args = parser.parse_args(argv)
    if args.command == "download":
        print(json.dumps(download(args.directory, days=args.days), indent=2))
    elif args.command == "evaluate":
        print(describe(evaluate(args.directory, args.profile)))
    else:
        report = latest(args.directory)
        print(describe(report) if report else "No historical evaluation yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
