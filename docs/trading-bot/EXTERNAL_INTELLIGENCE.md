# External intelligence

Whale, on-chain, exchange-flow and derivatives data, treated as **experimental
research inputs**. They have to earn their place by improving a forecast beyond
what price and volume already show, out of sample, after costs. Until they do,
they are measurements a person reads, never instructions a program follows.

The one rule that shapes everything in this zone:

```
external data
   -> normalized events
   -> feature engineering
   -> research / statistical testing
   -> strategy logic
   -> independent risk controls
   -> paper execution

Never:   whale alert -> buy or sell
```

`external_intelligence/` implements the first four steps. It contains no
strategy, no order, no account access and no way to reach any of them (section 20
explains how that is enforced, not just promised).

## Where things stand

| Piece | State |
| --- | --- |
| Common provider interface, event model, append-only store | built and tested |
| Whale Alert adapter | built; reads **exports you already have**. No live feed (section 2) |
| CoinLobster adapter | built; reads exports in a project-defined flat format. No live feed (section 2) |
| Arkham, Nansen, CryptoQuant, Glassnode | documented placeholders only. Nothing is integrated, bought or scraped (section 17) |
| Features, lagged-only normalisation | built and tested |
| Latency, event studies, ablation, hypothesis registry | built and tested on synthetic data |
| Real provider data | **none has been loaded.** Every statistical result so far comes from synthetic fixtures that plant, or do not plant, an effect to prove the machinery can tell them apart |
| Live ingestion of any provider | off, pending your decision (section 18) |

Nothing here has been run against real whale data, so nothing here says anything
yet about whether whale data helps. What it says is that the pipeline can ask the
question properly.

## 1. Architecture

```
external_intelligence/
  events.py        ExternalEvent, event types/classes, timestamp checks
  attribution.py   who a provider *says* an address is (closed vocabulary)
  sanitize.py      untrusted-text handling
  errors.py
  ingest.py        raw payload -> events -> checks -> store (import_file, poll_once)
  dedup.py         duplicate and cross-provider matching
  guards.py        static check that nothing turns a feature into an action
  base/            provider interface, clock, rate limiter, GET-only transport,
                   coverage, receipt policy, health, file formats,
                   executed-activity adapter
  whale_alert/     Whale Alert adapter
  coinlobster/     CoinLobster adapter
  placeholders/    Arkham, Nansen, CryptoQuant, Glassnode (specs, no code paths)
  storage/         append-only SQLite store (data/external_intelligence.sqlite)
  features/        windows, feature definitions, point-in-time pipeline, normalisation
  research/        price lookups, baseline features, latency, event studies,
                   triggers, splits, ridge model, statistics, evidence ladder,
                   hypotheses, ablation, experiment-registry bridge
```

How it meets the rest of the repository:

* **Reused, not replaced.** Bars are the engine's `MarketBar` (its
  `observable_at` rule is mirrored for events). The store subclasses the engine's
  `Database` plumbing. Ablation runs are ordinary `RunRecord`s in the existing
  experiment registry. Money uses the engine's `Decimal` helpers.
* **Separate storage.** Events live in `data/external_intelligence.sqlite`. The
  store refuses to open `market.sqlite`; Binance.US market data and third-party
  data are never mixed in one file.
* **Its own safety zone.** `crypto_research/safety.py` gained one rule set,
  `external-intel` ("reads external data only: cannot authenticate, trade or
  move funds"). The audit that already covers the other zones covers this one.
* **Not touched.** The backtester, portfolio model, strategies, market-data
  layer, account tools, desktop app and every existing registry rule.

Data flow when a file is imported:

1. the raw payload is stored exactly as received (addressable by its hash);
2. it is split into records; an unreadable record is quarantined, not fatal;
3. the provider adapter turns each record into events, or rejects it with a reason;
4. every event's timestamps are checked; future, out-of-order and (for live feeds)
   stale events are quarantined with the reason;
5. events are inserted; a repeat of a stored event is counted and changes nothing;
6. attribution claims are recorded as history, and provider health is updated;
7. if you declared the payload complete for a window, that coverage is recorded,
   unless records were lost (unreadable, invalid or out of order). Then it is
   withheld and the report says why, because a count of zero over a window with
   holes would be a guess. Records that are deliberately out of scope (an
   unsupported transaction type, a change-only output) do not count as lost.

Nothing is ever updated or deleted: the package contains no `UPDATE` or `DELETE`
statement, and a test checks that.

## 2. Provider adapters

Every provider implements one interface (`base/provider.py`): fetch or import,
normalize one record into events, and refuse to guess. An adapter produces
`ExternalEvent`s and nothing else; it cannot see a portfolio or an order.

| Provider | What is implemented | Live feed |
| --- | --- | --- |
| Whale Alert | Reads JSON, JSON lines or CSV exports in the documented "sub-transactions" shape (`hash`, `timestamp` in Unix seconds, `inputs` / `outputs` with `address`, `owner`, `owner_type`, `amount`) and in a flat shape (`from_address`, `to_owner_type`, ...) | off |
| CoinLobster | Reads exports in a **project-defined** flat shape (`executed_activity_flat_v1`) with kinds `whale_trade`, `large_trade`, `liquidation`, `funding`, `open_interest` | off |
| Arkham, Nansen, CryptoQuant, Glassnode | A specification each; raising `ProviderNotIntegrated` if used | not applicable |

Why live ingestion is off (checked against the providers' public pages on
2026-09-28; verify again before relying on it):

* **Whale Alert** has no free data API. Its Alerts API and its Enterprise REST API
  are paid plans that authenticate with an API key. This zone cannot use a key
  by design, and buying a subscription is outside this task.
* **CoinLobster** offers keyless access through an MCP endpoint, which needs HTTP
  `POST`, and this zone's audit forbids `POST`. Its REST API needs a key and is
  on paid plans. It publishes no response schema or rate limits that could be
  coded against, and its terms exclude bulk extraction and systematic copying.

So both adapters are **importers of user-provided data**. Use an export only
within the licence it came with; do not redistribute it. Social-media scraping of
public alert feeds is not supported. The CoinLobster flat shape is defined here
because no documented schema exists; if the real payload differs, only that
adapter changes.

The one network primitive, `UrllibGetFetcher`, exists so a permitted feed can be
added later without weakening the zone. It is HTTPS-only, GET-only, sends no
body and no header except a fixed User-Agent, only contacts hosts on an
allow-list a human wrote, caps the response size, bounds the wait, refuses any
URL carrying a user name, password or credential-looking query key (`api_key`,
`token`, `signature`, ...) before sending anything, and honours a local rate
limiter. Nothing in the package instantiates it with a real host.

Timeouts, rate limits, unreadable replies and provider errors each become a
failed report and a health mark, never an exception that propagates toward a
decision.

## 3. Normalized event schema

Every adapter emits `ExternalEvent` (schema version `1`), an immutable record:

| Field | Meaning |
| --- | --- |
| `event_id` | stable id derived from provider + provider id + schema version |
| `provider`, `provider_event_id` | who reported it and under what id |
| `event_type` | one of 17 types below |
| `asset`, `quote_asset` | the coin (USD and each stablecoin are distinct assets), and the quote if any |
| `blockchain`, `exchange` | where, as the provider states it |
| `event_time`, `provider_publish_time`, `system_received_time` | three separate timestamps (section 4) |
| `estimated_received_time`, `received_time_basis`, `assumed_delay_ms`, `received_time_assumption` | the recorded estimate when no receipt time was observed |
| `amount_asset`, `amount_usd` | amounts, or missing. Missing stays missing |
| `value`, `value_unit` | a reading that is not an amount (a funding rate), with its unit |
| `source_address`, `destination_address` | as given |
| `source_entity`, `destination_entity` | the provider's names, cleaned, stored apart from the raw transfer |
| `source_entity_type`, `destination_entity_type` | mapped from the provider's own type by exact match, else unknown |
| `transaction_hash`, `market_symbol` | where applicable |
| `side` | set **only** for an executed trade whose provider states buy or sell |
| `metadata`, `raw_payload_reference`, `schema_version` | bounded provider extras; pointer to the stored raw payload |

Event types, grouped by evidentiary class:

* **On-chain transfer:** `LARGE_TRANSFER`, `EXCHANGE_INFLOW`, `EXCHANGE_OUTFLOW`,
  `EXCHANGE_TO_EXCHANGE`, `STABLECOIN_MINT`, `STABLECOIN_BURN`, `TOKEN_MINT`,
  `TOKEN_BURN`, `TREASURY_TRANSFER`, `UNKNOWN_TRANSFER`
* **Executed trade:** `WHALE_TRADE_BUY`, `WHALE_TRADE_SELL`, `LARGE_EXECUTED_TRADE`
* **Liquidation:** `LONG_LIQUIDATION`, `SHORT_LIQUIDATION`
* **Derivatives state:** `FUNDING_UPDATE`, `OPEN_INTEREST_UPDATE`

Things an event deliberately cannot say: there is no bullish or bearish field, no
score, no direction and no confidence on an event, and a test asserts that no such
field exists. Validation refuses negative or non-finite amounts, a `side` on a
transfer, a buy or sell with no explicit side, a funding update with no value, and
a receipt basis that does not match the times present.

The store (`storage/`) holds raw payloads, events, attribution history,
quarantined records, declared coverage, provider-health snapshots, canonical
groups and dedup ambiguities. Money is stored as text so no precision is lost.

## 4. Timestamp semantics

Three timestamps, never merged:

| Timestamp | Meaning |
| --- | --- |
| `event_time` | when the underlying thing happened (block time, trade time) |
| `provider_publish_time` | when the provider says it exposed the event, if it says |
| `system_received_time` | when **our system** actually obtained it: an observation, not an inference |

**The backtest rule:** a simulation at decision time `T` may use an event only if
`system_received_time <= T`. The event's own time is never enough: a transfer that
happened an hour ago and reached us a minute ago is not usable an hour ago.

A file of historical events normally has no genuine receipt time. Every import
must therefore state a `ReceiptPolicy` (there is no default):

| Policy | Result |
| --- | --- |
| `none` | no receipt time; events are stored and never available to a simulation |
| `observed_now` | receipt time is the moment of ingestion (right for a live poll; for an old file it makes the events usable only from today) |
| `observed_field` | the record carries our own capture time from a log kept at the time; without one, no receipt time |
| `assumed_delay(seconds, reason)` | an **estimate**: `max(event time, publish time) + delay`, with the reason written into the event |

An estimate is stored in its own fields (`estimated_received_time`,
`assumed_delay_ms`, `received_time_assumption`) and `received_time_basis` says
`ASSUMED_DELAY`. Simulations must opt in with `allow_estimated=True`, and an event
whose basis is `UNKNOWN` is never used at all. Blockchain time is never silently
treated as the time we could have acted.

At ingestion, timestamps are checked (`timestamp_problems`): an event or receipt
from the future, a publish time before the event, a receipt before the event or
the publish, and (only for live feeds that set a limit) an event too old to be
news are quarantined with the reason. A small tolerance (5 seconds by default)
absorbs ordinary clock skew between a provider and us.

## 5. Transfer vs executed-trade distinction

They are different classes of evidence and never share a type, a feature or a
studied category:

* A **transfer** says coins moved between addresses. It does not say anyone sold.
* An **executed trade** says a trade happened at a price, with a buyer and a seller.
  Only these may carry `side`, and only when the provider states it.
* A **liquidation** is a forced closure, its own class.
* **Funding** and **open interest** are readings of a derivatives market, not actions.

How a transfer is filed (`classify_transfer`), using only the provider's own
entity types:

| Condition | Type |
| --- | --- |
| both ends typed exchange | `EXCHANGE_TO_EXCHANGE` |
| destination typed exchange | `EXCHANGE_INFLOW` |
| source typed exchange | `EXCHANGE_OUTFLOW` |
| either end typed treasury | `TREASURY_TRANSFER` |
| both ends unknown | `UNKNOWN_TRANSFER` |
| senders of mixed types | `LARGE_TRANSFER`, with the reason noted |
| anything else | `LARGE_TRANSFER` |

`EXCHANGE_INFLOW` means "moved to an address the provider calls an exchange". It is
**not** "sold": a wallet-to-exchange transfer is never classified as a sale unless
a source explicitly reports an executed sale, and then it is a different event
from a different class. A name with no provider type (for example the bare label
"binance") is kept as text but does not make anything an exchange.

The feature pipeline enforces the split: executed-trade events never enter a
transfer feature and the reverse, and mints and burns are excluded from "whale
transfer" features because they are issuer actions. Tests pin all three.

## 6. Entity attribution limitations

An attribution is a **claim by one provider at one time**. What is stored: the
provider, the raw label and raw type exactly as sent (cleaned of control
characters, nothing more), when we recorded it, and a confidence figure **only if
the provider supplied one**. No confidence is ever computed, normalised or
invented, and two providers' labels are never assumed equally reliable.

The type vocabulary is small and closed: `EXCHANGE`, `MARKET_MAKER`, `FUND`,
`CUSTODIAN`, `TREASURY`, `INSTITUTION`, `WHALE`, `OTHER`, `UNKNOWN`. A provider's
type string is mapped only when it equals a known token (after lower-casing and
turning spaces and hyphens into underscores). Substring matching is refused:
`exchange_wallet_of_someone` is `OTHER`, not an exchange. `OTHER` means the
provider labelled it and we do not map that label; `UNKNOWN` means it was not
attributed. Neither is a guess.

Attribution history is append-only, so "did this label change later?" is answered
from records instead of being overwritten. Limits worth remembering:

* labels are the provider's inference. They are sometimes wrong and they change;
* a label applied *today* to an old transfer is look-ahead. Point-in-time label
  history is required for a fair test, and few providers offer it;
* the same address can be a hot wallet, a custody wallet or a customer deposit
  address, and the label cannot always tell them apart;
* an exchange flow feature is only as good as the exchange labels behind it.

## 7. Event deduplication

Different providers can report the same on-chain transfer. Each provider record
is kept exactly as it arrived. A **canonical identity** is a separate, additive
claim that several records describe one transfer, made only when a deterministic
rule proves it and nothing contradicts it.

| Rule | Used when | Requires |
| --- | --- | --- |
| `same_transaction_hash` (strong) | both records have a hash | same asset, same hash (case and `0x` insensitive), equal amount, same destination where both give one, and no chain conflict |
| `same_addresses_amount_time` (weaker, labelled so) | at least one record has no hash | same asset, same source and destination address, amounts within 0.1%, event times within 2 minutes |

Not merged, and recorded as **ambiguous** instead: two records sharing a hash with
different amounts (a multi-output transaction: related, not duplicate); a hash
match where an amount is missing; several records from the *same* provider
matching each other; a chain of matches that is not mutually consistent.
Executed trades, liquidations and funding readings have no deterministic
cross-provider identity and are never merged across providers.

The exchange label is deliberately not a matching key: providers name the same
exchange differently, so a mismatch is not evidence of two transfers. Within one
provider, the same provider id is an exact duplicate and is stored once.
Results are deterministic and independent of input order, and re-running the check
adds nothing.

## 8. Look-ahead prevention

Every mechanism, each with a test that pins it:

* **Events** are visible only from their receipt time, enforced in SQL for stored
  events and identically for in-memory ones. Estimates need explicit permission.
  Events with no receipt time are never visible.
* **Feature windows** are `(T - window, T]` over event time, and the events in them
  must also have been received by `T`. Consequence, by design: a report that
  reaches us after its event has left the window is never counted, which is what
  would have happened live. The latency analysis measures how often that is.
* **Missing stays missing.** A window with nothing in it is a true zero only if the
  feed was *declared* complete over the window (coverage is declared, never
  inferred, and withheld automatically when an import lost records). Otherwise the
  feature is missing, with a stated reason. A sum is also missing if any event in
  the window has no USD amount, since it would be understated. A declared window
  must also be *usable* by the run reading it: one that rests on estimated receipt
  times counts only when the run allows estimates; one declared on a file imported
  today counts only for decisions made from that moment on (before it, nothing had
  been received); one whose events carry no receipt time never counts; one
  declared for some assets only (an export filtered to BTC) says nothing about the
  others and never covers a market-wide feature such as stablecoin issuance.
  Otherwise a default run would read an unusable window as a confident zero.
* **Normalisation** (`exchange_inflow_zscore_30d` and the like) uses only
  observations strictly before `T`, within the lookback. The value being
  normalised is compared with that history, not included in it, and each
  historical value is the one that was computable *then* (a late-arriving event
  does not rewrite the past). Too little history, or no variation, gives missing
  rather than a number.
* **Prices** at a time are the last *completed and observable* bar. A bar still
  forming is invisible. Forward returns start after the decision.
* **Slower feeds** are tested with `extra_delay_ms`, which shifts the visibility
  cut-off earlier: "would this still hold if we had been slower?"
* **Splits** are chronological, with the training samples whose outcome window
  reaches into the test period *purged*. Nothing is shuffled; standardisation is
  fitted on training rows only.
* **Derived triggers** ("extreme" readings) are judged against a threshold made
  from strictly earlier readings.
* A run whose feed **reveals the future but reports it late** is shown to gain
  nothing, while the same feed with falsified early receipt times *would* be
  exploited: the receipt-time guard is what makes the difference, and a test shows
  both.

## 9. Latency

`research.latency.analyze_latency` measures, per event, how much price had moved
by the time the event was actionable:

`price_at_event_time`, `price_at_provider_publish_time`,
`price_at_system_received_time`; `return_event_to_publish`,
`return_event_to_receive`; and `return_receive_to_5m`, `..._1h`, `..._4h`,
`..._24h`.

Only the receipt time is a moment we could have acted, so only prices from
receipt onward count as outcomes. Estimated receipts are used only when allowed.
Events with no receipt time are listed as skipped, not guessed. The ablation adds
a **latency stress**: the whole experiment is rerun with the feed delayed by extra
amounts (15 minutes by default) and the report says whether any gain survives.

Resolution limits what can be seen. With hourly bars a 5-minute return has no
meaning (the next observable price is an hour away) and is reported as missing,
not zero. Real latency work needs minute-level bars.

## 10. Price-already-moved analysis

The same tool answers the question that matters most for real-time alerts: *was the
signal already in the price by the time the bot saw it?* A large move between
`event_time` and `system_received_time` means it was. Read the three numbers
together: the move before the event became actionable, the move after, and how they
compare with the unconditional distribution (the event study prints that beside
every result).

Limits: the price at a time is the last completed bar, up to one bar old; two
moments inside one bar cannot be separated; and the price lookup is per market
symbol, so for a coin the analysis uses that coin's Binance.US bars, not the
venue where an executed trade happened.

## 11. Research hypotheses

Each feature family has a testable claim, a mechanism that can be stated **without
reference to any backtest**, the features it concerns, the experiments that test
it, and a caveat. They are hypotheses, not assumptions, and their status stays
`UNTESTED` in code; results live in the experiment registry as append-only runs.

| | Hypothesis | Experiments |
| --- | --- | --- |
| A | Unusually large exchange inflows, after controlling for recent price, volume and volatility, may predict weaker forward returns. | 1, 3 |
| B | Large exchange outflows may sometimes indicate reduced immediately available sell-side supply, but the effect may be weak or regime-dependent. | 1, 3 |
| C | Executed whale net buying may contain more directional information than wallet transfers because it reflects actual market execution. | 2, 3 |
| D | Extreme liquidation events may create short-horizon dislocations or mean reversion. | 2, 3 |
| E | Funding and open-interest combinations may identify crowded positioning, but direction cannot be inferred from either variable independently. | 2, 3 |

Every hypothesis carries the same controls: recent return, volatility, trend, volume
and a range (spread proxy) feature. The question is never "did price move after a
whale event?" but "did the external variable improve a forecast after accounting
for what price and volume already show?"

**The evidence ladder.** Whale information has different strengths. Levels
describe how much a piece of information *is*, not how much it *predicts*:

| Level | Meaning |
| --- | --- |
| 1 | a raw large transfer |
| 2 | an attributed transfer (the provider names or types at least one end) |
| 3 | aggregate repeated flow: several similar transfers in one direction |
| 4 | confirmed executed market activity (a trade or liquidation, not a movement) |
| 5 | an observed market response (measured over enough independent events) |

The levels are never combined into a "whale score" and no confidence percentage is
attached; there is nothing to derive one from.

## 12. Ablation testing

`research.ablation.run_ablation` asks whether extra information improves a plain
forecast. Each experiment adds columns to the same fixed model:

| Experiment | Inputs |
| --- | --- |
| 0 | market data only (return, volatility, trend, volume, range) |
| 1 | + transfer features (including stablecoin issuance) |
| 2 | + executed-trade, liquidation and derivatives features |
| 3 | + both families |
| 4, 5 | placeholders: entity attribution; aggregate on-chain regime. Listed, never run |

How it stays honest:

* **One sample for all experiments.** Every experiment is scored on exactly the rows
  where *every* feature any experiment needs is present. Otherwise one could look
  better merely by being tested on easier rows. Missing external data shrinks the
  sample for all of them; below a stated minimum the run reports
  `INSUFFICIENT_DATA` and the counts of each kind of missing reading, instead of
  numbers.
* **Ratios stay out of the model.** A buy/sell ratio or liquidation imbalance is
  undefined in a quiet hour; requiring it would keep only busy hours and bias the
  sample. The model uses the components (buys, sells, long and short liquidations)
  and the exclusion is recorded in the result.
* **Chronological, purged folds**: walk-forward (expanding window, consecutive test
  blocks) or holdout (train, validation for looking at, an untouched test).
* **The benchmark** is the prevailing mean, and the baseline is the same ridge model
  on market features only, so a gain is a gain from the *added* columns.
* **Overlap is priced in.** Forecast horizons longer than the sampling step make
  neighbouring rows share outcomes. Uncertainty comes from a moving-block bootstrap
  with blocks at least one horizon long, and the effective sample size is stated.
* **A family of comparisons is corrected** (Holm), and the number of earlier runs on
  the same question further discounts the result.
* **A provider health gate** can be supplied: an unhealthy provider's features turn
  missing, which pauses the experiments that depend on them; Experiment 0 still runs.

Each comparison ends with the six-point checklist from `EXPERIMENTS.md`: beats the
baseline out of sample; margin exceeds cost drag at a low and a high assumption;
not concentrated in one or two periods; survives a slower feed; survives
discounting for configurations tried; mechanism statable without the backtest.
It **describes**. Even if every item passes, promotion is a human decision, and
nothing here promotes anything. The cost line is a crude stress test (it ignores
turnover and overlap) and the cost figures are placeholders, labelled as
assumptions.

`research.registry_bridge.run_and_record` writes a run into the existing
registry with the dataset fingerprint (hash of exactly the events and bars used),
provider versions, feature list per experiment, dates, eligible symbols, empty
holdings/cash/contributions (this is predictive research; no orders or balances are
simulated, and the record says so), strategy and model versions, cost assumptions,
latency assumptions, outputs, and notes. A run that raised, and a run that could not
answer for lack of data, are both kept as `FAILED` with the reason, and both count
as a run on the question. Nothing is overwritten. Results are byte-for-byte
reproducible: all randomness is seeded and the result holds no timestamp.

What the synthetic tests establish: a planted effect that market data cannot see is
detected; no effect shows none; events received after the decision add nothing; a
feed that reveals the future is useless unless its receipt times are falsified; a
slower feed removes a gain that depended on speed. They establish nothing about real
data.

## 13. Event studies

`research.event_study` describes what followed events of a kind, next to what
followed *anywhere*:

* `run_event_study` studies raw provider events selected by an `EventFilter`
  (type, asset, provider, exchange, minimum USD). Entry is the **receipt** time and
  entry price the last observable price.
* `run_trigger_study` studies derived categories, where a "trigger" is an extreme
  reading of a feature, judged against the feature's own strictly earlier history
  (`research.triggers.exceedance_triggers`). This is how "extreme whale buy
  imbalance", "large liquidation spike" or "unusual open-interest increase" are
  expressed: as a feature beyond a chosen quantile of its trailing month.

For each category and each horizon (5 minutes, 1 hour, 4 hours, 24 hours, 7 days,
configurable, and only where the bars can resolve them) it reports the raw count,
the count of **independent** events (greedy, non-overlapping, so a burst of events
is not treated as many samples), and on the independent set: median, mean, spread,
share positive, mean worst drawdown after the event, and the move before the event.
It also reports the same statistics for every bar in the data (the unconditional
baseline) and, if asked, a breakdown by trend or volatility regime computed from
prices before the entry. A category with fewer independent events than a stated
minimum is marked not interpretable.

**No causal claims.** Returns after an event describe what followed. They do not show
the event caused them; prices are moved by many things at once, events cluster in
busy markets, and overlapping windows make neighbours look more independent than
they are. That disclaimer travels with every result.

## 14. Why whale data is not inherently directional

A large transfer may be an internal exchange transfer, custody movement,
collateral movement, an OTC settlement, treasury activity, a bridge movement, an
actual intent to buy or sell, or something not yet attributable. Transfer size
alone does not say which. Direction cannot be read from it, and the same coins can
appear on both sides of a story ("moved to an exchange" is also "moved off a cold
wallet"). Attribution is a provider's inference (section 6); timing is often late
(section 9); and by the time an alert is public, professional participants have
seen it too. So the zone records what was reported, keeps transfers apart from
executed trades, and lets a test decide whether any of it helps. A raw transfer is
level 1 on the ladder for exactly this reason.

## 15. Why stablecoin mint/burn events are not automatic buy/sell signals

A mint means new units were issued; a burn means units were redeemed. Neither says
what happens next. A mint can be treasury inventory, a cross-chain move (a burn on
one network paired with a mint on another), or pre-positioning for customer
redemptions; a burn can be an ordinary redemption or the other half of a bridge. Nothing
says the new units will be used to buy coins, or when. The features are named for
what they measure (`stablecoin_mint_usd_24h`, `stablecoin_burn_usd_24h`, summed across
all stablecoins, market-wide), their descriptions state that issuance is not evidence
of buying, and USD and each stablecoin remain distinct assets everywhere. Whether
issuance predicts anything is an empirical question for the ablation.

## 16. Limitations of open interest and funding

* **Open interest** counts contracts outstanding. A rise means positions were
  opened, and new longs and new shorts raise it equally, so it carries no direction
  by itself. Definitions vary by venue and contract (dollar- or coin-margined).
* **Funding** is the periodic payment between long and short holders of a perpetual
  contract that keeps its price near spot. Its sign reflects which side is paying,
  not where price goes next, and its scale differs by venue and interval.
* **Both belong to derivatives venues.** This project trades spot on Binance.US
  only, so these are information about *another market*, and the venue behind a
  provider's figures is usually not Binance.US.
* **Only combinations can express crowding** (hypothesis E). A single variable
  cannot, so a study of funding or open interest alone is not a test of E.
* Readings are states, not events: features use the latest reading within a stated
  maximum age, and a reading that is too old is missing, not carried forward.

## 17. Future Arkham/Nansen/CryptoQuant/Glassnode integration

None is integrated, bought, scraped or required. Each has a specification in
`placeholders/providers.py`, and using one raises `ProviderNotIntegrated`. Roles and
costs are estimates to be verified before any decision.

| | Role | Candidate features | Update rate | Historical need | Likely cost | Attribution uncertainty | Would justify integration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Arkham** | wallet/entity attribution; exchange, custodian, fund and market-maker labels | value moving between labelled and unlabelled addresses; net flow of labelled funds vs exchanges | near real time; labels revised continuously | the label an address had *at the time* of each transfer | paid or application-based (verify) | high | attribution improves exchange-flow features (Experiment 4 vs 1) |
| **Nansen** | labelled smart-money cohorts; cohort accumulation and distribution | cohort net change in holdings; wallets adding vs reducing | hourly to daily | cohort membership as of each test date (a cohort defined by past success is chosen with hindsight) | paid subscription; API on higher tiers (verify) | high and biased | cohort accumulation predicts returns using membership frozen before the test |
| **CryptoQuant** | exchange flows and reserves; whale ratio; aggregate holder behaviour | reserve change; provider-aggregated net flow; whale ratio; stablecoin reserves | hourly and daily | vintage data: values as first published, not restated | free web tier plus paid API (verify) | medium to high | aggregates add information beyond price at 1 to 7 day horizons (Experiment 5) |
| **Glassnode** | holder cohorts; network activity; realized metrics; exchange balances; slow regime features | MVRV and realized-value ratios; supply in profit; active addresses | daily, finer on paid plans | long daily history with vintage tracking | restricted free tier plus paid plans (verify) | medium | slow regime features improve long-horizon risk estimates, not timing (Experiment 5) |

Before any of them is integrated: you approve it and any spend; its terms are read
and recorded here; an adapter implements `normalize_record` with a stated receipt
policy; coverage is declared honestly; point-in-time label or vintage data is
confirmed to exist; the zone's audit still passes (a credentialed provider needs a
deliberately different, separately audited module, see section 20); and the tests
above are extended to it.

## 18. Data-cost considerations

The project's principle is "complexity and cost must earn their place", and the
starting capital is small. Today the subsystem costs nothing: no subscription, no
paid dependency, standard library only, local storage.

* Free or permitted first: official public APIs, free historical archives, data you
  already legitimately hold.
* **Whale Alert and CoinLobster live data are not free** (section 2), so live
  ingestion stays off until you decide.
* Paid providers (section 17) are estimated by role only. Buying any of them to
  test a hypothesis is only worth it if a *free* proxy for the same hypothesis has
  already shown something.
* A free, permitted source probably exists for **executed large trades on the venue
  this project actually trades**: Binance.US publishes individual trades on a
  public endpoint (`/api/v3/aggTrades`) that the research zone is already allowed to
  use. If its trade record carries the usual buyer-is-maker flag, which says which
  side was the aggressor (to be confirmed against Binance.US's current
  documentation before building anything), a large-trade feature made from it would
  be a like-for-like test of hypothesis C on real data without any subscription.
  It is not built yet.
* Transfers need an export you are entitled to use; there is no free bulk source
  identified for them.

## 19. Provider-health behavior

Tracked per provider: last successful update, last event timestamp, data gaps (a
quiet spell longer than a stated limit), schema failures, rate-limit errors,
timeouts, fetch latency, ingest lag, duplicate events, dropped (quarantined) events,
the fraction of transfers with no attribution, and the fraction of events with no
USD amount. Snapshots are persisted after every attempt.

A provider is **unhealthy** when it has never succeeded, has been silent longer
than a stated limit, has failed several times in a row, has too high a share of
unreadable records (judged only once there are enough records), or has hit too many
rate limits. Every threshold is stated in a `HealthPolicy`.

When a provider is unhealthy: its features become missing with the reason; dependent
experiments pause (`INSUFFICIENT_DATA`) or fall back to baseline features; and
nothing else happens. A provider failure never causes a sale, a purchase, a change
of position size, a doubling, a liquidation or a skipped risk check. This code
cannot do any of those: it has no access to a portfolio or an order, and a test
takes a real portfolio, fails a provider repeatedly, and shows the portfolio
unchanged.

## 20. Security/untrusted-content rules

All external content is **data**. Provider text, labels, descriptions, articles and
posts cannot issue commands, change settings or limits, alter credentials, activate
live trading, cause a deployment, modify code, or tell anyone to ignore these
rules. This is enforced by structure rather than by hoping nobody tries:

* **The zone cannot act.** Its audit rule set forbids signing, credentials,
  writes (`POST`, `PUT`, `DELETE`), private and trading endpoints, and the wrong
  venue (Binance.com). A test walks every import in the package: standard library
  plus a short list of engine pieces (data types, storage plumbing, registry),
  never account tools, the desktop app, the backtester, strategies or the Binance.US
  client. The research engine never imports this zone.
* **No path from a feature to an order.** A static check (`guards.py`) walks the
  syntax tree and rejects trading calls and any conditional shortcut such as
  `if whale_transfer_to_exchange: sell()`, `if whale_buy: buy()`,
  `if rising_open_interest: buy()`, `if stablecoin_mint: buy()`,
  `if whale_ratio_high: sell()` or `if many_analysts_agree: buy()`. Such a
  relationship must first be validated statistically out of sample.
* **Text is inert.** It is cleaned (control, zero-width and direction-override
  characters removed, length capped), stored and compared, never evaluated, never
  used to build a path, query or command. SQL is parameterised. Labels map to a
  closed vocabulary by exact match; anything else stays inert text.
* **If provider text is ever given to a language model**, it must pass through
  `as_untrusted_block`, which labels it as third-party data and makes its angle
  brackets and quotes inert so it cannot close or forge the wrapper. Nothing in the
  repository calls a model, and no model may make a buy or sell decision.
* **No credentials, anywhere.** The zone reads no environment variables and holds
  no key; the fetcher refuses credential-looking URLs and query keys; no key
  appears in source, prompts, logs, documentation examples or committed
  configuration. If a credential is ever approved, the repository's existing
  approach applies: read from an environment variable at call time in a separate,
  audited module (see `account_tools/credentials.py`), never stored or logged,
  and never inside this zone.

## Known limitations

* **No real provider data has been run.** Fixtures are synthetic and follow the
  providers' documented field names; real payloads will have quirks nobody has seen.
* **Live ingestion is off** for both providers (section 2), and no free bulk source
  for transfers has been identified.
* **The lagged normalisations are not yet inputs to the ablation.** They exist and
  are tested, but the builder recomputes its history at every decision time, which is
  far too slow for a year of hourly decisions. Wiring them in needs the base feature's
  series computed once and reused.
* **Latency needs finer prices.** With hourly bars, sub-hour returns are missing by
  design (section 9).
* **One deliberately plain model.** The ablation uses one ridge regression with one
  pre-declared penalty. A relationship that is non-linear, or that only appears in
  interaction (hypothesis E), would need a different test.
* **Predictive test, not a portfolio backtest.** The ablation forecasts forward
  returns for one asset at a time. It does not simulate orders, holdings or cash, and
  it does not turn a forecast into a rule. Doing so is a later, separate step.
* **What counts as "the same question" is the experiment name.** Earlier runs under
  the same name discount a result (section 12). Renaming a run resets the count, so
  the name must be kept stable by whoever runs it.
* **Provider thresholds are not modelled.** A provider reports transfers above some
  minimum size, and that minimum may have changed over time. A count or a sum is only
  comparable across periods if it did not.
* **Imports read a whole file into memory.** Fine for exports of a few hundred
  megabytes; larger ones need streaming.
* **Speed.** Everything is plain Python. Measured on the development machine, a full
  ablation (four experiments, walk-forward, one latency stress) over a year of hourly
  decisions with ten external features took about six seconds, so speed is not a
  constraint at this scale. The normalisation builder is the exception (above).

## Using it

There is no command-line entry point yet; the pieces are a library. A short tour
with synthetic values and no credentials:

```python
from external_intelligence.base.clock import SystemClock
from external_intelligence.base.health import ProviderHealth
from external_intelligence.base.receipt import ReceiptPolicy
from external_intelligence.ingest import IngestOptions, import_file
from external_intelligence.storage import IntelligenceStore
from external_intelligence.whale_alert import WhaleAlertAdapter

store = IntelligenceStore.open("data/external_intelligence.sqlite")
report = import_file(
    WhaleAlertAdapter(), "my_export.jsonl", store=store,
    policy=ReceiptPolicy.assumed_delay(60, "export has no capture time; assume one minute"),
    health=ProviderHealth("whale_alert"), clock=SystemClock(),
    # Only if the export is complete for that window, and for these assets (leave assets out only
    # if it holds every asset the provider reports):
    options=IngestOptions(covers=(start_ms, end_ms), assets=("BTC", "ETH")),
)
print(report.events_inserted, report.quarantine_reasons)
```

Features come from `features.FeaturePipeline(RepositoryEventSource(store.events),
store.coverage.index()).compute("BTC", decision_time_ms)`; latency, event studies
and ablation take the events plus a `BarPriceSource` or the market bars. Results
carry statuses and reasons, never a recommendation.

## What is deliberately not here

Live trading; authenticated order placement; withdrawal access; asset sales;
automatic strategy promotion; self-modifying code; autonomous capital increases;
paid subscriptions; private wallet tracking; social-media scraping; language-model
buy or sell decisions; automatic analyst-following; promotion of any model to
production.

## Open decisions (yours)

1. Whether to pay for any live provider (section 18), and which hypothesis would
   justify it.
2. Whether to supply an export you are entitled to use, so the pipeline can run on
   real transfers.
3. Whether to build the Binance.US executed-trade feature (section 18) as the first
   real-data test.
4. Whether to fetch minute-level bars, which real latency work needs (section 9).

## Testing

Tests live in `tests/test_external_intelligence_*.py`: events, storage, Whale Alert,
ingest and provider failures, duplicates, features, research, ablation, and the
safety boundary. Run them with `python -m unittest discover -s tests`, or one file at
a time. All fixtures are synthetic and follow the providers' documented field names;
no real payload was available to check them against.
