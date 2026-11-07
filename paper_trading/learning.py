"""Bounded forward-only rule selection; discovery and validation never overlap.

Candidates differ only in entry threshold. Independent pretend portfolios use
the owning agent's exact cost/fill/risk routine. This is adaptive rule selection,
not a claim of statistical significance or an unrestricted self-writing model.
"""

from __future__ import annotations

from decimal import Decimal

from crypto_research.decimals import dec

CANDIDATES = {"standard": "0.002", "selective": "0.005", "very_selective": "0.010"}
LABELS = {"standard": "Standard (0.2%)", "selective": "Selective (0.5%)",
          "very_selective": "Very selective (1.0%)"}
PHASE_HOURS = 168
MIN_EXITS = 10
MIN_EDGE = Decimal("1")
HOUR = 3_600_000


def initial_learning() -> dict:
    return {"version": 1, "active_rule": "standard", "phase": "discovery",
            "hours": 0, "last_hour": 0, "started_at": None, "challenger": None,
            "portfolios": {}, "promotions": 0, "rounds": 0,
            "actual_exits": 0, "actual_wins": 0, "actual_pnl": "0",
            "message": "Collecting forward practice evidence; no rule changes yet."}


def entry_margin(state: dict) -> Decimal:
    return dec(CANDIDATES[state.get("learning", {}).get("active_rule", "standard")])


def actual_outcome(learner: dict, action: str, details: dict) -> None:
    if action == "SELL":
        pnl = dec(details["realized_pnl"])
        learner["actual_exits"] += 1
        learner["actual_wins"] += int(pnl > 0)
        learner["actual_pnl"] = str(dec(learner["actual_pnl"]) + pnl)


def advance(learner: dict, observations, at: int, *, make_wallet, simulate, record) -> None:
    """Consume only the current observation. A promotion affects the next check."""
    if learner["version"] != 1 or learner["active_rule"] not in CANDIDATES:
        raise ValueError("Unsupported saved learning rules")
    if not learner["portfolios"]:
        _new_portfolios(learner, make_wallet)
        learner["started_at"] = at
    for key, portfolio in learner["portfolios"].items():
        wallet = portfolio["wallet"]
        if wallet["active"]:
            def completed(action, details, portfolio=portfolio):
                if action == "SELL":
                    portfolio["exits"] += 1
                    portfolio["wins"] += int(dec(details["realized_pnl"]) > 0)
            simulate(wallet, dec(CANDIDATES[key]), completed)
        equity = dec(wallet["equity"])
        peak = max(dec(portfolio["peak"]), equity)
        portfolio["peak"] = str(peak)
        portfolio["drawdown"] = str(max(dec(portfolio["drawdown"]), (peak - equity) / peak))

    # Count one matching, newly observed hourly close, never every minute or
    # assumed hours during sleep/offline time. Skip mixed-hour observations.
    hours = {o.bar_time for o in observations}
    if len(hours) == 1:
        hour = next(iter(hours))
        if hour > learner["last_hour"]:
            learner["hours"] += 1
            learner["last_hour"] = hour
    learner["message"] = (f"{learner['phase'].capitalize()}: {learner['hours']}/{PHASE_HOURS} observed hours; "
                          f"need {MIN_EXITS} completed trades per compared rule.")
    if learner["hours"] < PHASE_HOURS or at - learner["started_at"] < PHASE_HOURS * HOUR:
        return
    incumbent = learner["active_rule"]
    portfolios = learner["portfolios"]
    if learner["phase"] == "discovery":
        current = portfolios[incumbent]
        if not current["wallet"]["active"]:
            _restart(learner, at, make_wallet, "Discovery stopped: reference rule hit its loss limit.", record)
            return
        eligible = [key for key, p in portfolios.items()
                    if key != incumbent and _eligible(p, current)]
        if not eligible:
            learner["message"] += " No qualifying challenger yet."
            return
        challenger = max(eligible, key=lambda k: dec(portfolios[k]["wallet"]["equity"]))
        evidence = {"candidate_equity": portfolios[challenger]["wallet"]["equity"],
                    "reference_equity": current["wallet"]["equity"],
                    "candidate_exits": portfolios[challenger]["exits"], "reference_exits": current["exits"],
                    "candidate_drawdown": portfolios[challenger]["drawdown"],
                    "reference_drawdown": current["drawdown"], "observed_hours": learner["hours"]}
        learner.update(phase="validation", challenger=challenger, hours=0, started_at=at)
        _new_portfolios(learner, make_wallet)
        learner["message"] = "Testing a frozen challenger on new observations; active rule stays unchanged."
        record("LEARN", learner["message"], challenger=challenger, active_rule=incumbent, **evidence)
        return
    challenger = learner["challenger"]
    candidate, current = portfolios[challenger], portfolios[incumbent]
    if not candidate["wallet"]["active"] or not current["wallet"]["active"]:
        _restart(learner, at, make_wallet, "Validation rejected: a comparison wallet reached its loss limit.", record)
        return
    if min(candidate["exits"], current["exits"]) < MIN_EXITS:
        learner["message"] += " Waiting for more completed trades."
        return
    passed = _eligible(candidate, current)
    record("LEARN", "Completed separate validation of the frozen challenger.", passed=passed,
           challenger=challenger, active_rule=incumbent, candidate_equity=candidate["wallet"]["equity"],
           reference_equity=current["wallet"]["equity"], candidate_exits=candidate["exits"],
           reference_exits=current["exits"], candidate_drawdown=candidate["drawdown"],
           reference_drawdown=current["drawdown"], observed_hours=learner["hours"])
    if passed:
        learner["active_rule"] = challenger
        learner["promotions"] += 1
        record("ADAPT", f"Changed entry rule from {LABELS[incumbent]} to {LABELS[challenger]} after a separate validation period.",
               previous=incumbent, selected=challenger, candidate_equity=candidate["wallet"]["equity"],
               reference_equity=current["wallet"]["equity"], candidate_exits=candidate["exits"],
               reference_exits=current["exits"], candidate_drawdown=candidate["drawdown"],
               reference_drawdown=current["drawdown"], observed_hours=learner["hours"])
        message = "Validation passed. New entry rule applies to future checks; holdings and limits stay unchanged."
    else:
        message = "Validation did not confirm an improvement. Kept the existing rule."
    _restart(learner, at, make_wallet, message, record)


def _eligible(candidate, reference) -> bool:
    return (candidate["wallet"]["active"] and reference["wallet"]["active"]
            and min(candidate["exits"], reference["exits"]) >= MIN_EXITS
            and dec(candidate["wallet"]["equity"]) >= Decimal("200") + MIN_EDGE
            and dec(candidate["wallet"]["equity"]) >= dec(reference["wallet"]["equity"]) + MIN_EDGE
            and dec(candidate["drawdown"]) <= dec(reference["drawdown"]))


def _new_portfolios(learner, make_wallet):
    keys = CANDIDATES if learner["phase"] == "discovery" else (learner["active_rule"], learner["challenger"])
    learner["portfolios"] = {}
    for key in keys:
        wallet = make_wallet()
        wallet["active"] = True
        learner["portfolios"][key] = {"wallet": wallet, "exits": 0, "wins": 0,
                                       "peak": "200", "drawdown": "0"}


def _restart(learner, at, make_wallet, message, record):
    learner.update(phase="discovery", challenger=None, hours=0, started_at=at,
                   rounds=learner["rounds"] + 1, message=message)
    _new_portfolios(learner, make_wallet)
    record("LEARN", message, active_rule=learner["active_rule"])
