# External-intelligence requirement review

Reviewed 6 October 2026 against the user's 24-part request. This work continues
the existing implementation; it does not replace the engine or create another
provider framework. Existing uncommitted trade-feed and storage changes were
present before this review and have been preserved.

## Repository discovery and integration

Python 3.11+; this machine uses 3.13. Runtime dependencies remain empty.
`crypto_research/binanceus` supplies public market data, `storage` supplies
SQLite plumbing, `datafeed` loads immutable datasets, `backtest` owns portfolio,
cash buckets, costs and fills, `strategies` owns fixed-rule interfaces, `config`
parses TOML/JSON, and `experiments` keeps append-only runs including failures.
`account_tools` remains isolated. `paper_trading` is a separate rehearsal process.
Tests use unittest; provider health and quarantine records supply ingestion
monitoring, and the desktop app has its own crash log.

The existing `external_intelligence` package is the integration point. It reuses
Decimal helpers, market bars, database plumbing and RunRecord. It has separate
storage and its own safety rules. No event is connected directly to a strategy
action, paper execution, account authentication or live execution.

One installation gap was corrected: setuptools package discovery now includes
`external_intelligence*`. Previously source-checkout imports worked but an
installed distribution would omit these research modules. Two tests check all
subpackages and preserve the existing packages and zero runtime dependencies.
A wheel build was not verified: setuptools is absent in this environment.

## Requirement coverage

| Request sections | Existing implementation | Practical limit |
| --- | --- | --- |
| 1, 3: providers and trust | `base/provider.py`, transport, ingestion, sanitize, guards and safety tests | No authenticated providers or untrusted-text execution |
| 2: schema and timing | `events.py`, `base/receipt.py`; observed and explicitly estimated receipt times stay separate | Estimated timing is an assumption, not a historical observation |
| 4: Whale Alert | `whale_alert/adapter.py`: local JSON/JSONL/CSV imports, transfer classifications and attribution | No live feed; real archive compatibility not established |
| 5: CoinLobster | `coinlobster/adapter.py`, `base/executed_activity.py`: executed trades, liquidations, funding and open interest | Local project-defined format, not a verified provider wire schema |
| 6: future providers | `placeholders/providers.py`: Arkham, Nansen, CryptoQuant, Glassnode role/feature/frequency/cost/hypothesis specifications | No paid integrations |
| 7, 8: features | `features/definitions.py`, pipeline, windows, normalization | Missing coverage remains missing; normalization is not yet wired into ablation |
| 9, 10: hypotheses/evidence | `research/hypotheses.py`, `research/evidence.py` | No single whale score or invented confidence |
| 11–13: incremental value/testing | baseline, ablation, chronological splits, ridge model, block statistics and registry bridge | Predictive research, not simulated portfolio performance |
| 14, 15: latency/event studies | `research/latency.py`, price, event_study, triggers | Horizons finer than price resolution are missing; overlap is disclosed |
| 16, 23: prohibited shortcuts | guards and safety tests; package has no execution interface | No automatic promotion, sales, orders or spending changes |
| 17: quality/health | `base/health.py`, coverage and quarantine | Health depends on configured thresholds and declared feed coverage |
| 18, 19: identity/attribution | dedup, attribution and append-only storage repositories | Ambiguous events are not silently merged; provider labels remain claims |
| 20–22: documentation/tests/cost | `EXTERNAL_INTELLIGENCE.md` and external-intelligence unittest modules | Real provider evidence remains insufficient to establish added value |

The event schema includes provider and provider-event identities, type, asset,
quote asset, blockchain/exchange, three distinct timestamps, amounts, addresses,
entity claims, transaction hash, market symbol, explicit executed side, metadata,
raw reference and schema version. Historical availability uses an explicit
receipt policy; event time alone cannot make an event actionable.

Feature families cover transfer counts/value, exchange inflow/outflow/net flow,
stablecoin issuance, executed buy/sell/net flow/ratios, liquidations and imbalance,
funding and open interest levels/changes. Normalizations include trailing
percentile, z-score and ratios against trailing mean/median. No future observation
enters their histories.

Experiments 0–3 compare market data alone, plus transfers, plus executed activity,
and plus both on a shared sample. Experiments 4–5 remain placeholders. Records
preserve dataset identity, provider/model/feature versions, assumptions, outputs,
failures and notes. Holdings/cash/deposits are explicitly empty because this is
forecast testing; fees are assumed cost bounds, not measured fills. It cannot
claim a profitable accumulation strategy from a prediction improvement alone.

## Provider-source verification

The [official Whale Alert documentation](https://developer.whale-alert.io/api-account/documentation)
now identifies a free historical social-alert archive and downloadable sample
transactions, alongside subscription APIs. The older statement that no free bulk
transfer source was identified needs this qualification. The archive is selected
alerts, not evidence of complete transfer coverage. Do not assert zero flow in
unreported periods or substitute archive download time for historical receipt.
No archive was downloaded, account opened or subscription purchased in this review.
Validate its schema and applicable terms before importing it.

[CoinLobster's website](https://coinlobster.com/) advertises free AI connections
subject to a daily limit and links to developer documentation.
The old blanket claim that it publishes no schema should not be relied on without
a fresh endpoint-specific review. Neither a usable response contract, numeric
rate limit nor bulk-storage permission was established here. The existing import
adapter remains the supported path; no scraping or live connection was enabled.
See its [terms](https://coinlobster.com/terms) before obtaining data.

## Verification and changes in this review

Created: this report and `tests/test_external_intelligence_packaging.py`.
Modified: `pyproject.toml` package discovery only.
Existing adapters, backtester, portfolio, strategy interfaces and safety controls
were preserved. No new paid dependencies or credentials were introduced.

The existing external-intelligence suite passed 340 tests before the packaging
change. The initial test run failed because the sandbox's default temporary folder
denied writes; rerunning with TEMP/TMP inside the workspace passed. This was an
environment failure, not an ingestion defect. The safety audit passed with 216
files scanned. Final test results are reported in the accompanying chat.

## Next research task and human decisions

Use one legitimately obtained historical export to verify raw format, attribution
and publication timestamps before any statistical claims. Predeclare a hypothesis,
receipt-delay sensitivity, asset/horizon, chronological split, cost assumptions and
success rule. Compare it against the existing price/volume baseline using the same
sample and report insufficient data when coverage cannot support the comparison.

No permission is needed to use the completed local research machinery. Live
third-party feeds, paid access, execution, strategy promotion, portfolio weights,
purchase cadence and recurring deposits remain separate user decisions. This
review grants none of them and starts no network poller.
