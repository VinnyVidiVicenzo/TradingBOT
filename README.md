# crypto-research

A controlled research and (later) paper-trading platform for evaluating crypto
accumulation strategies on **Binance.US** market data.

The question it exists to answer: *does added market information improve
accumulation decisions relative to simple benchmarks?* Not "does this make
money" — over a rising window almost anything does — but "does this beat a
do-nothing baseline, out of sample, by more than it costs?"

## This is not a trading bot

**Nothing here can place an order or move funds.** The repository is split into
two zones with separately-verified guarantees:

| Zone | Authenticates? | Can trade or move funds? |
| --- | --- | --- |
| `crypto_research/` — the research engine | **No.** No signing code exists in it. | No. |
| `account_tools/` — read-only account reader | Yes, read-only. | **No.** No such endpoint exists in it. |

The engine cannot even reach the account reader: no engine module imports it, and
a test re-imports the whole engine with `account_tools` made un-importable to
prove no chain exists. Both claims are enforced, not promised:

```bash
python -m crypto_research safety-check
```

The guarantees, their limits, and the API-key setup: [docs/trading-bot/SAFETY.md](docs/trading-bot/SAFETY.md).

Scope: Binance.US only, spot only, no leverage, no borrowing, no margin, no
futures, no martingale sizing, no selling.

## Requirements

Python 3.11+. **No third-party dependencies** — standard library only, which a
test asserts by inspecting every import in the package.

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
306 tests, 0 failures (9 live-API tests skipped by default)
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
