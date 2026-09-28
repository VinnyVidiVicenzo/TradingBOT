# Independent review

An adversarial pass over the project, done deliberately as a separate step from
building the analyses -- the same way a second analyst checks a first analyst's
work before it goes out. Scope: the 13 files in `scripts/`, which carry zero
automated test coverage (unlike `crypto_research/` and `account_tools/`, which
have 318 passing tests and were re-verified against live data repeatedly while
being built).

## What was checked

1. **Every numeric claim quoted in `CONTEXT_FOR_CLAUDE.md`** was spot-checked
   against its source CSV/report file, independently of the script that
   produced it (8 claims across 6 different analyses: crypto-vs-stocks,
   possible-futures, long-history, scenarios, fundamentals, valuation,
   trading-cost, big-forces). All 8 matched exactly. No transcription errors
   found in this sample; not every number in the document was individually
   re-verified.
2. **Line-by-line review of `series.py`**, the module shared by the mix,
   savings and possible-futures studies, for the classic errors this kind of
   code invites: look-ahead bias, silent data-gap handling, and double-counted
   or mismatched trading costs between the monthly crypto-basket rotation and
   the annual sleeve-rebalancing logic. Found one real bug (below). The
   apparent inconsistency between the two trading-cost calculations (2x
   turnover for a full monthly coin swap, 1x for an annual rebalance transfer)
   turned out to be correct on inspection: they price different events -- a
   sell-and-buy within the crypto sleeve versus moving money across the
   sleeve boundary, where only the crypto leg is assumed to cost anything.
3. **A search across all `scripts/*.py` for the same silent-missing-data
   pattern** that caused the bug below. Found nowhere else: every other script
   either draws on the `crypto_research` engine (which raises on a data gap
   rather than silently filling it) or on external series with no gaps.
4. **Full re-run of `python -m unittest discover`** (318 tests) and
   `python -m crypto_research safety-check` after the fix, both still clean.

## Bug found and fixed: a real 912-day data gap silently read as "flat"

`series.py`'s monthly crypto basket can pick any of 198 Binance.US USDT pairs.
Checking all 198 for internal history gaps (not just the three headline coins,
which were already known to be clean) found three:

| Symbol | Gap | Cause |
|---|---|---|
| FLOKIUSDT | 2 days | trivial, immaterial |
| TRXUSDT | 1,093 days | **never selected at any point in 6 years** (confirmed) -- no effect |
| XRPUSDT | 912 days (2021-01-13 to 2023-07-14) | Binance.US suspended XRP trading during the SEC lawsuit -- a real historical event, not a data error |

XRP **was** held in the basket, picked on 2021-01-01, right before the gap
opened. For the rest of that month the code found no Binance.US price for a
coin it was actively holding and silently scored those days as 0% return.
XRP's real price (Yahoo) rose 61% in that window (\$0.3056 -> \$0.4923), so one
month's return was understated by roughly 20 percentage points -- not a
rounding error, and it compounds into every multi-year path built from that
month.

**Fix:** when a held symbol's Binance.US price is missing, the code now falls
back to the real published price (Yahoo Finance, cached like every other
external series in this project) for exactly that symbol and exactly those
days, rather than assuming no price movement. It never changes which coins get
picked -- the volume-ranking logic is untouched -- only the return of a coin
already being held on a day Binance.US has no bar for it.

## Effect of the fix

Re-ran the two affected scripts (`mix_vs_savings.py`, `possible_futures.py`).
`long_history.py` also imports from `series.py` but only reuses the generic
`simulate`/`pain` helpers -- its Bitcoin series is built independently from
Yahoo data and was confirmed unaffected.

- Every pure stock-fund or pure-savings row is **byte-identical** before and
  after (confirms the fix is properly scoped to the crypto sleeve only).
- Crypto-containing results moved **up** (the bug had been understating a
  recovery, so fixing it helps crypto's numbers, not hurts them). Measured
  against the true pre-fix files recovered from git: in `possible_futures.py`
  the typical result of every crypto-containing mix rose by about 1% to 16%
  (crypto only, scenario A: $828 -> $958); in `mix_vs_savings.py` only 100 of
  780 crypto-containing results changed at all (the windows overlapping the
  affected month), by between -0.1% and +20.7%, which moved the averages by
  0.2% to 14% depending on mix and holding length.
- **No qualitative conclusion changed.** "Beat savings" counts were unchanged
  or moved by at most one result out of dozens; worst-case and lose-money
  figures moved by 0-2 percentage points or stayed exactly the same.

`results/mix_vs_savings_BEFORE_fix.csv` and
`results/possible_futures_BEFORE_fix.csv` hold the pre-fix numbers for
comparison; the git history has both versions of every affected report.
(Correction made during the final report: the first of those two files had
been saved after the re-run and was therefore an identical copy of the
post-fix results, not a pre-fix record. It has been replaced with the true
pre-fix data recovered from git commit 210c67f.)

## What this review did not cover

- Not every number in every document was independently re-derived -- only a
  representative sample, plus the one module a manual read flagged as risky.
- The `crypto_research` engine and `account_tools` were not re-audited beyond
  confirming their existing 318 tests still pass; they were already verified
  against live data multiple times during construction and are the
  best-covered part of the project.
- This is one reviewer's pass, done after the fact, not a second independent
  build from scratch. It reduces the chance of an undiscovered error; it does
  not guarantee none remain.

## Follow-up findings made while writing the final report

A second pass, this time checking the *method* behind the numbers rather than
only their transcription. It found the following, all corrected:

1. **"Typical" was the upper of the two middle values, not the median.** Four
   scripts (`risk_profile.py`, `vs_stock_fund.py`, `mix_vs_savings.py`,
   `long_history.py`) took `sorted_values[len // 2]`, which for an even number of
   start months picks the higher of the two middle results. Typical results were
   overstated by $1 to $15 (largest: 3-year crypto, $341 vs a true median of $326).
   No conclusion changed. The scripts now use the true median and were re-run.
   The 8-claim spot check above did not catch this because it compared the
   document against each script's printed output, and both used the same
   definition: it checked the copy, not the method. (`possible_futures.py` has
   the same pattern but over 5,000 paths, where the difference is negligible, so
   it was left alone.)
2. **`results/mix_vs_savings_BEFORE_fix.csv` was not a pre-fix record** (see
   above). Replaced with the real pre-fix data from git.
3. **The review's own summary overstated the size of the XRP fix** ("every
   crypto-containing row moved up 1-16%"). The precise figures are in the
   "Effect of the fix" section above.
4. **Wrong table in `CONTEXT_FOR_CLAUDE.md`.** Its "small slice of crypto" table
   still showed pre-fix numbers ($325 / $382 / $463 for the 90/10, 80/20 and
   crypto-only rows, 3-year hold; the corrected values are $343 / $386 / $465).
5. **Two statements were not like-for-like or were mixed up**: the optimal
   allocation note compared a 3-year resampled median ($561) with a 6-year real-path
   result ($241), and the possible-futures note quoted "93-100% of 5-year futures"
   where 93% was actually a 3-year sensitivity result (5-year: 98-100%).
6. **Reproducibility gaps.** The driver scripts for the start-date sweep, blind
   selection and dip-buying tests (results files `start_date_sweep_*.csv`,
   `honest_selection_12m.csv`, `dip_buying_12m.csv`) and the BNB usage
   calculation were run ad hoc and never saved in the repository. Their saved
   result files were re-summarised and the BNB figures were recomputed from the
   saved BscScan files (they matched), but the three tests cannot be re-run from
   the repo alone.
7. **Reproduction check passed.** The four studies above were re-run from scratch
   and every data file (`risk_profile.csv`, `crypto_vs_stocks.csv`,
   `mix_vs_savings.csv`, `long_history.csv`) came out identical in content to the
   saved version (only Windows line endings differ, which git normalises).
