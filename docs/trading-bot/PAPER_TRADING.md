# Paper trading: rehearsing a plan against live prices

`paper_trading/` watches a plan run against real Binance.US prices, with
pretend money, so mistakes and real costs show up before any real money is
involved. It places no order and holds no credential -- a rehearsal never
touches a real account, so it needs none.

## What a rehearsal is, in one sentence

The desktop **Practice agent** is a separate forward-only simulator in
`paper_trading/practice.py`, rather than the paired config-replay workflow below.
It keeps one persistent $200 pretend wallet, uses only the public market client,
and supports simulated buying and selling. No research config or real-account
permission is changed. See README's automatic practice-agent section for its
entry-rule learning, fixed risk limits, costs, persistence and fill-model limitations.
Start launches the fixed `market_capture.practice_worker` process. It continues
practice and prospective learning with the desktop window closed, while this
computer is awake. Reopening only reads the saved sessions; Pause remains an
explicit action. Status shows the heartbeat, last successful check and retries.
Nothing starts automatically at Windows login; after shutdown or a worker crash,
use Start/Reconnect. Missed trades and observations are never reconstructed.
`paper_trading/learning.py` compares bounded candidate entry thresholds using
forward discovery and a separate validation period. Trial portfolios reuse the
practice simulator; promotions affect only future entry checks. Learner state,
actual net outcomes and phase/promotion evidence share the wallet's atomic commit.
Background trial failures stop that trial, without partially changing its wallet
or discarding a valid actual practice decision. No learner can change the budget,
exit thresholds, permitted symbols or account-access boundary.

The optional `market_signals.py` layer adds bounded public aggregate-trade and
depth snapshots and hourly context. Its paired forward comparison freezes entry
parameters and shares starting holdings/cash, fills, costs and exit rules. Only
the candidate uses market-entry filters. Neither trial feeds the existing learner
or primary portfolio; failures and incomplete samples cannot alter those inputs.
Summary observations with content hashes are retained atomically in `market_samples`;
primary activity rows retain their evidence/cost explanations. Raw tape is not
archived, so hashes alone do not make historical observations replayable.
Existing practice/learning state is extended additively without a reset. See
README for sampling frequency, truncation/freshness handling and fixed filter thresholds.

`prediction.py` adds delayed, forward-only opportunity learning to the same
transaction. Predictions and fixed-scale feature vectors are recorded before
labels; outcomes mature on a fresh quote one hour later (five-minute grace).
Missing labels expire. Training is progressive, while challenger validation is
frozen and occurs only on subsequent forecasts. A fixed past-frequency baseline,
condition-specific scores, checkpoint history and deterioration-triggered
rollback provide comparisons without changing the actual agent. The predictor
uses standard-library numeric code and JSON, not executable model serialization.
A savepoint prevents predictor failure from discarding valid wallet/learner
updates. See README for all thresholds and the limits of hypothetical returns.

`swing.py` reuses that engine in independent 4h/24h namespaces scoped by the
economic identity of `research_costs.json`. The SQL adapter substitutes only
fixed table identifiers with validated prefixes; values remain bound parameters.
Per-coin sampling intervals prevent overlapping swing target windows. Existing
one-hour labels and weights are not migrated or overwritten. The fee import
does not change the current practice execution assumptions.

`market_capture/` is a separate, optionally installed public streaming recorder
with independent start/stop controls, finite storage and no account access.
Execution replay rejects capture gaps, requires causally later near-time depth
snapshots and leaves passive fills unmodeled. `metals.py` supports manual physical
inventory and hypothetical at-expiry hedge arithmetic only. See
[research extensions](RESEARCH_EXTENSIONS.md) for commands and limits.

Two ordinary backtest configs -- one for the plan, one for the benchmark it is
compared against -- run again every time new prices close, with the ending date
replaced by the newest one instead of a fixed date from the file.

## Why it is a separate program

`docs/trading-bot/SAFETY.md` states the rule this follows: *"If paper trading is
ever built, it becomes a separate process that consumes intents; the research
code does not grow an execution mode."* `paper_trading/` is exactly that
separate process. It does not add anything to `crypto_research/`; it only calls
functions `crypto_research/` already has and already tests:
`crypto_research.datafeed.download.Downloader` to fetch prices,
`crypto_research.experiments.runner.run_experiment` to run a strategy and record
it, and `crypto_research.experiments.registry.ExperimentRegistry` to keep the
result. A rehearsal's numbers come from code this project has already tested,
not from a copy of it -- and a bug found in a rehearsal's plumbing is, by
construction, a bug in `paper_trading/`'s few hundred lines of glue, never in
the backtester itself.

## What a rehearsal cannot show

A rehearsal of a few weeks cannot show that a plan is good, or that it is bad.
Both the strategy under test and the benchmark are watched on the very same
short, recent stretch of prices, which is one small sample of one market
regime. What it *can* show, honestly:

* whether the plan runs cleanly against real, live data day after day, without
  a person watching it;
* what it actually would have cost to run, once the exchange's own filters
  (minimum order sizes, price steps) apply to a real, small pool of money;
* the beginning of a record of what the order book really looked like when an
  order would have crossed it (see "Measuring the real cost of the book"
  below), which a backtest can never have.

The [scorecard](#the-scorecard) reflects this on purpose: it never asks "did
the plan beat the benchmark". See "Ideas not yet explored" in
`CONTEXT_FOR_CLAUDE.md` and the final report's limits section for why a short
watch period proves little about the future.

## Setting one up

A rehearsal is two files in `configs/`, in the same format as any other
backtest config (see `configs/benchmark1_staged_deployment.toml` and
`docs/trading-bot/BACKTESTING.md`):

* `configs/paper_rehearsal_example.toml` -- the plan under test.
* `configs/paper_benchmark_example.toml` -- what it is compared against.

Copy both, rename them, and change what you want to try: the symbols, the pool
size, the tranche count and cadence, the strategy. Two rules apply that an
ordinary backtest config does not need to follow:

1. **`db_path`, `symbols`, `interval` and `start` must be identical in both
   files.** `paper_trading.plan.load_plan` checks this and refuses to start a
   rehearsal where they differ, because the two tracks have to see
   byte-identical prices and pay the same costs for the comparison to mean
   anything.
2. **`end` in both files is a placeholder.** It has to be a valid date after
   `start` so the file loads, but every tick throws it away and uses the newest
   fully closed price instead. You never edit `end` to "move time forward".

`db_path` should point at a database of its own (the examples use
`data/paper_market.sqlite`), never at `data/market.sqlite`: a rehearsal's live
downloads must never mix into the data this project's actual findings were
computed from, and the reverse.

Starting a *different* rehearsal (a different pool, a different strategy) means
a new pair of files pointing at a new `db_path`. Reusing a `db_path` with a
changed strategy would silently start comparing apples to a different, earlier
sample of apples.

## Running it

    python -m paper_trading tick --rehearsal configs/paper_rehearsal_example.toml --benchmark configs/paper_benchmark_example.toml
    python -m paper_trading report --rehearsal configs/paper_rehearsal_example.toml --benchmark configs/paper_benchmark_example.toml

`tick` is meant to be run on a schedule -- once a day is enough for a daily
plan, and running it more often than that is harmless (see "Idempotency"
below). On Windows, Task Scheduler can run it: an action of
`python -m paper_trading tick --rehearsal ... --benchmark ...` with "Start in"
set to the project folder, triggered daily. Nothing about scheduling it needs a
credential of any kind.

`report` reads back what has happened so far and prints the plain-language
write-up described below. It can be run at any time and changes nothing.

## What one tick does

1. **Fetch.** Ask Binance.US's public price endpoint for whatever bars have
   closed since the last tick, for every symbol the plan names, and store them
   in the rehearsal's own small database.
2. **Find the common moment.** Take the earliest of "the newest bar each symbol
   has reached". A decision is only ever made once *every* symbol has reached
   the same day -- never on a mix where one coin's price is a day fresher than
   another's, which would be an inconsistent snapshot no real system could have
   acted on.
3. **Stop if nothing changed.** If that moment has not advanced since the last
   tick, the tick ends here, having done nothing further.
4. **Run both tracks.** Otherwise, run `run_experiment` once for the rehearsal
   config and once for the benchmark config, each with its `end` replaced by
   that moment, and record both the same way a backtest would: a full
   `record.json`, `metrics.json`, `equity_curve.csv` and `orders.jsonl` per run,
   in `experiments/runs/`, kept forever, failures included.
5. **Log the tick.** Whatever happened -- a run, nothing new, or a failed
   fetch -- one line is appended to `<experiment_dir>/paper_ticks_<name>.jsonl`.
   This is what the scorecard and the report read to describe the rehearsal's
   history, separately from the experiment registry, which only ever hears
   about a tick that reached a run.

### Idempotency

Calling `tick` again before a new price has closed is always safe: the "common
moment" from step 2 has not moved, so step 4 finds an existing recorded run for
that exact moment and skips it rather than creating a near-duplicate. A
rehearsal can be ticked once a minute, once an hour, or once a day -- the
recorded history is the same either way, decided by how often prices actually
close, not by how often the tick is invoked. A missed day (the computer was off,
the network was down) is not lost either: the next tick that succeeds simply
finds more than one new day's worth of bars available and proceeds from
whichever moment they now have in common.

### A feed failure runs nothing

If Binance.US cannot be reached, or answers with an error, the tick is logged as
`FEED_ERROR` and neither track is run: there is no partial run, and nothing is
recorded in the experiment registry for that attempt. This mirrors the same
rule `external_intelligence` follows for its own providers -- a data source
failing must never be confused with a strategy failing, and must never reach
anything that could look like a trading decision.

### It cannot be fooled about "now"

`Downloader.backfill_klines` (the same function a real backtest download uses)
refuses to treat a bar as closed if its close time is after the *real* wall
clock, regardless of what time a caller claims it is. `tick()` accepts a `now`
purely so its own bookkeeping (the tick log's timestamp, the fetch window) can
be tested deterministically; it cannot make live data appear before it has
really happened. Tests that exercise this use dates safely in the past for
exactly this reason -- a start date close to today would make them depend on
what time they happened to run.

## The scorecard

Six checks, each `PASS`, `FAIL`, or `NOT_YET_EVALUATED` (too little history to
judge yet):

| Check | What it asks |
| --- | --- |
| `feed_reliable` | Can Binance.US usually be reached? (waits for at least 5 ticks) |
| `no_crashes` | Has any run ever failed? |
| `only_buys` | Has anything ever been sold? (structurally should be impossible) |
| `cash_never_negative` | Has the leftover cash in either track ever gone negative? |
| `benchmark_available` | Whenever the rehearsal has a result, does the benchmark have one too? |
| `rejection_rate_is_low` | Are most attempted orders actually going through? |

None of these grade the plan's performance, and none of them ever will --
`Scorecard.fully_passing` cannot be read as "the plan is good", only as
"nothing has gone mechanically wrong that we know to check for". A high
rejection rate is the most likely real finding early on: a small pool split
into many tranches across several coins can fall under an exchange's minimum
order size, and the scorecard's detail names the exact reason
(`NOTIONAL_BELOW_MIN` and the like) so the plan can be adjusted.

## The weekly report

`python -m paper_trading report ...` prints how many ticks have run and what
they found, a side-by-side table of both tracks' capital in, value now, simple
return, worst drop so far, leftover cash and total fees/spread/slippage, the
scorecard, and a standing reminder -- on every report, not just the first one --
that a rehearsal this short cannot show which track is better. The report
never uses the words "beats", "better" or "recommend" about the two tracks; if
a future change to it ever does, that is a bug.

## Measuring the real cost of the book

A backtest can only ever *assume* a spread and a slippage figure (see
`crypto_research.backtest.costs`); it has no order-book history to measure
them from. `paper_trading.depth_cost` reads the live book -- the same public
endpoint `scripts/trading_cost.py` already uses for one-off snapshots -- and
reports how far a hypothetical purchase would really have walked up it, next to
the mid price:

    python -m paper_trading measure-cost --symbol BTCUSD --notional 200 --log data/paper_depth_observations.jsonl

This is deliberately **not** wired into the automatic `tick`: it is a separate,
optional measurement, invoked on its own so a slow or failing depth read can
never affect whether a rehearsal tick runs. Run it by hand, or add it to the
same schedule as `tick`, to start building a real record of order-book cost
alongside the rehearsal's own history. Comparing that record against the
assumed spread and slippage figures a run used is a natural next step, not yet
built.

## Limitations

* **No fee-tier reading.** A rehearsal holds no credential, so it cannot read
  the account's real, tiered trading fee the way `account_tools/` can for a
  read-only check; it uses the same assumed fee schedule a backtest does, and
  the report is only as accurate as that assumption.
* **Daily bars only, unless the config says otherwise.** Sub-daily plans are
  possible (any interval `crypto_research` supports), but nothing has been
  rehearsed at anything finer than a day.
* **The depth-cost measurement is a snapshot**, exactly like
  `scripts/trading_cost.py`'s: the book a few seconds before or after a real
  decision can look different, especially in a fast market.
* **A rehearsal's own history is only as long as it has been running.** There is
  no way to "back-fill" a rehearsal's own tick log for days before it started;
  that is what an ordinary backtest is for.

## Safety

`paper_trading` carries the same audited claim as the research engine itself:
it cannot authenticate, and it holds no signing code, no credential, and no
private endpoint (`crypto_research/safety.py`, zone `paper-trading`). Its import
surface is checked directly: it may only import the standard library and a
short, explicit list of already-audited `crypto_research` modules -- never
`account_tools`, never `desktop_app`, never `crypto_research.binanceus.transport`
or `crypto_research.safety` itself (`tests/test_paper_trading_safety.py`). It
contains no function named after a trading action, no dynamic code execution,
and reads no environment variable. See `docs/trading-bot/SAFETY.md` for the
project's safety model as a whole.
