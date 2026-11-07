# Safety

## What this system is

A research platform for evaluating crypto accumulation strategies on
Binance.US market data. It answers questions of the form *"would this plan have
done better than a simple benchmark?"*

## What this system is not

It is not an autonomous real-money trading system. It cannot place an order and it cannot
move money. It can, through a separate and deliberately isolated subproject,
read one account's commission rates and balances — and nothing else.

**Live execution is intentionally absent.** That is a design property, not a
to-do item: no part of this repository contains a code path that submits,
amends or cancels an order, or that moves funds. The two sections below state
what each zone can do and how each claim is enforced.

## Separate zones and explicit capability limits

The repository is split into zones with separately-verified guarantees. This
split is the whole design, and collapsing it into one vague "the repo is safe"
statement would destroy the useful part.

| Zone | Can it authenticate? | Can it trade or move funds? |
| --- | --- | --- |
| `crypto_research/` — the research engine | **No.** No signing code exists. | No. |
| `account_tools/` — the account reader | Yes, read-only. | **No.** No such endpoint exists in it. |
| `desktop_app/` — the desktop window | **No.** No signing code, no credential field. | No. |
| `external_intelligence/` — external observations | No authenticated fetching. | No. |
| `paper_trading/` — practice/research models | No. | No. |
| `market_capture/` — public stream recorder | No. | No. |
| Tests, tools and documentation — support | No product authentication workflow. | No. |

### Zone 1: the research engine cannot authenticate

Everything a backtest run executes lives here, and it has no way to
authenticate at all:

1. **No signing.** Binance.US authenticated endpoints require an HMAC-SHA256
   signature. Nothing in `crypto_research/` imports `hmac` or computes a
   signature, so an authenticated request cannot be formed.

2. **No credentials.** No API key or secret is read, stored or referenced.

3. **GET only, whitelist only.** `crypto_research/binanceus/transport.py`
   issues `GET` and nothing else, and refuses any path not in
   `PUBLIC_ENDPOINTS` — market data exclusively. It rejects a credential
   header outright if one is ever attached.

4. **It cannot reach the account reader.** No engine module imports
   `account_tools`, no engine module imports the CLI or the auditor (which is
   how the transitive path is closed), and a test re-imports the whole engine
   in a fresh interpreter with `account_tools` made un-importable to prove no
   chain exists. A backtest therefore cannot read live account state even by
   accident, which is what keeps a run reproducible from stored data alone.

### Zone 2: the account reader cannot trade

`account_tools/` authenticates, because reading your real commission rates is
the one thing public data cannot provide. Its power is bounded structurally:

1. **GET only.** There is no code path that issues another method.
2. **Five endpoints.** `ACCOUNT_READ_ENDPOINTS` lists `/api/v3/account`,
   `/api/v3/account/commission`, `/sapi/v1/asset/tradeFee`,
   `/sapi/v1/account/status` and `/sapi/v1/account/apiTradingStatus`. A path
   outside it is refused before a request is built. A test asserts the list
   contains no `order`, `withdraw`, `transfer`, `convert`, `oco` or `otc` path,
   and that it stays small — every addition widens what a leaked key could
   reach through this tool.
3. **Credentials live only in the environment.** Read at call time from
   `BINANCEUS_READ_API_KEY` and `BINANCEUS_READ_API_SECRET`, never written to
   disk, never placed in a config or a run record. `Credentials.__repr__` is
   overridden so a stray log line or traceback cannot leak the secret, and API
   error text is redacted before it is raised.
4. **It emits text, not actions.** Output is configuration for a human to
   review and paste. It does not edit configs or experiments.

### Zone 3: the desktop app cannot authenticate

`desktop_app/` is a window over the research engine, for someone who would
rather not use a terminal. It makes the *same* claim as the research engine and
is audited under the same rules (`DESKTOP_APP_RULES` in
`crypto_research/safety.py`):

1. **It only calls into `crypto_research`.** It never imports `account_tools`,
   and its imports must stay inside an explicit standard-library allow-list.
   `tests/test_desktop_app_safety.py` asserts both by walking every import.
2. **No field accepts a credential, and no code mentions one.** A test fails if
   any source file contains words like `api_key` or `password`. If real fee
   rates or balances are ever wanted, that stays a separate manual step in a
   terminal, at `account_tools`' own hidden prompt -- never a box in this window.
3. **Nothing is spent.** Its screens read saved prices and run practice
   backtests. The *Safety check* screen runs the audit on demand and reports
   the result in plain words.
4. **Its one audit opt-out is pinned.** Tkinter's list and text widgets clear
   themselves with `.delete()`, which the audit's HTTP-DELETE pattern cannot tell
   apart from a web request. Those few lines carry the `# safety-audit: allow`
   marker, and a test fails if the marker appears on any line that is not a
   `.delete(` call.
5. **It does not write to the research log.** A starting-month view is dozens of
   exploratory backtests, so each finished plan is appended as one line to
   `experiments/gui_plans.jsonl` instead of to `experiments/`.

### The audit

`crypto_research/safety.py` audits each zone against its own rule set, and
`tests/test_safety.py`, `tests/test_account_tools.py` and
`tests/test_desktop_app_safety.py` assert that each audit passes, that each can actually fail when fed a violation, and that
signing is permitted in Zone 2 while order placement is forbidden in both.

```bash
python -m crypto_research safety-check
```

### What this does and does not buy you

Being precise, because this is the part worth being precise about: the earlier
absolute property — *no credential exists anywhere, therefore no order is
possible* — has been traded for a narrower one: *the code that could
authenticate contains no order path, and the code that runs experiments cannot
authenticate*.

That is a weaker guarantee. It is still a real one, and it is still enforced
by structure rather than by intention. But the residual risk is now yours to
manage rather than the architecture's to eliminate: if the API key you supply
has trading permission enabled, then **something else** on your machine could
use it, even though this tool will not. Which is why the next section matters.

## The API key you supply

Create it with **reading permission only**. Specifically, on the Binance.US API
management page:

- **Enable Reading** — yes, this is all the tool needs.
- **Enable Spot & Margin Trading** — **off.**
- **Enable Withdrawals** — **off.**
- **IP access restriction** — restrict to your own IP if your setup allows it.
  This is the single most effective control, because a key that only works from
  one address is far less useful to anyone who obtains it.

Then set it in your own shell, never in a file in this repository:

```bash
export BINANCEUS_READ_API_KEY="<paste key>"
export BINANCEUS_READ_API_SECRET="<paste secret>"
```

`.gitignore` excludes `.env` and `.env.*`, and the audit fails the build if a
credential *value* appears in any tracked file. Neither of those helps if a
secret is pasted into a chat, a commit message or a screenshot, so do not.

### A limitation to be honest about

**This tool cannot verify that your key is read-only.** Binance.US exposes no
endpoint reporting a key's own permissions, so `account_tools check` reports
*account*-level flags (`canTrade`, `canWithdraw`) which are not the same thing.
Verifying the key's own restrictions is a manual step on the API management
page, and the tool says so rather than implying it has checked.

### Rotate it when you are done

The research workflow needs the key for a few seconds, once, to read rates and
balances. It is not a standing requirement. Reading the values and then
deleting the key removes the residual risk entirely, and the measured rates stay
in your configs where they belong.

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

## Buy-only research configs; simulated exits in the practice agent

`ExecutionConfig.allow_sell` defaults to `False`, and a configuration file
cannot switch it on: the config parser raises if it sees `allow_sell = true`.
Enabling exits changes the research question, so it has to be a deliberate code
change with its own tests, not a flag flip.

The simulator does contain a sell settlement path, unreachable in V1. It is
there so the engine stays general and so the sell path is reviewable before
anything depends on it.

The desktop practice agent is a separate local simulator with explicit buy and
sell tests. It starts with $200 pretend cash, caps each coin's entry at $50,
and never adds deposits or borrowing. It uses the existing public GET client,
not account access. Its SQLite transaction commits wallet changes and activity
together, rechecks pause state before simulation, and prevents overlapping
checks from repeating a purchase. Invalid data prevents simulation. A $40 loss threshold is
a stop trigger, not a guaranteed maximum loss; price gaps can exceed it.
An explicitly started, fixed local worker continues checks with the GUI closed.
One OS lock owns both sessions; per-session threads isolate slow requests, and
the heartbeat remains independent of those requests. Saved pause flags and
control revisions are rechecked before simulation, including pause/resume races.
Data-fetch failures retry with bounded backoff without changing cash, decisions
or labels; simulation errors and risk halts remain paused. Reopening never
resets or pauses a session. Sleep/shutdown interrupt coverage, and no missed
decisions or learning outcomes are invented. There is no login auto-start.
Learning is limited to three predefined entry thresholds, with forward discovery
and a separate validation phase. It cannot change position/spending limits,
exit thresholds, code, symbols or transport permissions. Shadow wallets share
the same simulation routine and observations; they never fund the primary wallet.
The advisory opportunity predictor stores forecasts before delayed labels and
cannot change the trade strategy or risk limits, even after model validation.
Its incremental parameters, frozen checkpoints and rollback events are local
JSON/SQLite records. A prediction-only savepoint isolates computation failures
from valid wallet changes; no account, model-service or execution access is added.
Independent 4h/24h studies reuse the delayed-label engine in cost-scoped tables,
without resetting the original predictor. Imported fee files contain rates and
provenance only; they cannot grant account access. The public recorder is a
separate audited zone with one optional WebSocket dependency and a fixed allow-list
of Binance.US streams. It never sends application trade messages. Top-20 snapshot
recording and conservative replay do not imply full-book or passive-fill accuracy.
Physical-metals calculations are local manual inventory/scenario arithmetic;
there is no path to buy metal, sell derivatives or obtain an insurance policy.

## No martingale, no doubling down

Position sizing in V1 is either a fixed fraction of a fixed pool
(Benchmark 1) or a fixed fraction of an explicitly declared contribution
(Benchmark 2). Neither reacts to prior outcomes. The dip-weighted research
strategy can increase a simulated buy after a price fall, but its tilt is capped
and funded from a finite pool. It does not borrow or implement unbounded doubling.

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
thing that spends money is a model that can spend money by accident. The
zone split described above is the first instance of this rule, and
`account_tools` is deliberately a sibling of the research engine rather than a
module inside it for exactly that reason.

The boundary is the strategy interface. A strategy receives a
`StrategyContext` and returns `OrderIntent` records. It gets no client, no
database handle, filesystem service, network client or wall clock through that
interface. This is an API boundary, not an operating-system sandbox: trusted
Python strategy code can still import modules. The simulator has no live order
placement path. Running untrusted strategy code would require process isolation.

Whatever comes later — a model, a signal service, a news reader — sits behind
that same boundary and returns intents into a simulator. If paper trading is
extended into live execution, it must become a **separate process** with its own
controls; the research code must not grow a live execution mode. Current paper
rehearsal invokes the backtester with pretend money only.

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
