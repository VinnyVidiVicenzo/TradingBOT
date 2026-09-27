# Experiments

## The question

Does added market information improve accumulation decisions relative to simple
benchmarks? Not "does this strategy make money" — over a rising window almost
anything makes money — but "does this beat Benchmark 0 and Benchmark 1, out of
sample, by more than its costs?"

Complexity must demonstrate incremental out-of-sample value before it is
adopted. That is the standard, and it is the reason the registry exists.

## What is recorded

Every run writes `experiments/runs/<run_id>/`:

| File | Contents |
| --- | --- |
| `record.json` | The whole record (below). |
| `metrics.json` | Metrics alone, for diffing. |
| `config.resolved.json` | The fully resolved config that was hashed. |
| `equity_curve.csv` | Per-mark value, cash, invested, units, NAV per unit, contribution inflow. |
| `orders.jsonl` | Every attempted order, accepted or rejected, with fills, fees and reasons. |
| `cash_ledger.csv` | Every cash movement, with its bucket. |

`record.json` carries:

- `run_id`, `created_at`
- `status` — `COMPLETED` or `FAILED`
- `git_commit`, `git_dirty` — a result from uncommitted code is not
  reproducible, and the record says so rather than implying otherwise
- `code_version`, `tool_versions` (Python, platform)
- `config`, `config_sha256`
- `dataset_version` — content hash, bar count, symbol count, range, interval
- `strategy_name`, `strategy_version`, `strategy_params`
- `eligible_symbols`
- `starting_holdings`, `starting_cash`, `external_contributions`
- `fee_assumptions`, `spread_assumptions`, `slippage_assumptions`,
  `latency_assumptions`, `execution_settings`
- `rate_assumptions_used` — which assumed FX rates the run actually relied on
- `date_range`
- `metrics`
- `warnings`
- `error`, `traceback` on failure

## Two rules of the registry

**Nothing is overwritten.** A run directory that already exists is never reused;
re-running the same config allocates a new run ID. Two runs of one config are
both kept so they can be compared, rather than one replacing the other.

**Failures are kept.** A run that raised is recorded with `status: FAILED` and
its traceback, and it appears in the index like any other. Discarding failed
runs is how a research log quietly becomes a record of only the results that
happened to work — and the failures are often the informative ones. A run that
failed because a config asked for a symbol with no stored data is telling you
something true about your data, not about your strategy.

## Running

```bash
# Rules first: the backtester needs real exchangeInfo.
python -m crypto_research data refresh-rules --symbols BTCUSD,ETHUSD,SOLUSD

# Then the bars a config needs.
python -m crypto_research data download-for configs/benchmark1_staged_deployment.toml

# Then the run.
python -m crypto_research run configs/benchmark1_staged_deployment.toml

python -m crypto_research experiments list
python -m crypto_research experiments show <run_id>
```

## Protocol

### 1. Establish the floor first

Run Benchmark 0 over the window. Every other number is quoted relative to it.
Then run Benchmark 1 with `tranche_count = 1` — a lump sum. Staging only earns
its complexity if it beats both.

### 2. Change one thing at a time

Each run records a config hash. Sweeping `cadence_days` across
`{1, 7, 14, 30}` gives four runs whose records differ in exactly one field,
which is what makes the comparison attributable.

### 3. Split the data before you look at it

Nominate an in-sample window and an out-of-sample window **before** running
anything, and write the split into the config names. A parameter tuned on 2024
and validated on 2024 has been validated on nothing.

### 4. Count every run you make

The registry index is the count. If twenty configurations were tried and the
best is reported, the honest description is "the best of twenty", and the
apparent edge should be discounted accordingly. This is the failure mode that
kills retail backtesting, and the index exists so it cannot be forgotten
quietly.

### 5. Sweep the cost assumptions

At $200 of capital a 0.6% taker fee across 36 orders is a material share of any
edge being measured. Re-run the comparison at a higher and a lower fee
assumption. If the ranking flips, the finding is about the fee assumption, not
about the strategy.

### 6. Check the rejections

A plan with a high rejection count is not working the way it appears to.
Sub-minimum-notional rejections mean the tranche size is too small for the
venue, and a plan that "deployed $200" while silently rejecting a third of its
orders did not do what its parameters say.

### 7. Look at entry price, not just ending value

Over a rising window every accumulation plan shows a profit, and ranking by
ending value mostly ranks how early each plan happened to buy. Average entry
price relative to the period mean, cost drag, and time-weighted exposure
separate the plan from the market.

## What would count as evidence

For a more complex strategy to displace a benchmark:

1. It beats Benchmark 0 and the lump-sum Benchmark 1 on an out-of-sample window
   it was never tuned on.
2. The margin exceeds its added cost drag, at both the low and high fee
   assumption.
3. The advantage is not concentrated in one or two periods. A strategy whose
   entire edge comes from one March is a strategy that got one March right.
4. It survives a plausible worsening of the spread, slippage and latency
   assumptions.
5. The number of configurations tried is stated, and the margin survives being
   discounted for that.
6. The behaviour has a mechanism that can be stated in a sentence without
   reference to the backtest.

Failing any of these is a reason not to adopt it. That is the intended outcome
most of the time.

## Before anything model-driven is added

Preconditions, all of them:

- The benchmarks are fully characterised on multiple windows, including a
  falling one. A 2024-only conclusion is a bull-market conclusion.
- The out-of-sample split is fixed and written down.
- Costs are measured rather than assumed, or the sensitivity sweep is standing
  policy.
- Any news, sentiment or model signal is stored point-in-time with a
  `source_received_at`, exactly like a bar. A sentiment score backfilled from an
  article published after the decision time is look-ahead bias with extra steps,
  and it will flatter a backtest enormously.
- Model and news text is treated as untrusted data, never as instruction, and
  sits behind the strategy boundary described in [SAFETY.md](SAFETY.md).
- No model can alter capital limits, permissions, code or its own scope. That is
  not negotiable and it is not a configuration option.

## Pre-registration template

Copy into the config `description` before running, so the record contains the
hypothesis rather than only the result:

```
HYPOTHESIS: <what should happen, and why, in one sentence>
IN-SAMPLE:  <dates>
OUT-OF-SAMPLE: <dates, fixed before running>
BASELINE:   <which run_id this is measured against>
SUCCESS:    <the number that would count as success, stated in advance>
RUNS SO FAR ON THIS QUESTION: <n>
```

Stating the success criterion in advance is what stops the criterion from being
chosen after the fact to match whatever came out.
