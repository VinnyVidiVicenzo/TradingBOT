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
  `scripts/big_forces.py`.
- Every raw result, every row: `results/*.csv`.
- A separate, read-only account reader in `account_tools/` can look up the person's
  real trading fees and balances. It has no ability to trade. It was built but never
  successfully run against the real account; it turned out not to matter (see fees
  below).

## 4. Important facts about the data

- **Binance.US dollar pairs (BTCUSD, ETHUSD, SOLUSD) are missing 586 days of history,
  14 July 2023 to 19 February 2025**, when Binance.US suspended US-dollar trading.
  So all long tests used the **USDT** versions (USDT is a coin designed to track the
  dollar). USDT ranged from $0.9715 to $1.0176 over the period that could be
  checked, so treating it as a dollar is off by up to about 3%.
- Price history used: October 2020 to 26 September 2026, daily.
- All 202 USDT coins Binance.US currently lists were downloaded (about 239,000 days of
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

| Held | Typical end | Lost money | Fell by half or more at some point |
|---|---|---|---|
| 1 year | $220 | 27/60 | 26/60 |
| 2 years | $254 | 20/48 | 32/48 |
| 3 years | $341 | 3/34 | 27/34 |
| 5 years | $202 | 5/10 | **10/10** |

- Worst moment: **$200 fell to about $38 (−81%).**
- Typically spent a third or more of days below what was put in.
- Longest unbroken stretch below the starting money: up to **1,273 days (3½ years).**
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

Across 15 major events (China bans, Terra and FTX collapses, ETF approvals, halving,
election, bills):

- **Bad news:** fell on the day 6 of 6 times, average −11.8%, but within a month most
  had recovered (down only 2 of 6, average +4.6%).
- **Good news:** barely moved prices (average +0.7% on the day).
- **411 of 2,186 ordinary days (about 1 in 5) moved more than the day the CLARITY Act
  failed.** News moves are small compared with normal day-to-day swings.
- This is hindsight and only 15 events, so no single event proves anything.

### Test 6: crypto versus a plain stock-market index fund (SPY, S&P 500)
$200 in each on the same day, every start month. Fund prices include dividends.

| Held | Crypto typical | Fund typical | Crypto lost money | Fund lost money |
|---|---|---|---|---|
| 1 year | $220 | $236 | 27/60 | 12/60 |
| 2 years | $254 | $276 | 20/48 | **1/48** |
| 3 years | $341 | $289 | 3/34 | **0/34** |
| 5 years | $202 | $377 | 5/10 | **0/10** |

| | Crypto | Fund |
|---|---|---|
| Worst drop | about −85% | −24% |
| Days spent below starting money | 25–44% | 7–12% |
| Longest stretch below starting money | up to 3½ years | up to 16 months |
| Reward per unit of worst drop | −0.04 to 0.98 | **1.19 to 3.55** |

**Similar money on average; crypto won about half the head-to-heads; but far more
pain.** Caveats: 2020–26 was an exceptionally strong period for stocks (the fund
rose about 2.5x); the 5-year rows have only 10 tests. Both sides used identical start
dates, so those caveats don't tilt the comparison.

### Test 7: a small slice of crypto inside a stock fund
$200, rebalanced back to the target mix once a year, every start month. Crypto here
follows "the three biggest coins" re-picked monthly (switching costs included).

| Held 3 years | Typical end | Worst drop | Beat a savings account |
|---|---|---|---|
| Savings account only | $228 | 0% | — |
| Stock fund only | $281 | −24% | 36/36 |
| Fund 90% + crypto 10% | $325 | −34% | 36/36 |
| Fund 80% + crypto 20% | $382 | −43% | 36/36 |
| Crypto only | $463 | −79% | 35/36 |

- Each extra 10% of crypto added roughly 10 points to the worst drop.
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
growth above savings (because 2020–26 was unusually good).

| Held 5 years | Typical (A) | Bad 1-in-20 (A) | Typical (B) | Bad 1-in-20 (B) | Chance of losing money (B) |
|---|---|---|---|---|---|
| Savings only | $234 | $229 | $234 | $229 | 0% |
| Stock fund only | $425 | $246 | $304 | $176 | 11% |
| Fund 90% + crypto 10% | $505 | $255 | $323 | $168 | 11% |
| Crypto only | $828 | **$57** | $224 | **$15** | 47% |

- Crypto alone fell by half at some point in 98–100% of 5-year futures.
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
| 5 years | $342 | $142 | 28/260 | 212/260 | -55% | 5 yrs |
| 10 years | $514 | $168 | 10/200 | 187/200 | -55% | 6.1 yrs |

So the stock fund's real risk is much bigger than 2020-26 showed: it can halve,
and a 10-year hold starting around 2000 still lost money.

**Bitcoin since 2014 (Bitcoin alone, not a basket):** 3- and 5-year holds never lost
money for any mix; Bitcoin alone typically turned $200 into $882 (3 yrs) and $2,237
(5 yrs) but still fell -83% at worst. A 10% Bitcoin slice raised a fund's typical
5-year result from $392 to $542 and its worst drop from -34% to -49%.
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
  partly inconsistent); Dogecoin highly concentrated; Ethereum page unavailable.

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

### Fees
Trading fees barely matter at this size: about $1.20 on $200 over six years, and
changing the fee assumption from 0% to 1% moved results by about 1 percentage point.
Getting the person's exact fee rate is not important.

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
