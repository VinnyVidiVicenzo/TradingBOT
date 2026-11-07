# crypto-research

A controlled research and paper-trading rehearsal platform for evaluating crypto
accumulation strategies on **Binance.US** market data.

The question it exists to answer: *does added market information improve
accumulation decisions relative to simple benchmarks?* Not "does this make
money" — over a rising window almost anything does — but "does this beat a
do-nothing baseline, out of sample, by more than it costs?"

## This is not a trading bot

**Nothing here can place an order or move funds.** The repository is split into
seven zones with separate restrictions:

| Zone | Authenticates? | Can trade or move funds? |
| --- | --- | --- |
| `crypto_research/` — the research engine | **No.** No signing code exists in it. | No. |
| `account_tools/` — read-only account reader | Yes, read-only. | **No.** No such endpoint exists in it. |
| `desktop_app/` — the desktop window | **No.** No signing code, no credential field. | No. |
| `external_intelligence/` — external observations | No authenticated fetching. | No. |
| `paper_trading/` — practice/research models | No. | No. |
| `market_capture/` — public stream recorder | No. | No. |
| Tests, tools and documentation — support | No product authentication workflow. | No. |

The engine cannot even reach the account reader: no engine module imports it, and
a test re-imports the whole engine with `account_tools` made un-importable to
check that import boundary. The desktop app calls the research engine. The
following command checks selected forbidden patterns and credential assignments;
it is a regression check, not a complete security proof:

```bash
python -m crypto_research safety-check
```

The guarantees, their limits, and the API-key setup: [docs/trading-bot/SAFETY.md](docs/trading-bot/SAFETY.md).

Scope: Binance.US only, spot only, no leverage, no borrowing, no margin, no
futures, no martingale sizing. Research configs remain buy-only; the practice
agent can simulate buys and sales with pretend money.

## Requirements

Python 3.11+. The engine and practice models use the standard library, checked
by import inspection. The optional public stream recorder uses one dependency:

```powershell
python -m pip install "websockets==17.4"
```

Package installations may select the `recorder` extra instead.

```bash
git clone <this repo> && cd crypto-research
python -m unittest discover -s tests -t .
```

## Quick start

```bash
# 1. Trading rules. The backtester needs real exchangeInfo; it hard-codes no
#    tick size, lot step or minimum notional for any symbol.
python -m crypto_research data refresh-rules --symbols BTCUSD,ETHUSD,SOLUSD,USDTUSD

# 2. Candlesticks.
python -m crypto_research data download --symbols BTCUSD,ETHUSD,SOLUSD,USDTUSD \
    --interval 1d --start 2025-03-01 --end 2026-09-01

# 3. Check the history has no holes in it (see the warning below).
python -m crypto_research data gaps --symbols BTCUSD,ETHUSD,SOLUSD --interval 1d

# 4. Establish the floor, then compare against it.
python -m crypto_research run configs/benchmark0_hold.toml
python -m crypto_research run configs/benchmark1_staged_deployment.toml
python -m crypto_research run configs/benchmark2_contribution_dca.toml

python -m crypto_research experiments list
```

## Desktop app

A plain-language window over the research, for anyone who would rather not use a
terminal. On Windows, run this once to put a **TraderBot Plan Tester** shortcut on the
Desktop and in the Start menu; after that it opens with one click, or press the Windows
key and type "TraderBot":

```powershell
powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1
```

Add `-Remove` to take the shortcuts away again; run it again if this folder moves or
Python is reinstalled. From a terminal, or on another system:

```bash
python -m desktop_app
```

Installed Python packages also expose `crypto-research-desktop` and
`crypto-research-paper`. Launch the installed desktop command from the project
workspace so it can find `data/`, `results/` and `experiments/`; these local files
are not bundled into the package. This is not a self-contained executable.

The shortcut runs `run_desktop_app.pyw` with `pythonw.exe` (no console window). It
names Python explicitly because double-clicking a `.pyw` file only works once Windows
has been told to open that kind of file with Python, and often it has not.

Six screens: **Home**, **What we've found so far** (the write-ups in `results/`,
led by `FINAL_REPORT.md`), **Try your own plan**, **Your past plans** and **Safety
check**, plus **Practice agent**. Hover over a label for a plain-English explanation.

### Automatic practice agent

Open **Practice agent** and press **Start practice**. It preserves the existing
pretend wallet (initially a $200 test scenario, not a proposed investment amount),
watches BTCUSD, ETHUSD, SOLUSD, ADAUSD and XRPUSD public prices, and checks about once a
minute in the independent worker while the computer is awake. Its starter rules compare 20 and 50 completed
hourly price averages. Initially a buy requires the shorter average to be over 0.2% higher;
each position spends at most $50 including its assumed fee. Available cash is shared
equally across simultaneous qualifying entries, rounded down to cents, with a $10
minimum per entry. Existing holdings are kept, without borrowing or additional
deposits. It sells on a downward
trend, a 5% entry-price drop or a 10% rise. At estimated wallet value of $160 or
less it attempts to sell all pretend positions and pauses. Price gaps can cause
larger losses. These rules have not demonstrated a profitable edge.

The per-coin waiting board shows the latest saved trend gap, the strict entry
threshold and the estimated next completed-hour review, with a separate countdown
for the next price refresh. Learning's 168-hour discovery target is not a lock on
practice trades. Existing holdings, paused operation, old prices, available cash
and loss triggers are distinguished from a trend that simply does not qualify.
The timing is an estimate: fresh complete candle data must arrive on a later
price check; it does not promise a purchase at the displayed time. Refreshing
the countdown does not fetch data or modify the wallet. Older generic wait rows
are explained using their saved averages and threshold without rewriting history.

**Swing & execution** also offers independent background collection and separate
historical training. Background collection continues after the interface closes
until stopped or its storage budget is reached. Download hourly history for all
five coins, then train and evaluate candle-only candidates on three later test
periods per horizon. Frozen inputs and all experiment attempts are saved locally;
these research candidates cannot replace the running practice models. See the
[research guide](docs/trading-bot/RESEARCH_EXTENSIONS.md#continuous-collection-and-historical-training).

The top navigation offers **Swing practice** in a full-sized screen, with a
separate pretend wallet and editable starting amount. It uses a frozen daily model plus an hourly trend,
with cash limits and explicit exits. **Test the whole swing portfolio** compares
chronological returns, drawdowns and costs against cash and buy/hold. Published
spot rates are labeled separately from account-reported fees; original practice
history and its original fee assumptions remain intact. These experiments can
lose money and cannot place real orders.

Swing learning records prospective examples even when the strategy waits,
resolves 24-hour outcomes, and trains a separate candidate. Frozen candidates
receive fixed 35-day comparisons with matched paper portfolios. The learning
database and checkpoints are separate from the current wallet, and no candidate
changes its saved model automatically. The screen shows real sample counts,
expired outcomes, comparison progress and all five waiting reasons. Keep the app
open and the swing session active to collect new evidence; the original hourly
wallet and its learning history remain intact.

The **learning** panel tracks completed trades and realized gains/losses after
costs. In the background, three separate pretend portfolios test entry thresholds
of 0.2%, 0.5% and 1.0%, using the same public observations and the exact same
cost/risk routine. These trial balances are comparisons, not extra wallet funding.
Discovery requires at least 168 distinct observed hourly closes, at least seven
elapsed days, and ten completed trades in each compared portfolio. A qualifying
challenger must beat both $200 cash and the incumbent by at least $1 after costs,
with no larger peak-to-trough fall. The chosen challenger is then frozen and tested
from fresh equal wallets for another such period, with the same requirements.
Only a confirmed improvement changes future entry decisions; existing holdings,
the $200 starting budget, position caps, fees and exit/loss limits stay unchanged.

Learning runs only on observations received while practice is running. Paused,
offline or failed checks do not create samples, and minute checks do not masquerade
as independent hourly evidence. Too few trades or weak results mean **no change**.
The comparison results, outcomes and rule-change evidence are persisted locally;
hover over the learning panel to see trial values and completed-trade counts.
This is bounded adaptive rule selection, not a self-rewriting AI or a guarantee
of profits. Correlated trades, multiple candidate trials and changing markets
still limit what these thresholds prove. Older wallets retain their cash/holdings
and begin collecting new learning evidence on upgrade; past trades are not relabeled.

**Market context** now samples Binance.US aggregate trades over a five-minute
window and 20 order-book levels, about once a minute. It reports value-weighted
buy-initiated activity, largest sampled trade, nearby liquidity, hourly volume,
trend and volatility. These are measurements and unproven fixed heuristics,
not identified whales, investor intent or a continuous real-time feed. The
public request semantics are documented in [Binance.US's API](https://docs.binance.us/).

**Signal comparison** starts two independent copies of the saved wallet from
the same observed holdings/cash and freezes the current entry threshold in both.
Only one trial adds the market filter: at least ten recent aggregate trades,
55% buy-initiated notional, spread no wider than 0.3%, nearby sell offers covering
three times the planned spend, hourly volume at least its preceding 20-hour
average, and hourly RMS price changes below 2%. Material disagreement between
the book snapshot and current quote also withholds entry. Both trials retain
the existing exit, cost and loss rules. These thresholds are hypotheses to test,
not optimized or proven parameters. Returns and observed drawdown are reported
as percentages of the common starting value, rather than a proposed capital size.

Market samples retain measurement and receipt times, trade ID bounds, aggregate
statistics and a content hash in the local wallet database. They do not archive
the entire raw trade tape for replay. The one-page cap is 1,000 records: a full,
discontinuous, malformed or stale page is withheld from the comparison, never
silently treated as complete. Optional market-feed errors leave the existing
wallet/learner running on their previous inputs. Comparison failures commit
neither trial's partial changes. Missing periods are not reconstructed and no
comparison result can automatically activate these signals in the running agent.
New panels and measurements do not reset accumulated practice or learning hours.

Select a decision and open **Why this move** (or double-click it) to inspect the
saved explanation, conflicting trial signals, observation time and estimated
round-trip cost. **Market context** and **Signal comparison** have scrollable
details. Paid whale/wallet services and stock feeds are not connected by this update;
no subscription, additional library or credential is needed for these public samples.

**Opportunity learning** adds an advisory, incremental logistic prediction model.
It logs one opportunity per observed coin/hour even when the agent waits or
already holds that coin. It records the actual decision/reason, features, quotes,
cost assumptions and model estimates **before** the future outcome is known.
Features use fixed bounded scales for trend, hourly volatility, recent buy flow,
volume, spread, depth imbalance, missing market data and coin identity; scales
are never fitted on future samples. This uses the standard library, with no
remote AI service or API charge.

The target is a hypothetical buy now and sale about one hour later, after the
entry's saved fee and slippage assumptions. It is not a real fill or strategy
return, and does not model intrahour stops, depth consumption, taxes or lot sizes.
Only a fresh quote arriving between the target time and five minutes afterward
can label it. Offline/missed targets expire on the next check, without historical
backfill. Hypothetical outcomes and actual trade results remain separate.

The candidate learns incrementally from resolved outcomes. Stored estimates
are scored first, then training updates its weights; the running advisory
champion stays frozen. After 100 new training outcomes, a checkpoint freezes a
challenger. Only forecasts issued after that checkpoint count in validation.
Validation requires at least 100 later resolved examples and seven elapsed days,
a mean Brier-error improvement of over 0.01 versus the champion and over 0.005
versus a past-frequency baseline, plus at least ten estimates at or above 0.6
whose hypothetical net returns sum to a positive value. Those are experimental
selection gates, not statistical significance or proof of investable returns.
Multiple epochs and correlated outcomes can still overfit these gates.

Versioned JSON checkpoints with hashes, prediction/outcome rows and model events
are saved locally alongside the wallet. Two nonoverlapping blocks of 50 champion
outcomes scoring more than 0.03 worse than the frequency baseline trigger an
advisory rollback to the prior model if it scored better, otherwise to the initial
constant model. Candidate validation is cancelled and fresh training is required.
This monitors forecast deterioration; it cannot guarantee detection of every
market change. Scores are also grouped by observed market condition. Estimates
are explicitly uncalibrated, not dependable confidence percentages.

The `opportunities`, `prediction_state`, `prediction_models` and `prediction_events`
tables extend the same practice database. A prediction-only savepoint isolates
failures; it cannot reset wallet balances, learning hours, spending limits or
the trading rule. The model is **advisory only**, including after validation or
rollback: predictions cannot become trade instructions. It starts collecting
new evidence after upgrade; old observations are not relabeled or fabricated.

The **Swing & execution** tab adds separate 4h/24h models, fee-profile imports,
and explicit start/stop controls for public stream recording. **Physical metals**
tracks manual Silver Eagle/generic-bullion purchase lots, dealer-bid value and
hypothetical downside scenarios. Neither feature changes existing wallet limits
or executes a trade or hedge. See [research extensions](docs/trading-bot/RESEARCH_EXTENSIONS.md)
for setup, commands, cost assumptions, model requirements and scope limits.

The wallet and up to 100 displayed decisions are saved in
`data/practice_agent.sqlite` (the database retains the full activity log).
**Pause agent** stops new decisions and keeps holdings. Start launches an
independent practice worker. Closing or reopening the window does not pause or
reset either saved session; checks and prospective learning continue while the
computer is awake. Each screen shows the worker connection, last successful
check, retry time and observation gaps. If the worker stops, use Reconnect.
There are no recurring deposits, borrowing, automatic resets or live-order controls.
Temporary public-data failures, stale/missing history and wide quotes are
retried with 1–15 minute backoff without decisions on invalid prices. Simulation
failures and loss stops remain paused; the worker never overrides a user pause.
It uses current public data rather
than the saved daily history; starting it requires an internet connection.

Simulated buys use the ask plus 0.1%; sales use the bid minus 0.1%; each side
assumes a 0.4% fee. These are configurable-in-code research assumptions, not
verified account fees. Values estimate proceeds after sale costs. Fractional
fills do not enforce actual venue lot sizes/minimums, partial fills or queue
priority. Do not treat practice results as proof of live execution performance.

"Try your own plan" does not run from a single date. It runs the plan from the
1st of *every* month the saved prices allow, each held for the same length of
time (1, 2 or 3 years), and shows the worst, typical and best result plus a map of
every starting month -- because when you start matters more than anything else you
control. Its numbers match `results/start_date_sweep_12m.csv` to the cent
(`tests/test_desktop_app_sweep.py` checks this). The coins offered are ones that
still exist today, which flatters results; the screen says so.

Beside the plan it shows what a stock market fund and a savings account would have
done over the same starting months, bought on the same days, from the history in
`data/reference/` (read, never written). Those figures reproduce the research's own
(a 1-year stock fund: typical $235, money lost in 11 of 60 starts, worst fall 24%;
`tests/test_desktop_app_alternatives.py` checks it). Without those files the
comparison is simply left out.

It uses only the standard library and only calls into `crypto_research`. Each
finished plan is appended to `experiments/gui_plans.jsonl`; nothing is written to
the research log. Crashes are logged to `logs/desktop_app.log`.

## Read this before choosing a date range

**Binance.US USD pairs are missing 586 days of history, from 2023-07-14 to
2025-02-19** — the period USD trading was suspended. Confirmed against the live
API on 2026-09-26 for `BTCUSD`, `ETHUSD` and `SOLUSD`. The USDT-quoted pairs and
`USDTUSD` are continuous across the same range.

A USD-quoted backtest spanning that window is describing a market that was
closed. `load_dataset` refuses a dataset whose largest gap exceeds
`max_gap_intervals` (default 3 bars) rather than quietly stepping over it. The
bundled configs use 2025-03-01 to 2026-09-01, which sits entirely after the
resumption.

## What is deliberately not decided

This codebase implements the *framework*, not the portfolio. Nothing below is
settled, and none of it is hard-coded:

- **Which assets.** The three symbols in the example configs are candidates
  chosen for liquidity. Change them in configuration.
- **Equal weighting.** It is the fallback when no weights are given, not a
  conclusion.
- **Purchase cadence and tranche count.** These are the variables under test.
- **Recurring deposits.** None are committed. The contribution schedules in the
  configs are hypothetical inputs to an experiment.
- **Fee, spread, slippage and latency figures.** Every one is an unverified
  placeholder labelled `ASSUMPTION`. There is no single universal Binance.US
  trading fee — the rate depends on volume tier, promotions in force, and
  whether fees are paid in BNB. Replace the fee figures with your real ones:
  see [account_tools/README.md](account_tools/README.md).

Historical context only: starting capital was around $200, and an earlier
snapshot of roughly $206 was mostly BTC and BNB. That is context, not a current
balance, and existing holdings are never treated as spendable cash.

## The three benchmarks

| | Funds from | Behaviour |
| --- | --- | --- |
| **Benchmark 0** | nothing | Hold the starting portfolio. No orders, no fees. The floor. |
| **Benchmark 1** | `INITIAL` cash only | Deploy a fixed pre-existing pool in equal tranches on a fixed cadence. |
| **Benchmark 2** | `EXTERNAL_CONTRIBUTION` cash only | Buy from declared future deposits, never before each one is available. |

**1 and 2 are kept strictly separate.** They answer different questions — how to
deploy money you already have, versus what happens when new money arrives — and
the cash ledger enforces the split with separate buckets, so a combined number
that identifies neither effect cannot be produced by accident.

## Invariants the tests enforce

- A strategy sees only data observable at or before its decision time.
- Download time never affects when a bar becomes usable.
- Starting holdings never appear as spendable cash.
- Initial cash and contributed cash never merge.
- A contribution does not exist before its `availability_time`.
- `USD` is never treated as `USDT`; valuation raises rather than assuming par.
- Prices, quantities and notionals are validated against live exchangeInfo, and
  rounded down.
- Rejected orders are recorded as results, not swallowed.
- Two runs of one configuration produce byte-identical orders and metrics.
- No module can reach a live order path.

```
940 tests, 0 failures (9 live-API tests skipped by default)
```

Live API checks are opt-in, so the default run is offline and deterministic:

```bash
CRYPTO_RESEARCH_LIVE_TESTS=1 python -m unittest tests.test_live_binanceus
```

## Layout

```
crypto_research/
  binanceus/     public GET-only client, exchangeInfo parsing, filter enforcement
  storage/       sqlite: bars, quotes, parsed rules, raw exchangeInfo audit trail
  datafeed/      resumable download, dataset hashing, gap detection, loading
  backtest/      event loop, portfolio + cash buckets, execution, costs, metrics
  strategies/    benchmarks 0, 1, 2
  experiments/   append-only run registry
  safety.py      the zone-aware, machine-checkable safety audit
account_tools/   read-only account reader (separate zone; can sign, cannot trade)
desktop_app/     Tkinter window over the engine (separate zone; cannot authenticate)
configs/         example experiment configurations
docs/trading-bot/
tests/
```

## Documentation

| | |
| --- | --- |
| [ARCHITECTURE.md](docs/trading-bot/ARCHITECTURE.md) | Layers, the event loop, the strategy boundary. |
| [DATA_MODEL.md](docs/trading-bot/DATA_MODEL.md) | Every record, and the invariants each one carries. |
| [BACKTESTING.md](docs/trading-bot/BACKTESTING.md) | Order lifecycle, cost models, metrics, known limitations. |
| [SAFETY.md](docs/trading-bot/SAFETY.md) | Why there is no live path, and the rules for future model inputs. |
| [EXPERIMENTS.md](docs/trading-bot/EXPERIMENTS.md) | The registry, and the protocol for not fooling yourself. |

## A word on what the numbers mean

At $200 of capital, a 0.6% taker fee across 36 orders is a material share of any
edge being measured. Before believing any ranking, re-run it at a higher and a
lower fee assumption; if the ranking flips, the finding is about the fee
assumption. The framework records enough to detect overfitting. It cannot stop
anyone from running a hundred configurations and reporting the best one — that
part is on the person reading the output.
