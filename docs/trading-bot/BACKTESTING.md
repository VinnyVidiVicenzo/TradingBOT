# Backtesting

## The loop

`Backtester.run` drains a priority queue of events ordered by
`(timestamp, kind priority, insertion sequence)`. Priorities within one
millisecond: `BAR_CLOSE` (0), `QUOTE` (1), `CONTRIBUTION` (2), `DECISION` (3),
`EXECUTION` (4), `MARK` (5). The insertion sequence makes the ordering total, so
two runs of the same configuration cannot diverge.

The decision grid defaults to every bar-close time in the window, and can be
overridden with `decision_times`.

## Point-in-time guarantees

A strategy sees observations only through a `MarketView` pinned to the current
decision time. It never holds the dataset.

Three independent layers:

1. **Feeding.** `MarketState` only contains what the engine has already
   ingested, and the engine ingests in observability order.
2. **Ingestion assertion.** `MarketState.ingest_*` raises `LookaheadError` if an
   observation arrives with an observability time earlier than the simulated
   clock, and the clock cannot move backwards.
3. **Read-time assertion.** Every `MarketView` accessor re-checks that what it
   is about to return was observable at or before `now`. Redundant when the
   engine is correct — which is the point: an engine bug becomes a loud failure
   rather than an inflated backtest.

Visibility is gated on `close_time + feed_delay_ms`, not on when the data was
downloaded. Download time says nothing about knowability, and treating it as if
it did would push every historical bar's observability to today and empty the
run window. Set `[data] feed_delay_ms` to model a consumer that lags the venue.

## Cash and holdings

- **Holdings are not cash.** A starting position is marked to market and is
  never spendable. There is no holding-to-cash conversion, because V1 does not
  sell.
- **Buckets do not merge.** `INITIAL` and `EXTERNAL_CONTRIBUTION` are separate
  balances. An order names its funding bucket; overdrawing that bucket is a
  rejection, not a spill into another one.
- **Contributions do not exist early.** Credited at `availability_time` and not
  a moment before.
- **USD is not USDT.** Separate balances; valuing one in the other needs a real
  price or a declared assumption.

## The order lifecycle

At `submit_time = decision_time + latency`:

| Step | Failure mode |
| --- | --- |
| 1. Register client order ID | `DUPLICATE_CLIENT_ORDER_ID` |
| 2. Sell gate | `SELL_NOT_PERMITTED` |
| 3. Look up symbol rules | `UNKNOWN_SYMBOL` |
| 4. Reference price exists | `NO_REFERENCE_PRICE` |
| 5. Reference price is fresh enough | `STALE_QUOTE` |
| 6. Apply spread, then slippage, against the trader | — |
| 7. Limit marketability (limit orders only) | `LIMIT_NOT_MARKETABLE` |
| 8. Size the order, round quantity **down** to the step | `ZERO_QUANTITY_AFTER_ROUNDING` |
| 9. Exchange filters (lot size, notional, status, order type) | `EXCHANGE_FILTER` + the specific violations |
| 10. Cash in the named bucket, or holding for a sell | `INSUFFICIENT_BALANCE` / `INSUFFICIENT_HOLDING` |
| 11. Fill model produces slices | partial fill, or `ZERO_QUANTITY_AFTER_ROUNDING` |
| 12. Commission | — |

**Rejections are results, not errors.** A staged-deployment plan whose slices
fall below the exchange minimum notional is a finding about the plan, and it has
to survive into the metrics rather than being smoothed away. Every attempted
order lands in `orders.jsonl` with its reason.

Rounding is always **down**, for both price and quantity. For a buy that is the
conservative direction, and a backtest should never round its way into a better
fill than the venue would have allowed.

`MARKET_LOT_SIZE.stepSize` of `0` means unconstrained. Binance.US really returns
`"0.00000000"` on `BTCUSD`, and treating it as a literal step would zero every
market order — there is a regression test for exactly this.

## Cost models

Every number is an **assumption** until replaced with a measured one. All are
configurable, all are recorded in the run record, all carry a `source` or `note`
field, and the defaults are labelled `ASSUMPTION`.

### Fees

```toml
[costs.fees]
maker_rate = "0.0040"
taker_rate = "0.0060"
source = "ASSUMPTION -- placeholder, not a quoted Binance.US rate"
commission_asset_policy = "RECEIVED_ASSET"

[costs.fees.per_symbol.BTCUSD]
taker_rate = "0"
source = "zero-fee promotion, dates here"
```

**There is no single universal Binance.US trading fee.** The applicable rate
depends on 30-day volume tier, on promotions in force at the time, and on
whether fees are paid in BNB. The defaults in this repository are placeholders
that match no account and no date. Replace them per experiment and say in
`source` where the figure came from.

`commission_asset_policy`:

- `RECEIVED_ASSET` (default) matches Binance spot behaviour for an account not
  paying fees in BNB: a buy is charged in the base asset it receives, so the
  full quote budget leaves the account and slightly less base arrives.
- `QUOTE_ASSET` charges the commission in the quote asset, reducing the
  affordable quantity instead.

The choice changes accumulation results, which is why it is an explicit setting
rather than a buried convention.

**A later authenticated version should read the account's actual commission
rates from Binance.US rather than assuming them.** Binance.US exposes the
applicable commission rates on authenticated endpoints; those are out of scope
for V1, which holds no credentials by design (see [SAFETY.md](SAFETY.md)). Until
then, every fee figure here is an input to be justified, not a fact.

### Spread

`FixedSpreadModel` takes a half-spread in basis points, optionally per symbol.
With `prefer_observed = true` a real top-of-book quote in the dataset wins over
the assumption; on a bar-only dataset there is no observed spread and the
assumption is used.

### Slippage

- `fixed` — flat basis points, size-independent.
- `participation` — `base + coefficient * sqrt(notional / bar_quote_volume)`.
  Square-root impact is a conventional shape, not a calibrated one. At the order
  sizes this project concerns itself with (tens of dollars against liquid pairs)
  participation is effectively nil and it reduces to the base term.

Spread and slippage always move the price against the trader. A model that let
slippage help would manufacture returns.

### Latency

Constant milliseconds from decision to venue. Constant rather than jittered
because any random source has to be seeded and recorded before it may enter a
run. Latency interacts with `max_reference_age_ms`: set latency beyond the
staleness window and every order is correctly rejected — there is a test for
that too.

## Fill models

`FillModel.plan(FillRequest) -> Sequence[FillSlice]`, each slice carrying a
fraction, a price multiplier and a maker flag. Fractions summing below 1 leave
the order `PARTIALLY_FILLED`, and only the filled portion is paid for.

| Model | Behaviour |
| --- | --- |
| `full` (default) | One slice, whole order, at the effective price. |
| `fixed_fraction` | Explicit fractions and price multipliers. Exercises the partial-fill path without order-book replay. |
| `volume_cap` | Caps the fill at a share of bar quote volume. Falls back to a full fill when volume data is absent, rather than inventing a liquidity constraint from missing data. |

`full` is the V1 default and is deliberately conservative in the sense that
matters: the cost of crossing is already carried by the spread and slippage
models, and at these order sizes a complete taker fill on a liquid pair is
realistic.

## Determinism

Asserted by `tests/test_engine.py`: two runs of one configuration produce
identical orders, identical metrics and identical equity curves.

Achieved by: total event ordering; sorted iteration over every behaviour-
affecting dict; no wall-clock reads in the loop; no randomness; `Decimal`
arithmetic under an explicit precision context; and client order IDs derived
from the decision index rather than the run ID — so an ID collision reflects a
real flaw in a strategy's schedule instead of run-to-run noise.

Data identity is separate: `DatasetVersion` hashes the actual bar values, not
the request parameters, because Binance.US can revise history. The hash
deliberately excludes `source_received_at`, so re-downloading identical history
on a different day does not invent a new dataset version.

## Metrics

| Metric | Definition |
| --- | --- |
| `starting_portfolio_value` | First mark, less any contribution credited at or before it. Such a deposit is already inside that mark's value, so it is counted once, under `total_external_contributions`. |
| `ending_portfolio_value` | Last mark. |
| `total_external_contributions` | Sum of contribution inflows, valued at arrival. |
| `total_capital_in` | Starting value + contributions. |
| `net_profit` | Ending value − capital in. |
| `net_return` | `net_profit / total_capital_in`. Contributions are capital, not profit. |
| `time_weighted_return` | NAV-per-unit return, unaffected by deposit timing. |
| `max_drawdown` | Worst peak-to-trough on **NAV per unit**. |
| `total_purchased_capital` | Quote spent on buys. |
| `total_fees_in_quote`, `total_fees_by_asset` | Commission, in quote terms and per asset. |
| `estimated_spread_cost`, `estimated_slippage_cost` | Reported separately, not merged. |
| `total_cost_drag` | Fees + spread + slippage. |
| `turnover` | Traded notional / average portfolio value. |
| `average_entry_price_by_asset` | Quote spent / base acquired, in-simulation purchases only. |
| `time_weighted_exposure` | Time-weighted invested share. |
| `uninvested_cash_by_asset`, `uninvested_cash_value`, `uninvested_cash_fraction` | Ending cash. |
| `attempted_orders`, `accepted_orders`, `partially_filled_orders`, `rejected_orders`, `rejections_by_reason`, `fill_rate` | Order statistics. |

### Contributions and drawdown

Drawdown is measured on a fund-style unit price. A contribution issues new units
at the prevailing NAV rather than inflating the series, so a deposit landing
mid-drawdown does not read as a recovery. Without this, a DCA strategy would
appear to have shallower drawdowns purely because money kept arriving.

### Why win rate is absent

`metrics.json` reports `win_rate: null` with a stated reason. V1 evaluates
*accumulation*: the position is only ever opened. Every buy in an uptrend is a
"win" and every buy in a downtrend is a "loss", so a win rate measures market
direction, not the strategy.

What discriminates between accumulation plans is average entry price relative to
the period mean, cost drag, uninvested cash, and time-weighted exposure. Those
are the numbers to compare.

## The benchmarks

### Benchmark 0 — hold

No orders, no fees, no spread. The floor. It also does **not** deploy the
initial cash pool: doing nothing means cash stays cash, which is exactly what
makes it the right comparison for Benchmark 1.

### Benchmark 1 — staged deployment of a finite pool

Measures the pool once at the first decision so the tranche size does not drift
as it is spent, then spends it in equal tranches on a fixed cadence and stops.
Draws only on `INITIAL`. A contribution landing mid-window is untouched.

Variables under test: `tranche_count`, `cadence_days`, the asset list, the
weights. `tranche_count = 1` is a lump sum, which is the comparison that matters
most.

### Benchmark 2 — periodic DCA from declared contributions

Draws only on `EXTERNAL_CONTRIBUTION`. The buy grid is fixed: it advances on
every scheduled date whether or not there was money to spend, so the schedule
does not drift with deposit timing. `cadence_days = 0` buys on the first
decision tick at or after the money lands.

**Benchmarks 1 and 2 are not merged and must not be.** They answer different
questions — how to deploy money you have, versus what happens when new money
arrives — and a combined number would not identify which effect produced it.

## Known limitations

- **A 586-day hole in USD history.** Binance.US suspended USD trading from
  2023-07-14 to 2025-02-19, so USD-quoted pairs have no bars across it. The
  loader refuses a window spanning it (see [DATA_MODEL.md](DATA_MODEL.md)); the
  USDT-quoted pairs are continuous if a longer history is needed. This also
  means USD-pair research is currently limited to roughly 2019-2023 or
  2025-onward, and the two regimes are not obviously comparable.
- **Bar-close decisions only.** Intrabar path is not modelled; a plan that
  depends on intraday timing cannot be evaluated on daily bars.
- **No order book.** Fills come from a model, not from depth replay. Fine at $8
  per order on `BTCUSD`; not fine for size.
- **One rule book per run.** A run pins the latest stored exchangeInfo snapshot.
  If rules changed mid-window, re-run against the older snapshot and compare
  rather than interpolating.
- **`avgPriceMins` simplification.** See [DATA_MODEL.md](DATA_MODEL.md).
- **Uninterpreted filters.** `PERCENT_PRICE`, `PERCENT_PRICE_BY_SIDE` and others
  are preserved but not enforced. They do not bind the market-buy path V1 uses.
- **Survivorship bias.** Candidate assets are chosen today, knowing which assets
  still exist and are liquid today. Engineering does not fix this; only honesty
  about it does.
- **Cost assumptions dominate at small size.** At $200 of capital, a 0.6% taker
  fee across 36 orders is a material share of any edge being measured. Sweep the
  fee assumption before believing any ranking.
