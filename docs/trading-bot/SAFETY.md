# Safety

## What this system is

A research platform for evaluating crypto accumulation strategies on
Binance.US market data. It answers questions of the form *"would this plan have
done better than a simple benchmark?"*

## What this system is not

It is not an autonomous trading system. It is not connected to an account. It
holds no credentials. It cannot move money.

## Live execution is intentionally absent

There is no live order path in V1. That is a design property, not a to-do
item, and it is enforced in four independent ways:

1. **No signing.** Binance.US authenticated endpoints require an HMAC-SHA256
   signature. Nothing in this package imports `hmac` or computes a signature,
   so an authenticated request cannot be formed.

2. **No credentials.** There is no API key or secret anywhere: not in source,
   not in configuration, not in `.env.example`, not in tests, not in fixtures.
   The `.env.example` file explicitly documents that none is required.

3. **GET only, whitelist only.** `crypto_research/binanceus/transport.py`
   issues `GET` and nothing else, and refuses any path that is not in
   `PUBLIC_ENDPOINTS`. That list contains market-data endpoints exclusively;
   `/api/v3/order`, `/api/v3/account` and their relatives are absent, and the
   transport rejects a credential header outright if one is ever attached.

4. **An audit that runs in CI.** `crypto_research/safety.py` scans the whole
   repository for private endpoints, signing machinery, credential values,
   Binance.com hosts and testnet hosts. `tests/test_safety.py` asserts the
   audit passes, asserts the audit can actually fail when fed a violation, and
   asserts no module defines a function named `place_order`, `cancel_order`,
   `withdraw_funds`, `transfer` or similar.

Run it yourself:

```bash
python -m crypto_research safety-check
```

## Binance.US only

Binance.US and Binance.com are different exchanges. They list different pairs,
publish different symbol filters, apply different fee schedules and operate
under different regulation. A figure taken from one does not describe the
other, and a backtest built on the wrong one is not conservative, it is simply
wrong.

This codebase points only at `https://api.binance.us`. Binance.com hosts,
testnet hosts and Binance.com documentation are excluded, and the safety audit
fails the build if one appears.

## Spot only, no leverage

V1 models spot purchases with cash the configuration says is available.
There is no margin, no borrowing, no futures and no leverage of any kind.
`isMarginTradingAllowed` is recorded from exchangeInfo for completeness and is
never acted on.

## No selling in V1

`ExecutionConfig.allow_sell` defaults to `False`, and a configuration file
cannot switch it on: the config parser raises if it sees `allow_sell = true`.
Enabling exits changes the research question, so it has to be a deliberate code
change with its own tests, not a flag flip.

The simulator does contain a sell settlement path, unreachable in V1. It is
there so the engine stays general and so the sell path is reviewable before
anything depends on it.

## No martingale, no doubling down

Position sizing in V1 is either a fixed fraction of a fixed pool
(Benchmark 1) or a fixed fraction of an explicitly declared contribution
(Benchmark 2). Neither reacts to prior outcomes. There is no code path that
increases size after a loss, and any future strategy that proposes to do so
should be rejected on sight: with a finite pool, doubling after losses has a
guaranteed terminal state and it is ruin.

## Existing holdings are not cash

A starting holding is a position. It is marked to market and it is never
spendable. There is no function that converts a holding into cash, because V1
does not sell. `tests/test_portfolio_and_cash.py` asserts a starting BTC
balance never appears as available cash.

Separately, initial available cash and new external contributions live in
different ledger buckets and cannot be merged. See
[BACKTESTING.md](BACKTESTING.md).

## USD is not USDT

They are distinct assets with distinct balances everywhere in this codebase.
Valuing USDT in USD requires either the real `USDTUSD` price in the dataset or
an explicitly configured assumed rate that is recorded in the run record. With
neither, valuation raises `ValuationError` rather than quietly assuming par.

A depeg is exactly the situation where a stablecoin assumption would matter
most, and exactly the situation where a hard-coded `1.0` would hide it.

## The research model and the execution system stay separate

They are separate for a reason that survives V1: a model that can reach the
thing that spends money is a model that can spend money by accident.

The boundary is the strategy interface. A strategy receives a
`StrategyContext` and returns `OrderIntent` records. It gets no client, no
database handle, no filesystem access, no network and no clock. It cannot
place an order because it is not given anything that could.

Whatever comes later — a model, a signal service, a news reader — sits behind
that same boundary and returns intents into a simulator. If paper trading is
ever built, it becomes a **separate process** that consumes intents; the
research code does not grow an execution mode.

## Future LLM and news inputs are untrusted

V1 has no LLM or news processing. When it is considered, these rules apply
from the first line of code:

- **Model and news output is data, never instruction.** A headline, an article,
  a social post or a model completion is an untrusted string. It is parsed into
  a typed, range-checked value or it is discarded. It is never `eval`-ed,
  never used to build a query or a path, and never interpreted as a command.
- **Prompt injection is assumed, not guarded against by hope.** Any text
  fetched from the internet may contain instructions aimed at the model. The
  only reliable defence is that the model has no capability worth hijacking:
  it must sit behind the strategy boundary described above, where the strongest
  thing it can emit is a proposed order that the simulator will then
  independently validate against balances, buckets and exchange filters.
- **Signals are versioned and point-in-time.** A news or model signal must
  carry the timestamp at which it was *available*, and must be stored the way
  bars are: with a `source_received_at`. A sentiment score backfilled from an
  article published after the decision time is look-ahead bias with extra
  steps, and it will flatter a backtest enormously.
- **Every model-driven variant must beat the benchmarks out of sample** before
  it is taken seriously. See [EXPERIMENTS.md](EXPERIMENTS.md).

## No model may change its own limits or permissions

Stated plainly because it is the rule most likely to be eroded by convenience:

**No model will be permitted to change capital limits, change permissions,
modify code, widen its own scope, or promote itself to a higher level of
autonomy.**

Concretely:

- A capital limit, a position cap, a list of eligible assets and a cash
  allocation are **configuration**, authored by a human, hashed into the run
  record. A strategy reads them. Nothing a strategy returns can alter them.
- A strategy cannot write to the configuration, the database, the experiment
  registry or its own source. It has no handles to do so.
- Deciding that a model has earned more autonomy is a human decision, made
  from out-of-sample evidence, and implemented by a human editing code. It is
  never a conclusion the system can reach about itself.
- If a future component ever proposes its own promotion — larger size, wider
  permissions, fewer checks — that proposal is a finding to be reviewed, not
  an action to be executed.

## What a human still has to do

This system cannot lose money, and it also cannot make any. Every real-world
action — funding an account, placing an order, holding an asset — remains a
human decision taken outside this tool. The tool's job is to make that decision
better informed, and to be honest about how uncertain the information is.

## Threats this system does not address

Stated so they are not mistaken for handled:

- **Exchange risk.** Custody, insolvency, withdrawal freezes and account
  restrictions are outside the model entirely.
- **Overfitting.** The framework records enough to *detect* overfitting; it
  cannot prevent someone from running a hundred configurations and reporting
  the best. See [EXPERIMENTS.md](EXPERIMENTS.md).
- **Survivorship and listing bias.** The candidate assets are chosen today,
  with knowledge of which assets still exist and are liquid today. A backtest
  over 2024 on assets selected in 2026 is flattered by that choice, and no
  amount of engineering fixes it.
- **Data revision.** Binance.US can republish history. Dataset hashes make a
  revision detectable; they do not make it not have happened.
- **Tax and regulation.** Not modelled at all.
