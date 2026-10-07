"""Try one plan from every possible starting month.

Why this exists: a single start date is close to meaningless. The research in
this repo found that *when you happen to start* dominates everything else a
person controls, so one window -- especially one that includes 2020-21 -- can
make a plan look wonderful or terrible by luck alone. This runs the same plan
from the 1st of every month the saved prices allow, each held for the same
length of time, so the spread of outcomes is visible instead of hidden.

The orchestration deliberately mirrors scripts/risk_profile.py (open the
database once, build the rule book once, step start months with add_months,
a fresh strategy per window, skip a window when the engine refuses its data).
Its output was checked against results/start_date_sweep_12m.csv from that
earlier research: all 60 months matched to the cent.

Nothing here is recorded in experiments/ (the research log): a sweep is
dozens of exploratory runs, not one experiment. plan_history.py keeps a
one-line summary of each sweep for the "Your past plans" screen instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from crypto_research.backtest.engine import Backtester
from crypto_research.backtest.metrics import compute_metrics
from crypto_research.binanceus.filters import SymbolRuleBook
from crypto_research.config import parse_config
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.datafeed.loader import load_dataset
from crypto_research.errors import ConfigError
from crypto_research.provenance import rule_identity, source_identity
from crypto_research.storage.database import Database, open_database
from crypto_research.storage.repositories import RuleSnapshotRepository
from crypto_research.strategies import build_strategy
from crypto_research.timeutil import DAY_MS, add_months, parse_time, to_datetime, to_iso

from . import plan_builder
from .plan_builder import PlanRequest, Schedule

# How long each try is held. The research used the same 1-, 2- and 3-year holds.
HOLD_LABELS: dict[int, str] = {12: "1 year", 24: "2 years", 36: "3 years"}

# Fewer starting months than this says too little about how much timing matters.
MIN_WINDOWS = 6


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    start_ms: int
    capital_in: Decimal
    ending_value: Decimal
    max_drawdown: Decimal
    rejected_orders: int
    dataset_sha256: str = ""


@dataclass(frozen=True, slots=True)
class SweepRequest:
    amount: Decimal
    symbols: tuple[str, ...]
    schedule: Schedule
    hold_months: int
    # The range in which every chosen coin has saved prices (coin_catalog.overlapping_range).
    first_open_ms: int
    last_open_ms: int
    db_path: str


@dataclass(frozen=True, slots=True)
class SweepResult:
    amount: Decimal
    symbols: tuple[str, ...]
    schedule: Schedule
    hold_months: int
    outcomes: tuple[WindowOutcome, ...]
    # Starting months not tried because a coin had no saved prices for them.
    skipped: int
    provenance: dict = field(default_factory=dict)
    comparison_snapshot: dict | None = None


def month_starts(first_ms: int, last_ms: int, hold_months: int) -> list[int]:
    """The 1st of every month (UTC) a ``hold_months`` hold could start on.

    A hold ending on ``end`` needs the bar that opens the day before ``end``,
    so the last usable start is the latest month whose end is at most one day
    past ``last_ms``.
    """
    first = to_datetime(first_ms)
    start = parse_time(f"{first.year:04d}-{first.month:02d}-01")
    if start < first_ms:
        start = add_months(start, 1)
    starts: list[int] = []
    while add_months(start, hold_months) <= last_ms + DAY_MS:
        starts.append(start)
        start = add_months(start, 1)
    return starts


def run_sweep(request: SweepRequest) -> SweepResult:
    """Run the plan from every possible starting month. Call from a worker thread.

    Opens its own database connection (sqlite connections belong to the thread
    that made them). Raises ConfigError if trading rules are missing for a
    chosen coin; a starting month whose prices are missing or have a hole is
    skipped and counted, never silently filled in.
    """
    starts = month_starts(request.first_open_ms, request.last_open_ms, request.hold_months)
    outcomes: list[WindowOutcome] = []
    skipped = 0
    with open_database(request.db_path) as db:
        rule_book = _load_rule_book(db, request)
        for start_ms in starts:
            outcome = _run_window(db, rule_book, request, start_ms)
            if outcome is None:
                skipped += 1
            else:
                outcomes.append(outcome)
    return SweepResult(
        amount=request.amount,
        symbols=request.symbols,
        schedule=request.schedule,
        hold_months=request.hold_months,
        outcomes=tuple(outcomes),
        skipped=skipped,
        provenance={"code_sha256": source_identity(), "rules": rule_identity(rule_book)},
    )


def _load_rule_book(db: Database, request: SweepRequest) -> SymbolRuleBook:
    # Same two checks (and wording, which errors.py recognises) as
    # crypto_research.experiments.runner._execute.
    rules = RuleSnapshotRepository(db).latest()
    if not rules:
        raise ConfigError(
            f"no exchangeInfo snapshots in {request.db_path}; run "
            "`python -m crypto_research data refresh-rules` first"
        )
    rule_book = SymbolRuleBook(rules)
    missing = [symbol for symbol in request.symbols if symbol not in rule_book]
    if missing:
        raise ConfigError(
            f"no Binance.US trading rules stored for {', '.join(missing)}; "
            "download them before running"
        )
    return rule_book


def _run_window(
    db: Database, rule_book: SymbolRuleBook, request: SweepRequest, start_ms: int
) -> WindowOutcome | None:
    end_ms = add_months(start_ms, request.hold_months)
    config = parse_config(
        plan_builder.build_config_payload(
            PlanRequest(
                amount=request.amount,
                symbols=request.symbols,
                schedule=request.schedule,
                start=to_iso(start_ms),
                end=to_iso(end_ms),
                db_path=request.db_path,
            )
        )
    )
    try:
        dataset = load_dataset(
            db,
            config.data.symbols,
            config.data.interval,
            start_time=config.data.start_time,
            end_time=config.data.end_time,
            max_gap_intervals=config.data.max_gap_intervals,
        )
    except ConfigError:
        return None
    if not _covers_window(dataset, start_ms, end_ms, config.data.max_gap_intervals):
        return None

    strategy = build_strategy(config.strategy_name, config.strategy_params)
    result = Backtester(
        dataset=dataset,
        rule_book=rule_book,
        config=config.backtest,
        fill_model=config.fill_model,
    ).run(strategy, run_id="gui-sweep")
    metrics = compute_metrics(result)
    return WindowOutcome(
        start_ms=start_ms,
        capital_in=metrics.total_capital_in,
        ending_value=metrics.ending_portfolio_value,
        max_drawdown=metrics.max_drawdown,
        rejected_orders=metrics.rejected_orders,
        dataset_sha256=dataset.version.sha256,
    )


def _covers_window(
    dataset: MarketDataSet, start_ms: int, end_ms: int, max_gap_intervals: int
) -> bool:
    """Does every coin have prices right up to both edges of the window?

    load_dataset only refuses a hole *between* two saved bars. A coin whose
    saved prices simply begin partway through the window (XRP resumes trading
    after a 2-year pause) would pass that check and then quietly enter the
    plan late -- so the edges are checked here, using the same tolerance the
    loader allows for a hole in the middle.
    """
    tolerance = max_gap_intervals * DAY_MS
    for bars in dataset.bars_by_symbol.values():
        if bars[0].open_time > start_ms + tolerance:
            return False
        if bars[-1].open_time < end_ms - DAY_MS - tolerance:
            return False
    return True
