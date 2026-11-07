"""Chronological portfolio replay of the exact frozen swing-practice policy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from crypto_research.decimals import dec
from crypto_research.experiments.runner import source_identity
from crypto_research.jsonl import append_record, locked
from crypto_research.market_universe import SYMBOLS
from crypto_research.timeutil import now_ms
from . import historical, research_costs, swing_strategy as policy

LIMITATIONS = ("Normalized paper portfolios starting at index 100, with fractional quantities. "
               "Historical opens/closes and assumed spread/slippage are not executable quotes. "
               "Stop exits use hourly extremes; if stop and target both occur, the stop wins. "
               "No queue position, market impact, venue lot sizes or taxes are modeled. "
               "A gap in any required candle withholds the entire fold's performance. "
               "Current published/account-reported costs are applied uniformly as a scenario, not reconstructed historical account fees.")


def simulate(rows, config, start, end, *, buy_hold=False, spread="0.001"):
    by_symbol = {s: {r["at"]: r for r in rows if r["symbol"] == s} for s in SYMBOLS}
    state = policy.initial("100", config)
    state["active"] = True
    spread = dec(spread)
    history, actions = [], []
    for at in range(start, end, historical.HOUR):
        quotes, bars = {}, {}
        for symbol in SYMBOLS:
            bar = by_symbol[symbol].get(at)
            past = [by_symbol[symbol].get(at - i * historical.HOUR) for i in range(50, 0, -1)]
            if bar is None or any(p is None for p in past):
                return {"status": "withheld_data_gap", "missing_at": at, "symbol": symbol,
                        "return_pct": None, "max_drawdown_pct": None}
            bars[symbol] = bar
            price = dec(bar["open"])
            quotes[symbol] = policy.quote_from_bars(symbol, past, price * (1 - spread / 2), price * (1 + spread / 2), at, config)
        if buy_hold:
            if at == start:
                fee, slip = policy.costs(config)
                for symbol, q in quotes.items():
                    spend = dec(20)
                    price = dec(q["ask"]) * (1 + slip)
                    quantity = spend / (price * (1 + fee))
                    state["positions"][symbol] = {"quantity": str(quantity), "entry": str(price), "cost": str(spend), "at": at}
                    state["cash"] = str(dec(state["cash"]) - spend)
                    state["fees"] = str(dec(state["fees"]) + quantity * price * fee)
                    actions.append({"at": at, "action": "BUY", "symbol": symbol, "reason": "Equal-weight buy/hold benchmark",
                                    "spend": str(spend), "quantity": str(quantity), "fee": str(quantity * price * fee)})
        elif state["active"]:
            actions.extend(policy.advance(state, quotes, at))
            for symbol, position in list(state["positions"].items()):
                bar = bars[symbol]
                entry = dec(position["entry"])
                low, high = dec(bar["low"]) * (1 - spread / 2), dec(bar["high"]) * (1 - spread / 2)
                stop, target = entry * (1 - dec(config["stop"])), entry * (1 + dec(config["target"]))
                if low <= stop or high >= target:
                    bid = stop if low <= stop else target
                    reason = "Hourly stop assumption (stop wins if both hit)" if low <= stop else "Hourly profit-target assumption"
                    policy.close_position(state, symbol, {"bid": str(bid)}, at + historical.HOUR - 1, reason, actions)
        closing = {s: {**q, "bid": str(dec(bars[s]["close"]) * (1 - spread / 2))} for s, q in quotes.items()}
        policy.mark(state, closing)
        if state["halted"] and not buy_hold:
            for symbol in list(state["positions"]):
                policy.close_position(state, symbol, closing[symbol], at + historical.HOUR - 1, "Portfolio loss trigger at hourly close", actions)
            state["active"] = False
            policy.mark(state, closing)
        history.append({"at": at + historical.HOUR, "index": str(state["equity"])})
    if not history:
        raise ValueError("Portfolio test period is empty")
    for symbol in list(state["positions"]):
        policy.close_position(state, symbol, closing[symbol], end - 1, "End-of-test liquidation assumption", actions)
    policy.mark(state, closing)
    return {"status": "completed", "return_pct": float((dec(state["equity"]) / 100 - 1) * 100),
            "max_drawdown_pct": float(dec(state["drawdown"]) * 100), "fees_index_units": state["fees"],
            "exits": state["exits"], "wins": state["wins"], "halted": state["halted"], "equity_curve": history,
            "trade_ledger": [event for event in actions if event["action"] in ("BUY", "SELL")]}


def evaluate(directory):
    directory = Path(directory)
    report = historical.latest(directory)
    if not report:
        raise ValueError("Run historical training first")
    path = (directory / report["dataset_file"]).resolve()
    if not path.is_relative_to(directory.resolve() / "datasets"):
        raise ValueError("Frozen dataset path is outside the study")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != report["dataset_sha256"]:
        raise ValueError("Frozen historical inputs have changed")
    rows = json.loads(data)["bars"]
    identity = str(now_ms()) + "_" + uuid.uuid4().hex[:10]
    config = policy.config_from_report(report)
    result = {"id": identity, "created_at": now_ms(), "version": policy.VERSION, "historical_report_id": report["id"],
              "dataset_sha256": report["dataset_sha256"], "cost_profile": config["profile"],
              "code_sha256": source_identity(), "status": "started", "folds": [], "limitations": LIMITATIONS}
    with locked(directory / "swing_evaluation"):
        journal = directory / "swing_attempts.jsonl"
        append_record(journal, {"id": identity, "status": "started", "historical_report_id": report["id"], "version": policy.VERSION})
        try:
            for fold in report["tracks"]["24h"]["folds"]:
                current = {**config, "weights": fold["weights"], "training_last_label": fold["training_last_label"]}
                if current["training_last_label"] >= fold["test_start"]:
                    raise ValueError("Swing training overlaps testing")
                start, end = fold["test_start"], min(fold["test_end"] // historical.HOUR * historical.HOUR, report["source"]["end"])
                stressed = json.loads(json.dumps(current))
                profile = stressed["profile"]
                for rates in profile["fees"].values():
                    for side in ("maker", "taker"):
                        rates[side] = str(min(dec("0.1"), dec(rates[side]) * 2))
                profile["slippage"] = str(min(dec("0.1"), dec(profile["slippage"]) * 2))
                profile = research_costs.validate(profile)
                stressed["profile"] = profile
                result["folds"].append({"fold": fold["fold"], "start": start, "end": end,
                                        "training_last_label": fold["training_last_label"],
                                        "strategy": simulate(rows, current, start, end),
                                        "stress": simulate(rows, stressed, start, end, spread="0.002"),
                                        "buy_hold": simulate(rows, current, start, end, buy_hold=True),
                                        "cash_return_pct": 0.0})
            result["status"] = "completed"
        except Exception as exc:
            result.update(status="failed", error=str(exc)[:200])
            raise
        finally:
            folder = directory / "swing_studies"
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / (identity + ".json")).open("x", encoding="utf-8") as handle:
                json.dump(result, handle, sort_keys=True)
            append_record(journal, {"id": identity, "status": result["status"]})
            if result["status"] == "completed":
                historical._atomic(directory / "swing_latest.json", result)
    return result


def latest(directory):
    path = Path(directory) / "swing_latest.json"
    if not path.exists():
        return None
    if path.stat().st_size > 8_000_000:
        raise ValueError("Swing portfolio report is oversized")
    return json.loads(path.read_text(encoding="utf-8"))


def describe(report):
    lines = ["Whole swing portfolio tests — normalized index, no borrowed cash."]
    for fold in report["folds"]:
        s, stress, hold = fold["strategy"], fold["stress"], fold["buy_hold"]
        if s["status"] != "completed":
            lines.append(f"Test {fold['fold']}: performance withheld because required hourly data is missing.")
        else:
            lines.append(f"Test {fold['fold']}: swing {s['return_pct']:+.2f}%, stress {stress['return_pct']:+.2f}%, "
                         f"buy/hold {hold['return_pct']:+.2f}%, cash 0%; drawdown {s['max_drawdown_pct']:.2f}%; {s['exits']} exits.")
    lines.extend([report["limitations"], "This experimental policy has not established a reliable trading advantage. Future practice is a separate test."])
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe(evaluate(Path("data/learning_research"))))
