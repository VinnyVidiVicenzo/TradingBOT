# Data model

Defined in `crypto_research/models.py`. Persisted by `crypto_research/storage/`.

## Conventions

| Rule | Reason |
| --- | --- |
| All money and quantities are `Decimal` | Tick sizes like `0.00001` are not representable in binary floating point; float rounding would break determinism. |
| All timestamps are epoch milliseconds, UTC | Matches Binance.US; one integer representation removes timezone and float bugs. |
| `source_received_at` records provenance, not knowability | It says when the backfill ran. Visibility is gated on the venue close time plus an explicitly configured `feed_delay_ms`, never on download time. |
| Asset codes are opaque and never aliased | `USD` and `USDT` are different assets. So are `BTC` and `WBTC`. |
| Decimals persist as SQLite `TEXT` | `REAL` would round 8-decimal quantities and undo the whole discipline. |

## MarketBar

One candlestick. Table `market_bars`, primary key
`(exchange, symbol, interval, open_time)`.

| Field | Type | Notes |
| --- | --- | --- |
| `exchange` | str | `BINANCE_US`. |
| `symbol` | str | Venue symbol, e.g. `BTCUSD`. |
| `interval` | str | Binance.US kline interval. |
| `open_time` | int ms | Identifies the bar; Binance klines are keyed on open time. |
| `close_time` | int ms | When the bar became knowable. |
| `open` / `high` / `low` / `close` | Decimal | |
| `volume` | Decimal | Base asset. |
| `quote_volume` | Decimal or null | Quote asset. Used by the participation slippage and volume-cap fill models. |
| `trade_count` | int or null | |
| `source_received_at` | int ms | When the backfill ran. Provenance only; does not gate visibility. |
| `feed_delay_ms` | int | Modelled venue-to-consumer delay. Zero by default, set from `[data] feed_delay_ms`. |

`observable_at` = `close_time + feed_delay_ms`. This is the property the engine
orders and gates on.

`source_received_at` is deliberately **not** part of it. It records when the
backfill ran, which says nothing about when the bar became knowable: history
downloaded today was knowable at its close. Folding download time in would push
every historical bar's observability to today and empty the run window entirely
— a mistake this code made once, and now has a regression test against.

To model a consumer that lags the venue, set `feed_delay_ms` in `[data]`. That
is an explicit, recorded modelling choice rather than an artifact of when a
download happened to be scheduled. `MarketQuote` is different: `bookTicker`
carries no exchange event time, so for a quote the arrival stamp genuinely *is*
the observation time, and `observable_at` uses it.

Only closed bars are stored. A partially formed candle would change after the
fact, breaking reproducibility, and would leak information that did not exist at
decision time.

## MarketQuote

Top of book. Table `market_quotes`, primary key
`(exchange, symbol, source_received_at)`.

| Field | Type | Notes |
| --- | --- | --- |
| `bid` / `ask` | Decimal | A crossed quote (`bid > ask`) is rejected at construction. |
| `bid_size` / `ask_size` | Decimal or null | |
| `exchange_event_time` | int ms or null | `bookTicker` does not supply one, so this is usually null. |
| `source_received_at` | int ms | The only timestamp available for `bookTicker`, which is why the staleness check matters. |

Derived: `mid`, `spread`, `observable_at`.

## Ticker24h

24-hour rolling statistics. Fetched on demand for screening; not persisted in
V1, because a 24-hour window observed once is not point-in-time data and would
be easy to misuse in a backtest.

## ExchangeRuleSnapshot

Per-symbol trading rules as of one observation. Table
`exchange_rule_snapshots`, primary key `(exchange, symbol, source_received_at)`.

| Field | Source filter | Notes |
| --- | --- | --- |
| `status` | symbol | Only `TRADING` accepts orders. |
| `base_asset` / `quote_asset` | symbol | |
| `price_tick`, `min_price`, `max_price` | `PRICE_FILTER` | |
| `min_quantity`, `max_quantity`, `quantity_step` | `LOT_SIZE` | |
| `market_min_quantity`, `market_max_quantity`, `market_quantity_step` | `MARKET_LOT_SIZE` | A step of `0` means unconstrained — Binance.US really returns `"0.00000000"` here. |
| `min_notional`, `max_notional` | `MIN_NOTIONAL` and/or `NOTIONAL` | Both are read; the tightest bound wins. |
| `min_notional_applies_to_market`, `max_notional_applies_to_market` | `applyToMarket` / `applyMinToMarket` / `applyMaxToMarket` | |
| `notional_avg_price_mins` | either | Recorded; see the simplification note below. |
| `base_asset_precision`, `quote_asset_precision` | symbol | |
| `is_spot_trading_allowed` | symbol | |
| `order_types` | symbol | |
| `max_num_orders`, `max_num_algo_orders` | order-count filters | Recorded; not binding in V1, which never has an open order. |
| `raw_payload` | whole entry | Untouched, for audit. |
| `valid_from` | `serverTime` | |
| `source_received_at` | local | |

**Nothing is hard-coded.** No tick size, step size, precision or minimum
notional for any symbol appears anywhere in the source. Unknown symbols raise
rather than defaulting, because a guessed tick size makes a backtest silently
wrong.

Binance.US has been migrating symbols from `MIN_NOTIONAL` to the newer
`NOTIONAL` filter. As of 2026-09-26, `BTCUSD` still returns `MIN_NOTIONAL`.
Both are parsed, so neither migration direction breaks anything.

Filter types that this code does not interpret — `PERCENT_PRICE`,
`PERCENT_PRICE_BY_SIDE`, `TRAILING_DELTA`, `ICEBERG_PARTS`, and anything
Binance.US adds later — are preserved in `raw_payload` and are **not** treated
as satisfied. They do not bind the market-buy path V1 uses.

### Simplification worth knowing about

When `avgPriceMins > 0`, Binance checks a MARKET order's notional against an
average price over that window, not the last price. The simulator checks against
its reference price. For the $1 minimum on liquid USD pairs the difference is
immaterial; for a symbol with a large minimum and a volatile 5-minute window it
would not be. The field is recorded so this is visible rather than hidden.

### Raw audit trail

Table `raw_exchange_info_snapshots`, primary key `(exchange, fetched_at)`, holds
the complete untouched payload with a SHA-256. **Nothing is ever overwritten.**
When the venue changes a minimum notional next month, the record of what it was
this month has to still exist, or every experiment run before the change becomes
unexplainable. `distinct_hashes()` collapses the history to one row per distinct
rule set, which makes a rule change easy to spot.

## PortfolioHolding

A non-cash position supplied as a starting condition.

| Field | Notes |
| --- | --- |
| `asset` | |
| `quantity` | Must be non-negative. |
| `cost_basis_per_unit` | Optional. Only ever populated from an explicitly supplied figure. |
| `cost_basis_asset` | **Required whenever a basis is given.** A bare number is ambiguous between USD and USDT, and the constructor raises. |
| `source` | Provenance, e.g. `manual snapshot`. |
| `as_of` | |

**A holding is not cash.** It is marked to market and never spendable. There is
no code path that converts one into cash, because V1 does not sell.

A supplied cost basis is recorded but deliberately excluded from the
average-entry-price metric: averaging a known in-simulation purchase against a
pre-existing basis produces a number that looks precise and is not.

## CashLedgerEntry and cash buckets

Spendable balances, keyed by `(asset, CashSource)`.

| `CashSource` | Meaning |
| --- | --- |
| `INITIAL` | Cash you already have and are choosing to deploy over time. |
| `EXTERNAL_CONTRIBUTION` | New money that arrived from outside. |
| `SALE_PROCEEDS` | Proceeds of a sale. Unreachable in V1. |

The split is load-bearing. Benchmark 1 may spend only `INITIAL`; Benchmark 2 may
spend only `EXTERNAL_CONTRIBUTION`. Requesting more than a named bucket holds
raises rather than spilling into another bucket, so the two benchmarks cannot
quietly merge — which is the difference between measuring deployment timing and
measuring the effect of new deposits.

Every movement is journalled with asset, signed amount, bucket, timestamp and
reason, and the journal is written to `cash_ledger.csv` in the run directory.

## ExternalContribution

| Field | Notes |
| --- | --- |
| `amount` | Must be positive. |
| `asset` | `USD` and `USDT` are different contributions. |
| `availability_time` | The money does not exist before this. |
| `contribution_type` | `ONE_OFF_DEPOSIT` or `RECURRING_DEPOSIT`. |
| `label` | Free text, carried into the ledger reason. |

Distinct from initial cash: initial cash exists at `start_time`, whereas a
contribution cannot be spent, reserved or counted before `availability_time`.
The engine credits it as a `CONTRIBUTION` event at that timestamp; before then
no balance reflects it and no metric includes it. A contribution outside the run
window is ignored with a recorded warning rather than silently dropped.

**No recurring deposit is assumed.** A contribution schedule exists only if a
configuration file declares it.

## Valuation

`RateView` resolves an asset into the valuation asset in exactly three steps:

1. identity, when the asset *is* the valuation asset;
2. a pair price visible at the current simulated time (`{ASSET}{VALUATION}`, or
   its inverse);
3. an explicitly configured assumed rate, recorded in the run record as an
   assumption.

There is no fourth step. **USDT is not assumed to be USD.** Binance.US lists
`USDTUSD`, so the honest option is to include it in the dataset and price it.
With neither a price nor a declared assumption, valuation raises
`ValuationError`. A portfolio that silently values a stablecoin at par, or an
unpriceable asset at zero, reports a number that is not the truth — and a depeg
is precisely when that would matter.

## Data gaps

`DataGap` records a stretch of missing bars. Crypto trades continuously, so
consecutive fixed-width bars should be exactly one interval apart; anything
wider is an outage, a delisting or a trading suspension.

**Binance.US USD pairs are missing 586 days, from 2023-07-14 to 2025-02-19**,
covering the period USD trading was suspended. Verified against the live API on
2026-09-26 for `BTCUSD`, `ETHUSD` and `SOLUSD`. The USDT-quoted pairs
(`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) and `USDTUSD` are continuous across the same
range.

Any USD-quoted backtest window spanning that gap is invalid, and the numbers it
produces describe a market that was closed. `load_dataset` therefore **refuses**
a dataset whose largest gap exceeds `max_gap_intervals` (default 3 bars).
Smaller gaps are permitted but are recorded as run warnings rather than ignored.

Check before trusting a window:

```bash
python -m crypto_research data gaps --symbols BTCUSD,ETHUSD,SOLUSD --interval 1d
```

## Derived run records

Written per run under `experiments/runs/<run_id>/`:

| File | Contents |
| --- | --- |
| `record.json` | Full run record: config, hashes, git state, assumptions, metrics. |
| `metrics.json` | Metrics alone. |
| `config.resolved.json` | The fully resolved configuration that was hashed. |
| `equity_curve.csv` | Per-mark total value, cash, invested, units, NAV per unit, contribution inflow. |
| `orders.jsonl` | Every attempted order, accepted or rejected, with fills and reasons. |
| `cash_ledger.csv` | Every cash movement with its bucket. |

See [EXPERIMENTS.md](EXPERIMENTS.md).
