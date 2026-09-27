# Architecture

## Purpose

Determine experimentally whether added market information improves crypto
accumulation decisions relative to simple benchmarks. Everything here exists to
make that comparison honest.

V1 is research only and cannot place an order. See [SAFETY.md](SAFETY.md).

## Layers

```
                  configs/*.toml                  (human-authored, hashed)
                        |
                        v
  +---------------------------------------------------------------+
  |  crypto_research.config          resolve config -> dataclasses|
  +---------------------------------------------------------------+
                        |
       +----------------+----------------+
       |                                 |
       v                                 v
+---------------+               +-------------------+
|  datafeed     |               |   experiments     |
|  download     |               |   registry        |
|  dataset hash |               |   runner          |
|  loader       |               +-------------------+
+---------------+                        |
       |  ^                              |
       v  |                              v
+---------------+               +-------------------+
|   storage     |               |    backtest       |
|   sqlite      |               |    engine         |
|   bars/quotes |               |    portfolio      |
|   rules/raw   |               |    execution      |
+---------------+               |    costs/metrics  |
       ^                        +-------------------+
       |                                 ^
       |                                 |
+---------------+               +-------------------+
|  binanceus    |               |   strategies      |
|  transport    |               |   benchmarks 0/1/2|
|  public_client|               +-------------------+
|  parsing      |
|  filters      |
+---------------+
       |
       v
 api.binance.us   (public GET only)
```

Dependencies point downward and inward. `strategies` depends on `backtest` but
not on `storage`, `datafeed` or `binanceus`; it therefore cannot fetch, cannot
persist, and cannot see anything the engine has not handed it.

## Modules

| Module | Responsibility |
| --- | --- |
| `binanceus/transport.py` | GET-only HTTP with retry and rate-limit tracking. Refuses non-whitelisted paths and credential headers. |
| `binanceus/public_client.py` | Typed public endpoints: time, exchangeInfo, klines, tickers, bookTicker, depth. Paginates klines. |
| `binanceus/parsing.py` | Raw JSON to normalized records. Strings to `Decimal`, never through `float`. |
| `binanceus/filters.py` | `SymbolRules` / `SymbolRuleBook`. Rounding and filter enforcement from live exchangeInfo. |
| `storage/` | SQLite schema and repositories. Bars, quotes, parsed rules, and untouched raw exchangeInfo payloads. |
| `datafeed/download.py` | Resumable backfill. Stores closed bars only. |
| `datafeed/dataset.py` | `MarketDataSet` and its content hash (`DatasetVersion`). |
| `backtest/market.py` | Visible-market state and the `MarketView` that enforces point-in-time access. |
| `backtest/portfolio.py` | Cash ledger with source buckets, positions, and valuation that refuses to guess. |
| `backtest/costs.py` | Fee, spread, slippage and latency model interfaces, all labelled as assumptions. |
| `backtest/execution.py` | The order lifecycle: staleness, filters, balance, fill model, commission. |
| `backtest/engine.py` | The chronological event loop. |
| `backtest/metrics.py` | Run metrics. |
| `strategies/` | Benchmarks 0, 1 and 2, plus the `AssetUniverse` weighting helper. |
| `experiments/` | Append-only run registry and the runner that orchestrates a run. |
| `safety.py` | The machine-checkable no-live-trading audit. |
| `cli.py` | `python -m crypto_research`. |

## The event loop

`Backtester.run` drains a priority queue ordered by
`(timestamp, kind priority, insertion sequence)`. The third term makes ordering
total, so two runs of the same configuration cannot diverge.

Within one millisecond, the order is:

| Priority | Event | Why here |
| --- | --- | --- |
| 0 | `BAR_CLOSE` | A decision at a bar close must see that bar. |
| 1 | `QUOTE` | Same reasoning for top-of-book. |
| 2 | `CONTRIBUTION` | Cash available at T is spendable by a decision at T. |
| 3 | `DECISION` | The strategy runs. |
| 4 | `EXECUTION` | Orders reach the venue after latency; with zero latency, still strictly after the decision. |
| 5 | `MARK` | The equity point for T includes everything that happened at T. |

The decision grid defaults to the union of bar-close times in range, and can
be overridden per run.

## The strategy boundary

```python
class Strategy(Protocol):
    name: str
    version: str
    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]: ...
    def describe(self) -> dict[str, object]: ...
```

`StrategyContext` carries `now`, a `MarketView`, a read-only `PortfolioView`,
the `SymbolRuleBook`, the eligible symbols, a decision index and the run ID.
That is the entire surface. No client, no database, no filesystem, no clock,
no randomness.

This is the seam a future model sits behind, and the reason it can be allowed
to exist at all. See [SAFETY.md](SAFETY.md).

## Decimal discipline

Every price, quantity, fee and cash amount is a `decimal.Decimal`. Exchange
tick sizes such as `0.00001` have no exact binary representation, so a float
pipeline would produce rounding that differs between runs and platforms and
would quietly break determinism. Values arrive from the API as strings and are
converted directly. Storage writes them as `TEXT` for the same reason.

## Time discipline

One representation end to end: integer milliseconds since the Unix epoch, UTC —
the same as Binance.US.

Every record carries `source_received_at` separately from any exchange
timestamp, as provenance. The engine orders bars on *observability* —
`close_time + feed_delay_ms` — and deliberately excludes download time from it,
because when a backfill ran says nothing about when a bar became knowable. A
configured `feed_delay_ms` is the explicit way to model a consumer lagging the
venue.

## Determinism

Guaranteed by construction, and asserted by tests:

- total event ordering via the insertion sequence;
- sorted iteration over every dict that affects behaviour;
- no wall-clock reads inside the loop;
- no randomness (the transport's retry jitter is seeded and never runs in a
  simulation);
- client order IDs derived from the decision index, not the run ID;
- `Decimal` arithmetic under an explicit precision context.

## Extension points

Add a strategy: implement the protocol, register it in
`strategies/__init__.py::STRATEGY_BUILDERS`. Configs name strategies by string,
so a config file can never cause arbitrary code to load.

Add a cost model: implement `FeeModel`, `SpreadModel` or `SlippageModel` and
wire it in `config.py`. `describe()` output lands in the run record.

Add a fill model: implement `plan(FillRequest) -> Sequence[FillSlice]`.
Fractions summing below 1 leave the order partially filled.

## Deliberate non-goals for V1

No live or paper execution. No authentication. No websockets. No machine
learning. No news or LLM inputs. No selling. No optimizer. No web UI. No
deployment. Each of these is a decision to be revisited only with evidence.
