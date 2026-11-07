# Swing, execution and physical-metals research

All additions are research-only. Existing wallet balances, limits, practice
hours and one-hour prediction history remain unchanged. Loading the updated
desktop requires a restart; reopening preserves history and requires Start
practice. No order, hedge, physical sale or insurance policy is placed.

The fixed forward-practice universe is BTCUSD, ETHUSD, SOLUSD, ADAUSD and XRPUSD.
The waiting board and public recorder use the same list. Every coin must pass
the same current market-status, completed-candle, quote and liquidity checks.
New coins learn from newly observed data; no earlier trades or hours are invented.
Existing BTC/ETH positions and recorded learning stay intact. Simultaneous new
entries divide available cash equally, capped at $50 per coin and rounded down
to cents; allocations below $10 wait. Existing holdings are not rebalanced.
The shared advisory predictor retains its saved BTC/ETH identity feature and
uses neutral identity for SOL/ADA/XRP, alongside their own market features.
Historical scores span the earlier and expanded universes; they are not evidence
of prior training or validated performance on the newly added coins.

## Swing specialists

The Swing & execution tab shows independent 4-hour and 24-hour advisory models.
They use the current hourly trend, volume, volatility and market-context features
but learn distinct outcomes. Each coin is sampled no more often than its horizon,
so its target windows do not overlap. Coins remain correlated. Each model uses
separate incremental training, frozen future validation, condition-specific
scores and rollback events, inherited from the opportunity learner.

Fresh future quotes are required within five minutes of each target; missed
outcomes expire, without backfill. Evidence can take months, especially for
24-hour models requiring 100 training and at least 100 later validation outcomes.
These are not deployed swing-trading policies. The original one-hour model is
not reset or relabeled.

Hypothetical opportunity returns are compared against cash, a fixed 0.2% trend
filter and all opportunities, plus increased-cost stress. Percentages are
normalized opportunity results, not investable portfolio returns; they omit
position overlap/allocation and intrahorizon stop handling. Stress increases
fees/slippage to twice the input, capped at 10% each. These tests do not prove
profitability or equivalence to professional trading.

## Fee provenance

Default costs remain assumed, not account verified. Obtain reported per-symbol
rates locally through the existing read-only account tool:

```powershell
python -m account_tools fees --symbols BTCUSD,ETHUSD,SOLUSD,ADAUSD,XRPUSD --research-profile data/reported_fees.json
```

Select the resulting JSON with Import reported fee snapshot in Swing & execution.
Use account details only in the existing local account-tool workflow, never chat
or the desktop GUI. The export includes symbols, rates and observation time, not
balances or signing material. It does not prove current all-in fees: discounts,
additional charges, rebates, tax and subsequent fee changes require review.
Imported files are not independently authenticated. Old snapshots are flagged.

On 2026-11-07, the official [fee schedule](https://www.binance.us/fees) and
[April 22 announcement](https://blog.binance.us/zero-fee-trading/) confirmed
0% maker / 0.02% taker for ordinary Advanced Spot pairs, including the five
practice markets. This is a published-rate scenario, not an authenticated
account report. Local read-only account access was unavailable during setup;
account-specific rates/discounts remain unverified. The profile records its
verification type, source URL and observation time. Cash deposits, transfers,
One Click Buy/Sell and tax costs are outside this spot-fee profile. Historical
studies apply current costs uniformly as a scenario; they do not reconstruct
the user's historical account fees. The original 0.4%-per-side practice ledger
keeps its original costs, preserving continuity rather than rewriting returns.

Older BTC/ETH-only profiles remain readable. Missing new-coin rates use an explicit
assumed 0.4% per side and are flagged in the research panel; they are not presented
as reported account rates. Importing rates for all five markets removes those
fallbacks. New research conservatively uses the highest reported or assumed taker
rate across the five coins. Changed cost/coverage profiles get separate hash-scoped studies, preserving
old ones; identical rate refreshes retain studies. Practice's existing costs and
limits do not change. Profile files are ignored by Git.

## Public capture and execution diagnostics

The optional recorder requires `websockets==17.4` (or package extra `recorder`).
The engine and practice models remain standard-library-only. The recorder connects
only to fixed public Binance.US aggregate-trade, best-quote and top-20 depth
streams. No application messages for trading or authentication are sent.

Start public stream recording runs for up to 30 minutes in a worker, independently
of practice. Use its separate Stop control; closing the app also stops it. Data
is saved in `data/market_capture.sqlite`, with raw valid frames, receipt times,
session boundaries and gap markers. Partial-depth messages have no exchange
timestamp; none is fabricated. Protocol heartbeat handling comes from the
WebSocket library. Disconnects reconnect with backoff, and trade sequence gaps
are recorded. Depth snapshot update IDs are not assumed consecutive.

The recorder stops at 100,000 events or 128 MiB instead of deleting history.
One writer may own a file. Archive full captures and choose a new file via CLI.
Old status timestamps do not prove that a recorder is still running.

```powershell
python -m market_capture record --seconds 1800 --database data/market_capture.sqlite
python -m market_capture status --database data/market_capture.sqlite
python -m market_capture evaluate --database data/market_capture.sqlite --symbol BTCUSD --notional 100 --hold-seconds 60 --latency-ms 100 --fee 0.004 --slippage 0.001
```

The last command is a hypothetical order-size scenario, not an investment-size
recommendation. Replay chooses snapshots after the decision plus latency,
rejects stale/missing/gapped windows, walks visible asks/bids, and reports
insufficient entry/exit depth instead of inventing a complete fill. It repeats
with increased costs. Only the first eligible window is evaluated: this is an
execution diagnostic, not a backtested or trained scalping policy. Passive
fills, queue rank, market impact beyond displayed depth and full-book
reconstruction are not modeled. A successful estimate is not a fill guarantee.

Official stream semantics: [Binance.US API](https://docs.binance.us/).

## Continuous collection and historical training

Practice agent > Swing & execution now includes separate background-collection
controls and historical-training controls. Start background collection launches
a fixed public-data worker independently of Tk. Closing the interface stops its
old 30-minute recorder, but leaves the explicitly started practice worker and
background collector running. Stop background collection writes a stop request;
wait for a stopped heartbeat. Sleep, shutdown and network outages interrupt
coverage. Nothing is installed to start at Windows login, and no computer is kept
awake. After reboot, start collection again explicitly.

Captures live in ignored `data/learning_capture/`. Files rotate at 15-minute
boundaries or existing per-file event limits. Each is an independent session;
boundaries and disconnects must not be treated as continuous order-book coverage.
The worker stops near a 512 MiB directory quota (16 MiB reserved for overhead)
or 256 segments, preserving every file. The quota is checked every second;
it is a bounded operating budget rather than an exact filesystem allocation.
Archive full captures and select a new directory with the CLI. The interface
shows heartbeat/data age and current-process event counts. Old heartbeats are
explicitly unconfirmed; a held owner lock prevents a second collector even if
the heartbeat is stale. No process ID is killed to stop collection.

```powershell
python -m market_capture collect --directory data/learning_capture
python -m market_capture stop --directory data/learning_capture
python -m paper_trading.historical download --days 365
python -m paper_trading.historical evaluate
python -m paper_trading.historical status
```

Download/update 12 months of hourly history saves completed public hourly candles
for all five USD markets in `data/learning_research/history.sqlite`. Data gaps,
incomplete downloads and invalid bars remain visible. An incomplete download
cannot be evaluated. Refreshing history does not reconstruct practice trades or
add observed practice hours. Saved old bars outside the latest requested date
range are excluded from that study.

Train and evaluate saved history creates a new candle-only logistic study for
1h, 4h and 24h outcomes. Its features use only the preceding 50 completed candles,
with fixed scaling, volume, volatility, returns and explicit coin indicators.
It cannot train the existing quote/flow model as if historical spreads and order
books had been observed. The entry approximation is the next candle open; exit
is the last close of the chosen holding horizon. These are hypothetical prices,
with conservative reported/assumed fees, slippage and an assumed 0.1% full spread.
Increased-cost stress doubles those inputs, each capped at 10%.

Each horizon uses three expanding, chronological train/test splits. All coins
share the same boundaries. Training labels must end strictly before testing
starts, and test labels must end before the next test period; overlapping label
windows at boundaries are purged. Weights remain frozen during each test.
Earlier test outcomes may become legitimate past training data for a later fold.
No parameters are selected by looking at those test results.

Reports include prediction error against a training-only constant-probability
baseline, per-coin and market-condition errors, gap exclusions, selected
opportunity returns and a fixed trend comparison. Selected hypothetical trades
do not overlap within each coin/fold. These are opportunity diagnostics, not
portfolio returns, and correlated labels are not independent samples. Brier
error alone does not establish that probabilities are calibrated.

Every attempted fit is journaled, including failures after dataset preparation.
Exact candle inputs are frozen by content hash, alongside cost provenance,
source-code identity, feature version, fixed settings, boundaries, scores and
weights. Studies are immutable; only the latest-report pointer is replaced.
No artifact is promoted into the practice agent. Separate future validation and
a complete portfolio/execution assessment are still required before adoption.

The UI historical jobs run while this app process remains open; closing it can
interrupt a download or training job. The independent public collector has a
separate lifetime. No extra dependency beyond the recorder's existing WebSocket
library is needed.

## Frozen daily swing practice and whole-portfolio tests

The top navigation's Swing practice button opens its own full-sized screen. Enter a starting
pretend amount and choose Start swing practice. The suggested amount mirrors
the existing practice session and is editable before creating this separate
wallet. Results are expressed as percentage returns; this is not a funding
recommendation. Existing sessions resume their saved capital/model and cannot
be reset by changing that entry. State and decisions use the separate ignored
`data/swing_practice.sqlite`; the original hourly wallet, costs and learner
are not modified.

The policy freezes the last chronological 24h model, without choosing the test
period that performed best. A model score of at least 0.60 and a 20-hour average
strictly over 0.2% above the 50-hour average are both required. Scores are
uncalibrated, not proven win probabilities. All five coins share available cash,
with a cap of 25% of starting capital per position and a minimum allocation of
5% of starting capital. No borrowing is simulated. Quotes/history must be fresh,
and displayed sizes must cover estimated purchases/sales.

Exit rules are 24 hours held, a downward hourly trend, a 5% entry-price decline,
a 10% rise or a 20% loss of starting portfolio value. Stops are triggers, not
guaranteed execution prices. Illiquid exits remain held for retry; new entries
are withheld while an exit is blocked. A triggered portfolio limit permanently
stops new entries in that session. Unresolved exits may still be monitored.
Pause/close preserves holdings; it does not liquidate them. Opening the updated
app requires Start swing practice to resume minute-by-minute checks. The public
background collector has a separate lifetime and does not execute this policy.

The wallet pins its model and fee economics. Changed costs or an observation
over seven days old withhold new entries, while existing exits continue using
the saved estimated costs. Imported files are not independently authenticated.
Venue minimum sizes, rounding rules and actual order queue behavior remain
outside these fractional-fill estimates.

Test the whole swing portfolio replays each chronological test period using
its own earlier frozen model. It shares the forward policy's cash allocation
and exits. Results include an equity curve, fees, trade ledger, maximum observed
drawdown and comparisons with cash and an equal-weight buy/hold portfolio under
the same cost assumptions. Indices start at 100 for comparison, without treating
that as an actual investment amount. All positions are hypothetically liquidated
at the period's last close. The increased-cost case doubles spread, fees and
slippage. Hourly low/high data checks estimated stop/target exits; when both are
hit in one candle, the stop takes precedence. The true intrahour path and exact
fills remain unknown. Missing required candles withhold an entire period's
performance, including its comparison figures.

Portfolio studies are immutable in `data/learning_research/swing_studies/`, with
an attempt journal and a latest-report pointer. New historical training flags
an older portfolio report rather than presenting it as the latest model's test.
Negative research results do not prevent an explicitly requested pretend test;
they do not establish a reliable edge or authorize any real-money deployment.

```powershell
python -m paper_trading.historical evaluate
python -m paper_trading.swing_backtest
python -m paper_trading.swing_strategy status
```

The swing CLI supports start/check/pause for a selected separate database. A
new CLI start requires an explicit `--capital`; it performs one public-price
check, not a background trading loop. Use the desktop's Start swing practice
for independent background checks, including while the window is closed.

## Prospective swing learning

The Swing practice screen shows all five coins at once, with the frozen model's
score, the learning candidate's score, the trend and the current waiting reason.
Learning progress, Candidate comparison and Recent decisions have their own
tabs. The original hourly session remains under Practice agent; the old research
tab has no second swing controller.

The learner uses the same 13 candle features as the frozen swing model, with a
separate version and database: `data/swing_practice_learning.sqlite`. Existing
capital, positions, fees, trade records, model weights and the hourly learner
are not reset or relabeled. No old observations are backfilled. A learner
failure cannot roll back a completed wallet decision or pause the trading
simulation; it appears as a separate learning error.

Each fresh coin/hour records a prospective example, including WAIT decisions.
It saves the features and predictions before any outcome is known. A fresh bid
after 24 hours supplies the net-return label, using the entry example's saved
fee/slippage assumptions and actual observed bid/ask spread. Outcomes more than
five minutes late expire. The lowest sampled bid is recorded as observed
downside, not the true continuous intraday minimum. A backwards clock, stale
quotes or malformed features are rejected. Restarting cannot duplicate a
coin/hour example. Raw overlapping hourly labels are not independent evidence.

The candidate starts from the current model and updates only after outcomes
arrive. After at least 100 resolved labels across seven observed outcome days,
its weights are frozen in an immutable checkpoint. Subsequent quotes start a
fixed 35-day comparison with fresh, matched pretend portfolios: frozen
reference, frozen candidate, increased-cost candidate and buy/hold, plus cash.
They share starting size, risk rules, costs and observation windows; the active
wallet is not one of these comparison portfolios and is never rewritten.
Candidate training continues separately while that checkpoint is tested.

Validation predictions are scored only for nonoverlapping targets per coin,
using the actual observed outcome times. Errors are grouped into complete
seven-day blocks to reduce the influence of correlated coins and hourly labels.
Qualification is assessed once at the fixed deadline, not repeatedly until
results look favorable. The predefined research criteria require at least
100 scored outcomes, ten per coin, four complete weeks, at least ten candidate
exits, positive stressed returns, and a return more than one percentage point
above cash, reference and buy/hold. Candidate drawdown must not exceed the
reference drawdown or the predefined 1% research allowance, whichever is larger.

The weekly prediction-error criterion uses a conservative t-style lower bound
for paired weekly Brier improvement, requiring an advantage over 0.005. This is
an approximate research heuristic, not proof of statistical significance:
serially correlated weeks, limited samples and repeated candidate tests still
limit certainty. Observation gaps over two hours, missing period-end coverage,
incomplete benchmark entry liquidity, or stale/changed costs disqualify that
comparison. Missing fills are never invented. Checkpoints, shadow trades and
completed decisions remain archived; no candidate is automatically promoted.

Prospective training needs the background worker running, the swing session
active and fresh public checks; it does not need the desktop window open.
The first outcomes require a full day. Learning milestones do not lock the
current model out of trading. Pausing stops observation; closing the desktop
window leaves the explicitly started worker running. Missed
hours are not reconstructed. Cost snapshots must be refreshed before they
become seven days old; same-economic-rate refreshes retain the session and
learning study. A checkpoint waiting to start can wait for refreshed rates;
cost problems during an active comparison invalidate its qualification.
The learner stops adding data if its own database exceeds 256 MiB, preserving
history and leaving the active wallet's logic separate.

## Physical silver and gold

Physical metals is a manual USD inventory/scenario tool. Add daily purchase lots:
product, actual fine troy ounces, total acquisition cost including taxes/fees,
dealer buyback bid, spot at that quote, quote date and estimated resale costs.
No inventory is inferred from approximate holdings. Silver Eagles and generic
bullion can have different premiums; spot alone is not a resale valuation.

Dealer-bid value, gain/loss and break-even bid are shown by metal. Record sold
fine ounces only after completing a physical sale; the button only changes the
ledger. Partial sales split held/sold portions with proportional cost basis,
preserving original total cost and reducing the remaining hedgeable ounces.
Actual sale proceeds/profit are not modeled by this inventory adjustment.
Quotes are manual and may be stale;
select a held lot, edit its bid, spot, quote date or estimated costs, and use
Update selected lot's quote and estimated costs to refresh it without changing
its ounces or acquisition cost. No live or licensed metals feed is connected.
Inventory is stored locally in
ignored `data/metals_inventory.json`.

The scenario table changes metal prices and dealer premiums/discounts separately.
An optional idealized, cash-settled metal-linked put payoff illustrates downside
offsets and premium cost at a stated expiry, capped at held fine ounces. It is
not an actual quoted ETF/futures option, a contract-sizing recommendation or an
insurance policy. Contract multipliers, tracking/basis differences, expiry/early
resale, broker requirements and actual prices/costs require separate assessment.
Buying more gold/silver-linked assets increases rather than offsets a physical
long holding's exposure.

Price hedges may leave coin-premium, buyback-spread and transaction-cost losses.
They do not insure theft, damage, counterfeits or dealer default. No investment
or resale price is guaranteed. See the [CFTC/FINRA checklist](https://www.cftc.gov/sites/default/files/2024/03/Metals10Things_0.pdf)
and [CME's hedging/basis discussion](https://www.cmegroup.com/articles/case-study/case-study-hedging-risk-with-gold-futures-and-options.html).
