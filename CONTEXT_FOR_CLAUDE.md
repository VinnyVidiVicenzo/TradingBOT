# Context: crypto research project findings (September 2026)

**To the person using this:** copy this whole document and paste it as your first
message in a new Claude conversation. Then ask your question underneath it.

**To Claude, reading this:** this is a handoff from an earlier Claude Code session.
It summarises a research project and its findings so you can continue helpfully.
Everything below was measured on real data during that session unless marked
otherwise.

---

## 1. About the person you are helping

- They **do not understand technical terms or jargon.** Explain everything in plain,
  everyday language with short sentences. Avoid words like "backtest", "drawdown",
  "out-of-sample", "volatility", "API endpoint" unless you immediately explain them
  simply. Analogies help. Tables with plain column names help.
- They have roughly **$200** of starting capital in mind. An older snapshot of about
  $206 was mostly Bitcoin and BNB. That is historical context only, not a current
  balance.
- They are **not committed** to any particular coins, any weighting, any buying
  schedule, or any recurring deposits.
- **Do not give personalised financial advice.** You are not a licensed advisor.
  Present what the data showed; do not tell them what to do with their money.
- **Never ask for their API key or secret, and never accept one if offered.** Earlier
  in the project they pasted one half of a key into the chat by mistake. It was
  deleted on the exchange immediately afterwards. If a key is ever needed, they run a
  local tool themselves that asks for it at a hidden prompt.

## 2. What the project is

A **research tool** for testing ways of buying crypto on **Binance.US**, using real
historical prices. Think of it as a rehearsal machine: it replays the past and shows
what would have happened. **It cannot buy, sell or move money.** That is enforced in
the code and checked by automatic tests, not just promised.

Hard rules the person set at the start, still in force:

- Binance.US only (never Binance.com, which is a different exchange).
- Spot purchases only: no leverage, borrowing, margin or futures.
- No "doubling up after losses" style betting.
- No automatic selling.
- Anything more complicated than a simple approach has to *prove* it does better on
  data it was not tuned on before it is taken seriously.

## 3. Where everything lives

- Project folder: `D:\Desktop\TraderBOT` (a git repository with full history).
- Written in Python, no outside libraries. 318 automatic tests, all passing.
- Detailed technical docs: `docs/trading-bot/` (ARCHITECTURE, DATA_MODEL,
  BACKTESTING, SAFETY, EXPERIMENTS).
- Analysis scripts: `scripts/risk_profile.py`, `scripts/event_study.py`,
  `scripts/vs_stock_fund.py`, `scripts/mix_vs_savings.py`, `scripts/possible_futures.py`,
  `scripts/long_history.py`, `scripts/fundamentals.py`,
  `scripts/big_forces.py`, `scripts/trading_cost.py`,
  `scripts/leverage_and_flows.py`, `scripts/macro_and_maturity.py`,
  `scripts/optimal_allocation.py`, `scripts/rolling_correlation.py`,
  `scripts/withdrawal_risk.py`,
  `scripts/valuation.py`, `scripts/scenarios.py`, `scripts/crowd_mood.py`.
- Every raw result, every row: `results/*.csv`.
- A separate, read-only account reader in `account_tools/` can look up the person's
  real trading fees and balances. It has no ability to trade. It was built but never
  successfully run against the real account; it turned out not to matter (see fees
  below).

## 4. Important facts about the data

- **Binance.US dollar pairs (BTCUSD, ETHUSD, SOLUSD) are missing 586 days of history,
  14 July 2023 to 19 February 2025** (last price on 14 July 2023, next one on
  19 February 2025), when Binance.US wound down and then restored US-dollar trading
  (most dollar pairs stopped on 26-27 June 2023 and the major ones after 14 July 2023;
  dollar services returned on 19 February 2025; checked against Binance.US notices and
  its live price feed on 27 September 2026).
  So all long tests used the **USDT** versions (USDT is a coin designed to track the
  dollar). USDT ranged from $0.9715 to $1.0176 over the period that could be
  checked, so treating it as a dollar is off by up to about 3%.
- Price history used: October 2020 to 26 September 2026, daily.
- All 202 USDT coins Binance.US currently lists were downloaded (about 246,000 days of
  prices). **Coins that were delisted before today are not in the data at all**, and
  those are mostly the failures. So every result below is somewhat *more optimistic*
  than reality.
- Spot check: the tool's stored Bitcoin price for 15 Sept 2026 ($75,657, down 3.2%)
  matched CoinDesk's reported figure (about $75,600, "nearly 4%").

## 5. How the testing was kept honest

These matter for interpreting anything:

1. **Coins were picked "blind".** At each start date the test picked the three
   most-traded coins at that moment, using only information available then. Early on
   I had hand-picked Bitcoin, Ethereum and Solana, which is cheating because Solana
   rose 124x in one year and nobody could have known. Blind picking fixed that.
2. **Many start dates, not one.** Every plan was tested starting in every month of the
   six years, with a fixed holding period, so luck of timing shows up instead of
   hiding.
3. **Settings decided before looking at results**, and all of them reported.
4. **Trading fees, price spread and slippage were all included** for crypto.
5. **"Typical" means the median** (half the start months did better, half worse). Some
   older printed tables used the upper of the two middle values when the count was
   even, overstating typical results by up to about $15; corrected in the final-report
   pass.
6. **Reproducibility.** The studies behind most tables (risk profile, crypto vs fund,
   mix vs savings, long history) were re-run from scratch and reproduced their saved
   data files exactly (identical content; only Windows line endings differ). The small driver scripts for Tests 1-3 (start-date sweep,
   blind selection, dip buying) were never saved in the repository, only their result
   files, so those three cannot be re-run from the repo; their headline counts were
   re-derived from the saved result files.

## 6. The findings

### Test 1: spreading your buying out over weeks
Buy $200 all at once versus spread over 12 weekly buys.

- Spreading won **27 of 60** start months (1-year holds) and **22 of 48** (2-year holds).
  About 45%. **A coin flip; no benefit.**
- An early result looked great for spreading (+$5,205 vs +$3,768 over 4 years) but it
  **lost** on the 2 years held back for checking (−$14 vs +$11). It was luck.

### Test 2: picking the right coins
Blind-picked coins versus my hindsight picks, $200 held 1 year, 60 start months:

| | Hindsight picks | Blind picks |
|---|---|---|
| Typical result | $220 | **$220** |
| Average result | $774 | **$341** |
| Best start | $9,205 | **$2,867** |
| Doubled or better | 21 of 60 | **12 of 60** |
| Lost money | 25 of 60 | **27 of 60** |

The typical outcome didn't change; the exciting numbers were all hindsight.

### Test 3: buying more after prices fall
Same pool and dates, but spending more when a coin is further below its recent high.
Six versions tested (mild/medium/strong, 1-month or 3-month lookback).

- Every version beat steady buying **less than half the time** (19 to 26 of 60).
- The stronger the "buy the dip" effect, the worse it did. **Slightly harmful.**
- With the effect set to zero it reproduces steady buying exactly, which is tested,
  so the difference is real and not a bug.

### Test 4: how bad does it get (risk)
$200 all at once, blind picks:

| Held | Typical end | Lost money | Lost half or more from its own high point at some point |
|---|---|---|---|
| 1 year | $220 | 27/60 | 26/60 |
| 2 years | $252 | 20/48 | 32/48 |
| 3 years | $326 | 3/34 | 27/34 |
| 5 years | $194 | 5/10 | **10/10** |

("Typical" = the median. An earlier version of this table used the upper of the two
middle values when the count was even, which overstated the 2-, 3- and 5-year rows by
$2, $15 and $8.)

- Worst moment: **$200 fell to about $38 (−81%).**
- Typically spent about a quarter to nearly half of all days below what was put in
  (median 35%, 25%, 31% and 44% by holding length).
- "Lost half or more from its own high point" is a weaker measure than "fell to half
  of the $200 put in", which happened in only 14/60, 15/48, 15/34 and 7/10 of the holds.
- Longest unbroken stretch below the starting money: median 72, 89, 164 and 662 days
  by holding length; worst case up to **1,273 days (3½ years).**
- **Caution:** the holding-period rows are not a fair comparison with each other.
  5-year tests can only start in 2020–21 (near a price peak); 3-year tests include
  many starts in the cheap 2022 crash. Do not read this as "3 years is safest".
- Panic-selling after a 50% fall: cost $60 (1-year) and $230 (5-year), but saved $45
  (2-year) and $33 (3-year). Neither reliably good nor bad; it depends on when the
  money is needed. (The model never buys back in.)

### Test 5: big news events, including the CLARITY Act
**Status of the CLARITY Act (verified from news sources):** passed the House 17 July
2025 (294–134). **Failed in the Senate on 15 September 2026**, 49–50 on the procedural
vote, 11 short of the 60 needed. Betting odds of it becoming law in 2026 fell from
82% (February) to about 5–7%. It is **not law.** The separate stablecoin law, the
**GENIUS Act, did pass** (signed 18 July 2025).

Price moves of a Bitcoin/Ethereum/Solana basket:

| CLARITY event | On the day | Next week | Next month |
|---|---|---|---|
| Advanced by Senate committee (14 May 2026) | +1.5% | −5.4% | **−24.0%** |
| **Failed Senate vote (15 Sep 2026)** | **−4.4%** | **+16.9%** | **+16.3%** |

All four CLARITY milestones measured (introduced, passed the House, advanced by the
Senate committee, failed) were followed by next-month moves of -5.0%, +11.1%, -24.0%
and +16.3%: no consistent pattern, and each is a single event.

Across 15 major events (China bans, Terra and FTX collapses, ETF approvals, halving,
election, bills). **Every date below was independently verified by web search** during
a later reference-check pass (`results/reference_verification.md`); two dates
originally entered from memory turned out to be wrong by 2 days each, which changed
these numbers meaningfully -- reported here corrected, not as first computed:

- **Bad news:** fell on the day 6 of 6 times, average −7.3%. Recovery is weaker than
  first thought: at 1 week, 4 of 6 were STILL down (average −5.1%); at 1 month, 3 of 6
  were still down (average +2.1%, barely net positive). The original run said "most had
  recovered within a month" -- that was partly an artifact of one wrong date and is now
  closer to "roughly half had recovered."
- **Good news:** barely moved prices (average +1.1% on the day).
- **411 of 2,186 ordinary days (about 1 in 5) moved more than the day the CLARITY Act
  failed.** News moves are small compared with normal day-to-day swings. (This specific
  figure was unaffected by the date corrections -- the CLARITY Act's own two events were
  already independently verified from the start.)
- The single biggest event-day move in the dataset is FTX's collapse (−14.3%), not the
  China mining crackdown as first reported -- the code had hard-coded that label back
  when it really was the biggest, and kept citing it after the date correction changed
  which event actually was biggest. Also fixed.
- This is hindsight and only 15 events, so no single event proves anything.

### Test 6: crypto versus a plain stock-market index fund (SPY, S&P 500)
$200 in each on the same day, every start month. Fund prices include dividends.

| Held | Crypto typical | Fund typical | Crypto won head-to-head | Crypto lost money | Fund lost money |
|---|---|---|---|---|---|
| 1 year | $220 | $236 | 28/60 | 27/60 | 12/60 |
| 2 years | $252 | $275 | 24/48 | 20/48 | **1/48** |
| 3 years | $326 | $285 | 17/34 | 3/34 | **0/34** |
| 5 years | $194 | $374 | 2/10 | 5/10 | **0/10** |

| | Crypto | Fund |
|---|---|---|
| Worst drop | −81% to −88% | −24% |
| Days spent below starting money (median) | 25–44% | 7–12% |
| Longest stretch below starting money | up to 3½ years | up to 16 months |
| Reward per unit of worst drop | −0.04 to 0.98 | **1.19 to 3.55** |

**Similar money at 1-3 years, crypto ahead in about half the head-to-heads (2 of 10 at
5 years), but far more pain.** Caveats: 2020–26 was an exceptionally strong period for
stocks (the fund rose about 2.5x with dividends), so this window may flatter the fund
compared with a typical period. Identical start dates make the comparison fair within
this window; they do not make the window itself representative. The 5-year rows have
only 10 tests.

### Test 7: a small slice of crypto inside a stock fund
$200, rebalanced back to the target mix once a year, every start month. Crypto here
follows "the three biggest coins" re-picked monthly (switching costs included).

| Held 3 years | Typical end | Worst drop | Beat a savings account |
|---|---|---|---|
| Savings account only | $227 | 0% | — |
| Stock fund only | $281 | −24% | 36/36 |
| Fund 95% + crypto 5% | $306 | −29% | 36/36 |
| Fund 90% + crypto 10% | $343 | −35% | 36/36 |
| Fund 80% + crypto 20% | $386 | −43% | 36/36 |
| Crypto only | $465 | −79% | 35/36 |

- Each extra 10% of crypto added roughly 10 points to the *worst-case* drop (the single
  worst start month). The typical start month's worst drop rose more gently: 22% (fund
  only), 25% (10% crypto), 30% (20% crypto).
- These figures were corrected after the XRP fix and the median fix; the earlier table
  showed $325 / $382 / $463 for the 90/10, 80/20 and crypto-only rows.
- Over 1 year the slice didn't help the typical result; over 2+ years it raised it in
  this (very good) period.
- **Holding rule matters for crypto:** following the biggest coins monthly lost money in
  0 of 12 five-year windows; buying the 2020 top three once and holding lost in 5 of 10.

### Test 8: against a savings account (3-month Treasury bill rate)
Rates were about 0% in 2020–21, about 5% in 2023–24, about 4% in 2025–26.
- Over 1 year, the stock fund beat savings in 46 of 60 start months; **crypto alone only
  29 of 60 (a coin flip).**
- Over 3+ years, the stock fund and every mix beat savings in every start month.

### Test 9: 5,000 possible futures (reshuffled 30-day chunks of 2020–26)
Two scenarios: **A** = like 2020–26; **B** = same ups and downs but only half the extra
growth above savings (because 2020–26 was unusually good). Numbers below were corrected
during an independent review -- see the review note near the end of this document; the
correction moved every crypto figure up by roughly 1-16% and changed no conclusion.

| Held 5 years | Typical (A) | Bad 1-in-20 (A) | Typical (B) | Bad 1-in-20 (B) | Chance of losing money (B) |
|---|---|---|---|---|---|
| Savings only | $234 | $229 | $234 | $229 | 0% |
| Stock fund only | $425 | $246 | $304 | $176 | 11% |
| Fund 90% + crypto 10% | $520 | $258 | $327 | $169 | 11% |
| Crypto only | $958 | **$63** | $237 | **$16** | 46% |

- Crypto alone fell by half at some point in 98% (scenario A) to 100% (B) of 5-year
  futures, 91-96% of 3-year futures and 49-61% of 1-year futures.
- A 5–10% crypto slice barely changed the odds of losing money; it mostly raised the good
  outcomes.
- **Most important caveat:** the stock fund almost never "falls by half" here only
  because no big stock crash happened in 2020–26. Stocks halved in 2000–02 and 2008–09.
  Real stock risk is higher than these futures show.

### Test 10: longer history (stocks since 2000, Bitcoin since 2014)
Sources: Yahoo Finance, checked first -- Yahoo's Bitcoin matched our Binance.US
prices to 0.06% on a typical day; the stock fund showed the 2008 crash at -55%.

**Stock fund since 2000 (includes 2000-02 and 2008-09 crashes):**

| Held | Typical | Worst start | Lost money | Beat savings | Worst drop | Longest in red |
|---|---|---|---|---|---|---|
| 1 year | $226 | $113 | 68/308 | 236/308 | -51% | 1 yr |
| 5 years | $341 | $142 | 28/260 | 212/260 | -55% | 5 yrs |
| 10 years | $513 | $168 | 10/200 | 187/200 | -55% | 6.1 yrs |

So the stock fund's real risk is much bigger than 2020-26 showed: it can halve,
and a 10-year hold starting around 2000 still lost money.

**Bitcoin since 2014 (Bitcoin alone, not a basket):** 3- and 5-year holds never lost
money for any mix; Bitcoin alone typically turned $200 into $880 (3 yrs) and $2,220
(5 yrs) but still fell -83% at worst. A 10% Bitcoin slice raised a fund's typical
5-year result from $389 to $540 and its worst drop from -34% to -49%.
**Big caveat:** Bitcoin is the coin that survived and won; choosing it today is
hindsight. The blind 2020-26 basket did far worse. In the "half the growth"
futures, Bitcoin alone lost money in 36% of 5-year futures and fell by half in 99%.

### Analyst step 1: what is underneath the coins (BTC, ETH, XRP, SOL, ZEC, DOGE, BNB)
Coins chosen by rule: top by market value, minus stablecoins, keeping those that
trade meaningfully on Binance.US (DOGE added for liquidity, TRX dropped, BNB kept
because the person held it before; its exchange-token conflict is flagged).
Free data (Coin Metrics, CoinGecko). Full tables in `results/fundamentals_report.txt`.
- **Ethereum:** healthiest usage (active addresses +83%, transactions +113% over 3
  years); price grew slower than use. Layer-2 activity not counted, so understated.
- **Bitcoin:** addresses -35% but transactions +41% over 3 yrs; value per address 4x --
  price ahead of direct use, partly because ETF holders never touch the network.
  Fixed 21m supply, 96% issued.
- **XRP:** usage growing roughly in step with price; Ripple (a company) holds ~37bn
  in escrow -- concentration of control.
- **Zcash:** tiny usage (~5,700 addresses/day); value per address up 57x in 3 yrs --
  price far ahead of use; ~4% new coins/yr; privacy-coin regulatory risk.
- **Dogecoin:** usage shrinking (addresses -37% in a year, transactions -86% in 3);
  ~3.3% new coins/yr forever; top 10 wallets hold ~58% (mostly exchanges).
- **Solana, BNB:** no free usage data. BNB tied to one exchange.
- Ownership (`results/ownership_concentration.md`): Bitcoin broad by crypto
  standards (~15% in ~88 addresses, mostly exchanges/ETF custodians; source figures
  partly inconsistent); Dogecoin highly concentrated; Ethereum broad once pooled
  contracts are set aside (staking contract 75% is many stakers; biggest named holders
  are exchanges at ~1-2% each).
- **BNB usage** (BscScan free CSVs): 16.4m transactions/day, +24% in a year, +362% in
  3 years; new addresses/day -14% in a year. Very low fees mean much is bot activity.
- **Developer activity** (GitHub, commits in the past year, main code project):
  Bitcoin ~3,800-4,500, Solana ~4,250, XRP ~1,300 (last 3 months up sharply),
  Ethereum ~1,160 (main client only; Ethereum has several), BNB ~1,060 (last 3 months
  down sharply), Zcash ~830 (up sharply), Dogecoin 51 (very little). Rough sizes only.
- Still missing: Solana usage history, Solana/BNB valuation (paid services only), the
  person's own fee rate. Drop-off folder with instructions: `data/user_supplied/`.

### Analyst step 2: outside forces (`results/big_forces_report.txt`)
"Move-together score" = monthly correlation, -1 to +1.
- **Stocks:** Bitcoin vs stock fund +0.35 overall, but rising: +0.12 (2014-19),
  +0.58 (2020-22), +0.44 (2023-26). In the stock fund's 12 worst months, Bitcoin fell
  11 times (e.g. March 2020 -25%, June 2022 -38%). Crypto does NOT protect you in a
  stock crash; it tends to fall with it, often harder.
- **Rates:** in the 2022-23 rate rises Bitcoin fell 32% while the stock fund rose 7%.
  Few months with big rate moves, so evidence is thin.
- **Dollar:** when the dollar strengthened 2%+, Bitcoin averaged -3.1%; when it
  weakened 2%+, +9.4%. Weak but consistent opposite relationship.
- **Other coins vs Bitcoin:** month-to-month scores only 0.20 (ZEC) to 0.74 (ETH), but
  in the 3 months Bitcoin fell 20%+, every coin fell 18-36%. Owning several coins
  spreads risk in normal times, not in crashes.

### Analyst step: the real cost of trading on Binance.US (`results/trading_cost_report.txt`)
Live order books, 3 snapshots, 27 Sep 2026; excludes the exchange's trading fee.
- For a $200 order, the price penalty was 0.00-0.04% on every pair except ZECUSD
  (0.12%). BTC, ETH and XRP stay under 0.1% even at $50,000.
- The tests assumed 0.10% (5 bps spread + 5 bps slippage) -- confirmed as reasonable
  and slightly cautious. The trading fee itself (assumed 0.4-0.6%) is the bigger cost.
- Thinnest markets: ZEC in dollars (only ~$47k on offer within 1% of the price; a
  $50k buy would cost ~5%), then DOGE and BNB (~$130-170k). BTC/ETH ~$1.3-1.6m.
- Platform risk: the person considers it lower now; agreed to cover it as a short
  factual section in the final report (Binance.US USD suspension 2023-25, Binance
  $4.3bn settlement 2023, Bybit ~$1.5bn hack Feb 2025, CLARITY Act failure) rather
  than a full study.

### Analyst step: is it expensive right now? (`results/valuation_report.txt`)
Measure: MVRV (Coin Metrics free data) = market value / what holders collectively
paid. 1.0 = holders at break-even on average; 3 = triple; below 1 = at a loss.
Not available free for SOL or BNB.
- Today (26 Sep 2026): BTC 1.58, ETH 1.15, XRP 1.02, DOGE 0.82, **ZEC 2.60**.
- Past peaks mostly read 2-4 for Bitcoin; crash bottoms 0.7-0.8.
- BTC, ETH, XRP, DOGE sit in the middle-to-low part of their history (higher on
  55-58% of past days). Zcash is at almost its highest reading ever (fewer than
  0.5% of days higher) -- consistent with its price running far ahead of use.
- Did it predict Bitcoin's next 12 months? Below 1.0: rose every time (22 months);
  1.5-2.5 (today's zone): fell 28 of 70 times, typical +62%; above 3.5: fell 2 of 3.
  Only ~4 cycles and heavily overlapping months, so a handful of real episodes --
  a "stretched or not" gauge, not a timing signal.

### Analyst step: crowd psychology (`results/crowd_mood_report.txt`, `results/etf_flows_farside.md`)
Four free public sources; two others (Google Trends, crypto-specific betting-market
odds) have no reliable free API and were left as open gaps. Congressional trading
disclosures in crypto-adjacent stocks (Coinbase, Strategy, Bitcoin ETFs) would be a
genuinely interesting "obscure" source tying back to the CLARITY Act, but that
endpoint needs a higher-tier FMP plan than is currently connected.

- **Fear & Greed Index** (alternative.me, daily since Feb 2018, 3,157 days). Today
  reads 70 ("Greed"), higher than 79% of all days on record. Tested against what
  Bitcoin's own price did next: **extreme greed (76-100) was followed by the
  BIGGEST average gains** (+13.1% next 30 days, +39.9% next 90 days), not losses --
  the opposite of the popular "sell into greed" idea. Extreme fear (0-24) preceded
  only +3.2%/+2.1%. Read this as "Bitcoin trended up strongly across this history and
  greed readings cluster in uptrends," not as a signal to chase greed.
- **Wikipedia "Bitcoin" page views** (daily since Jul 2015): last 30 days averaging
  6,656 views/day; all 10 biggest attention spikes ever were in Nov-Dec 2017, at the
  2017 mania peak. Public attention today is far below the 2017 extreme.
- **Solana usage gap partly filled**: DefiLlama total value locked in Solana apps
  is $6.68bn today, down 40% from a year ago but up 2045% from three years ago.
  This is money using the network, not the active-address/transaction count we
  wanted -- that free-data gap remains open.
- **Bitcoin ETF money flows** (Farside Investors, real investor money, not
  sentiment): -$450m and -$296m flowed OUT on the two days around the CLARITY Act's
  15 Sep 2026 failure, then money flowed back IN on each of the next seven trading
  days (17-25 Sep), from about $135m to $999m a day (outflows had also run for a week
  before the vote: -$463m over 8-11 Sep). Consistent with the price-based event-study
  finding (fell 4.4% on the day, +16.9% the following week), using actual money rather
  than price alone. One 14-day episode,
  not a proven repeatable pattern.

### Analyst step: scenarios from history (`results/scenarios_report.txt`)
For each asset, every 1- and 3-year holding period in its own price record was
measured, then labelled by how often the past was that bad or good: disaster (1
period in 20), bad (1 in 5), typical (the middle), good (1 in 5). These are
frequencies from the past, not forecasts, and a coin that failed and disappeared
cannot appear in this kind of history at all.

$200 held 1 year:
| Asset | Disaster | Bad | Typical | Good | Years that lost money |
|---|---|---|---|---|---|
| Savings account | $200 | $200 | $202 | $208 | 0% |
| Stock fund | $156 | $194 | $226 | $244 | 22% |
| Bitcoin | $82 | $158 | $345 | $566 | 28% |
| Ethereum | $54 | $115 | $229 | $396 | 45% |
| XRP | $69 | $102 | $195 | $628 | 51% |
| Solana | $35 | $97 | $239 | $1,567 | 43% |
| Zcash | $41 | $79 | $190 | $483 | 51% |
| Dogecoin | $54 | $93 | $209 | $486 | 47% |
| BNB | $110 | $157 | $272 | $517 | 34% |

Over 3 years, Bitcoin, BNB and Solana lost money in almost none of their own past
3-year periods; Zcash lost money in 60% of them, the worst of the group.

### Analyst step: leverage -- borrowed-money betting and how it amplifies crashes
(`results/leverage_and_flows_report.txt`) "Leverage" = trading with borrowed money in
futures. This project's own tool cannot do this (spot-only, no leverage, no futures --
see docs/trading-bot/SAFETY.md); this reads it as an OUTSIDE indicator only, the same
way Fear & Greed was read. Binance.com and Bybit's public data are both geo-blocked
from this environment; Deribit's public BTC perpetual-futures history (back to mid-2019,
64,215 hourly readings) was used instead -- informational only, never touching the
Binance.US research engine or its data store.

- **Funding rate** (the fee crowded leveraged traders pay each other): extreme NEGATIVE
  readings (crowded short bets) preceded the BEST average next-30-day return (+8.9%,
  fell only 33% of the time); extreme POSITIVE readings (crowded long bets) preceded
  weaker, near-average returns (+5.1%, fell 48% of the time, barely different from the
  47% baseline). **At the 90-day horizon the order flips**: crowded longs were followed
  by +20.5% on average and crowded shorts by +18.0%. So this measure disagrees with the
  earlier Fear & Greed finding (extreme GREED preceded the biggest gains) at 30 days
  and roughly agrees with it at 90 days. A signal whose direction depends on the horizon
  chosen is not dependable; it is one more reason not to trust any single sentiment
  measure.
- **Stablecoin supply growth** (new USDT+USDC being created = new money likely arriving):
  months when supply SHRANK preceded worse Bitcoin returns (-5.7% avg, fell 68% of the
  time); the fastest-growing months preceded better returns (+9.8% avg, fell 53% of the
  time). This one points the intuitive direction.
- **The biggest leverage-driven crash by far is one the person hadn't known about**:
  8-11 October 2025, $19bn in leveraged positions forcibly liquidated, 1.6 million
  accounts, roughly 9x the Feb 2025 crash and 19x the FTX collapse -- triggered by a
  Trump tariff announcement with nothing to do with crypto itself. Independently
  confirmed against this project's own Binance.US data: BTCUSDT fell from $124,798
  (Oct 6) to a low of $109,278 (Oct 11). The May 2021 China-crackdown crash ($8.6bn
  liquidated, precise figure) was record-sized at the time but has since been dwarfed.
- Pattern across the 4 biggest crashes studied: the trigger was always news, a stablecoin
  failure, or a geopolitical announcement -- never leverage itself. But every time, a
  market already carrying a lot of one-directional leveraged betting turned an ordinary
  bad-news day into a much larger move, because forced position closures force further
  price drops, which force more closures.

### Analyst step: gold, GBTC, and whether crypto has calmed down
(`results/macro_and_maturity_report.txt`)
- **"Digital gold" claim tested directly**: move-together score with gold is +0.06 --
  essentially zero. In gold's 12 worst months, Bitcoin actually rose in 10 of them. The
  "digital gold" pitch compares what the two assets represent, not a real price
  relationship in this data.
- **GBTC premium/discount** (the old way to hold Bitcoin in a normal brokerage account,
  before 2024): no exact historical premium % was available for free, so a proxy was
  built (cumulative GBTC return minus cumulative Bitcoin return) and checked for
  sensibility rather than taken as gospel. It swung by 122 percentage points before
  GBTC converted to a real ETF on 11 Jan 2024, and by only 10 points in the 2.7 years
  since -- a >10x drop, exactly what should happen once ETF arbitrage starts holding
  the price to the coin's actual value. The proxy shows clearly positive readings
  through the 2021 run-up and a deep negative reading around the FTX collapse
  (Dec 2022, -35%), matching the publicly known shape of that period.
- **Has Bitcoin's volatility actually come down?** Yes, measurably: 73% (2014-17) to
  74% (2018-20) to 64% (2021-23) to 48% (2024-26, the ETF era) annualized volatility.
  Real evidence of a maturing market. Still 2.5-3x the S&P 500's typical 15-20%.

### Analyst step: what allocation would actually have been "optimal" (and why that question is a trap)
(`results/optimal_allocation_report.txt`) Grid-searched every crypto/stock/cash weight
in 10% steps, scored by "gain per unit of worst drawdown" (same measure used earlier).
- **On the ONE real historical path**, 100% cash "won" by this ratio -- but that is an
  artifact: dividing by an almost-zero drawdown inflates the ratio even for a tiny gain.
  Ranked instead by plain growth across the 1,000 resampled 3-year futures, 100% crypto
  tops the list ($561 typical, but it lost money in 21% of them) versus about $220 for
  100% cash (0% lost) -- which ranking matters depends on how much risk is acceptable,
  not on the arithmetic itself. (An earlier version of this note compared $561, a
  3-year resampled median, with $241, cash's result over the whole ~6-year real path:
  not a like-for-like comparison.)
- **Scored instead across many resampled possible futures** (reusing the possible-futures
  Monte Carlo machinery, not just the one lucky/unlucky real path): picked a DIFFERENT
  answer (10% stocks / 90% cash) than the single-history version (100% cash). The two
  versions disagreeing is the actual point -- a weight tuned to exactly what happened is
  not the same thing as a weight that holds up broadly, which is why "backtest-optimized"
  allocations should be distrusted.
- **Does spreading out the buying matter more once most of the money is in something
  steadier** (90% stocks / 10% crypto)? No: staged beat lump-sum in only 22 of 60 months
  (37%), same conclusion as for pure crypto.

### Analyst step: has owning several coins gotten LESS protective over time?
(`results/rolling_correlation_report.txt`) big_forces.py's single whole-period
correlation numbers were tracked instead in 90-day rolling windows, split into three
eras. 5 of 6 coins (ETH, XRP, SOL, DOGE, BNB) show RISING correlation with Bitcoin --
only Zcash fell. Average correlation with Bitcoin across the other 6 coins rose from
about 0.56 (first 18 months) to 0.71 (most recent ~30 months). **Owning several coins
instead of concentrating in Bitcoin has been diversifying less, not more, as the market
has matured** -- the opposite of what "the market is maturing" might suggest.

### Illustration only: if withdrawing instead of depositing, does crash timing matter?
(`results/withdrawal_risk_report.txt`) Not this project's actual situation -- money is
going IN, not out -- but requested explicitly as background knowledge. Took real 3-year
return sequences and replayed each one forward and in reverse, withdrawing $5/month
(2.5% of a $200 stake) either way. With no withdrawals, order would make zero difference
to the ending value; every difference shown exists only because of the withdrawals.
**Order mattered by more than $20 in all 36 of 36 windows tested.** Several consecutive
real starting months (March 2021 - May 2022, aligned with the well-documented 2021-22
crash) show the forward-order account being wiped out to $0 entirely, while the
reversed order of the exact same returns preserved real value. The takeaway is not
which direction is safer in general (that flips depending on which real sequence gets
reversed) -- it is that a crash landing early in a withdrawal period is far more
damaging than the identical crash landing late, which is why this project's own
accumulation (depositing) situation is fundamentally less exposed to this risk than a
withdrawal situation would be.

### Fees
Trading fees barely matter at this size: about $1.20 on $200 over six years, and
changing the fee assumption from 0% to 1% moved results by about 1 percentage point.
Getting the person's exact fee rate is not important.

### Independent review (`results/independent_review.md`)
A deliberate adversarial pass over the analysis scripts, done after the fact.
Found and fixed one real bug: a coin (XRP) held in the rotating crypto basket
had its Binance.US price data go dark for 912 days starting mid-month (the
exchange suspended it during the SEC lawsuit, a real event, not a data error);
the code silently scored the rest of that month as 0% return instead of XRP's
real +61% move, understating one month by about 20 percentage points. Fixed by
falling back to the real published price for exactly that gap. Re-ran the two
affected studies (mix-vs-savings, possible-futures): in possible-futures the typical
result of every crypto-containing mix rose by about 1-16%; in mix-vs-savings only 100
of 780 crypto-containing results changed (the windows overlapping the affected month),
by up to +21%; no pure-stock or pure-savings number changed at all, and no conclusion
in this document flipped. Full details, plus what else was
checked and came back clean, in the review file.

### Reference verification (`results/reference_verification.md`)
A separate pass checking factual claims about the outside world (event dates,
figures) rather than our own arithmetic. Nine event dates that had been typed
from memory were checked against sources: 2 were genuinely wrong by 2 days
each (China mining crackdown, Terra/LUNA collapse start) and corrected; 1 was a
real one-day UTC/US-timezone ambiguity (the 2024 Bitcoin halving) resolved to
this project's own UTC convention; the other 6, plus two figures stated in
conversation (the Bybit hack, the Binance settlement), were already accurate.
The two date corrections changed a real conclusion in Test 5 above (bad-news
recovery is weaker than first reported) and exposed a second bug: the code had
hard-coded "the 2021 China crash" as the biggest single-day mover in the
dataset, which stopped being true once its date was corrected -- fixed to
identify the actual biggest mover from the data instead of asserting a fixed
answer. Full source-by-source record, with links, in the file.

### Final-report pass (`results/independent_review.md`, last section)
A third audit, on the *method* behind the numbers rather than their transcription.
Found and fixed: "typical" had been the upper of the two middle values in four scripts
(overstating typical results by $1-$15); the small-slice table was still on pre-fix
numbers; the "before fix" file for it was a copy of the after-fix data; several
sentences overstated the data (a non-like-for-like allocation comparison, a mixed-horizon
"93-100%", a funding-rate "contradiction" that only holds at 30 days, a wrong ETF-flow
count, "a third or more of days" underwater, a bill number no readable source
confirmed). Also disclosed: the driver scripts for Tests 1-3, the BNB usage calculation
and the GitHub commit counts were never saved. The four main studies were re-run and
reproduce their saved data.

## 7. The overall conclusion so far

| Question | Answer |
|---|---|
| Does spacing out buying help? | No |
| Does picking coins help? | Only with hindsight |
| Does buying dips help? | No, slightly worse |
| Does reacting to news help? | No |
| How bad can it get? | About −80%, underwater up to 3½ years |
| Better than a stock index fund? | Similar money, much more pain |
| A small crypto slice in a stock fund? | More upside, somewhat bigger drops, similar odds of losing |
| Better than a savings account? | Stock fund usually yes over 2+ years; crypto alone a coin flip over 1 year |

The consistent theme: the things people think they can control didn't help; the
thing that dominated results was **when you happen to start**, which nobody controls.
A "fall by half" was the normal experience over multi-year holds, not a rare event.

## 8. Ideas not yet explored (if they want to continue)

- A "watch it live with no money" tracker that records what a plan would do each day
  going forward.
- Turning the tool into one simple command they can run themselves.
- Stopping here is also a legitimate choice: the research answered its question.

## 9. Final report

Everything above was synthesised into one plain-language document:
`results/FINAL_REPORT.md`. It covers all ten tests, every analyst-style deep dive,
the three audit passes, a coin-by-coin scorecard, the platform, rules and tax facts,
the case for and against, questions only the person can answer, a limits list, and
appendices (glossary, corrections table, sources, files). Every table in it was
checked against its source file by script (317 checks, all passing on 27 Sep 2026). If asked to continue this project, read that file first -- it is
the most complete single summary, more complete than this handoff document.

One fact surfaced only while preparing that report, not stated anywhere above:
**the tax "wash sale" rule does not apply to crypto** in the sources checked (two
tax-firm articles dated March and May 2026). Selling at a loss and immediately
rebuying does not block the loss for tax purposes, because the IRS treats crypto as
property and that rule only binds stocks and securities. Proposals to extend it to
crypto exist (a CNBC headline of 28 July 2026 says Congress is renewing the push);
none had passed as of those sources, and the status of any specific bill was not
confirmed. This is a factual note, not advice -- do not tell the person to act on it.
