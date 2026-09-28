# Crypto accumulation research: final report

*Prepared 27 September 2026 · price data through 26 September 2026 · Binance.US · written for a reader with no trading or technical background*

> **What this is.** A study of what *would have happened* to small amounts of money ($200) put into crypto, a stock fund or a savings account, using real past prices. It is not a forecast and it is not financial advice; I am not a licensed advisor.
>
> **What it cannot do.** The tools built for this project cannot buy, sell or move money. That is enforced in the code and by automated tests, not just promised.
>
> **Where the numbers come from.** Every table of results below is taken from a file in `results/` (the list is in Appendix D), and every outside fact is sourced in Appendix C, so any figure can be traced back.

**Contents:** 1. The short version · 2. What we did, and how we tried to keep it honest · 3. The evidence · 4. Outside the price charts: the exchange, the rules, tax · 5. The case for, and the case against · 6. What this research cannot tell you · 7. Questions only you can answer · 8. What could change these conclusions · Appendices: A glossary, B corrections made along the way, C sources, D files and how to re-run

---

## 1. The short version

**The question.** Is there a smart way to build a small position in a few large, easy-to-trade coins on Binance.US, and does anything beyond plain, steady buying (spreading purchases out, buying dips, reacting to news, reading crowd mood) actually help?

**What we found**

1. **No buying trick beat simple, steady buying.** Spreading $200 over 12 weekly purchases beat buying it all at once in only 27 of 60 start months (45%). Six versions of "buy more after a fall" beat steady buying in 19 to 26 of 60. Bad news did hit prices on the day (6 of 6 events, average −7.3%), but what came afterwards was mixed, and good news barely moved them.
2. **Luck of timing decided most outcomes.** The same $200, held one year with coins picked blind, ended anywhere from $43 to $2,867 depending on the month it started. The middle result was $220, and 27 of 60 start months lost money.
3. **The ride is very rough.** In most multi-year holds the coins lost at least half of their value from a high point at some stage (26 of 60, 32 of 48, 27 of 34 and 10 of 10 holds, for 1, 2, 3 and 5 years). The lowest point took $200 down to about $38 (−81%), and the longest stretch below the money put in lasted 1,273 days (3½ years).
4. **Against a plain S&P 500 index fund (2020 to 2026): typical results in the same range at one to three years, far more pain.** Over one year crypto lost money in 27 of 60 start months and the fund in 12. The worst drop was −81% to −88% for crypto and −24% for the fund. Crypto came out ahead in 28 of 60 one-year comparisons but only 2 of 10 five-year ones. That period flattered stocks: back to 2000, the same fund fell as much as 55%.
5. **A small crypto slice changes less than you might expect.** Held three years, 10% crypto inside a stock fund lifted the typical result from $281 to $343 and the worst-case drop from −24% to −35%. The chance of losing money barely moved.
6. **Crypto has not been a shelter, and owning several coins protects less than it used to.** Bitcoin fell in 11 of the stock fund's 12 worst months. Its price has almost no relationship with gold (+0.06). The other six coins' average "move-together score" with Bitcoin rose from about 0.56 to about 0.71.
7. **Borrowed money makes crashes bigger, and mood gauges are unreliable.** About $19 billion of leveraged bets were forced closed on 10 to 11 October 2025, the largest such event on record. The mood and leverage indicators we tested contradict each other or change direction with the time horizon, so none is dependable on its own.
8. **At $200 the trading costs are tiny; the bigger practical risks are elsewhere.** A $200 buy of Bitcoin, Ethereum, XRP or Solana costs 0.00% to 0.03% in price impact (the exchange's fee comes on top). The larger risks are the exchange and the rules around it (Binance.US's dollar service was suspended for about 20 months, and the main US crypto market-structure bill failed in the Senate on 15 September 2026) and the fact that some coins' prices have run far ahead of their use (Zcash) or their use is shrinking (Dogecoin).

**How much weight each finding deserves**

| Finding | Weight | Why |
|---|---|---|
| 1. No buying trick helps | Fairly strong | Three separate tests gave the same answer. But they rest on about five independent years of overlapping windows, and the small scripts behind them were not saved (Section 6). |
| 2. Timing luck dominates | Strong | A plain description of the spread of real outcomes. |
| 3. Very rough ride | Strong on direction, moderate on exact numbers | Real prices, but one era and a few dozen overlapping windows. |
| 4. Crypto versus the stock fund | Moderate | Fair within 2020 to 2026, but that window flattered stocks. |
| 5. Small slice | Moderate | Same direction in 2020 to 2026 (a basket) and 2014 to 2026 (Bitcoin), but each rests on a handful of market cycles. |
| 6. Not a shelter; weaker diversification | Moderate | 144 months of data for stocks and gold; only three months of 20%+ Bitcoin falls for the crash check. |
| 7. Leverage and mood | Weak to moderate | The leverage-crash facts are well documented; the mood indicators rest on few independent episodes and disagree with each other. |
| 8. Costs and platform facts | Moderate to strong | Costs were measured on live order books (three snapshots, one day); the platform facts are sourced (Section 4). |

**What it adds up to.** The evidence does not say crypto is good or bad. It says crypto has been a very rough ride whose results were dominated by when you started; that none of the tricks we tested improved the odds; and that a small slice behaves very differently from a large one. Whether that trade suits you depends on things this data cannot see: how long you can leave the money alone, what else you own, and how you would behave in a deep fall.

---

## 2. What we did, and how we tried to keep it honest

**The data**

- Daily Binance.US prices for all 202 coins it lists against USDT (a coin built to track the US dollar): about 246,000 daily prices, October 2020 to 26 September 2026. Coins that were delisted before today are missing, and they are mostly the failures, so results lean optimistic.
- Longer outside history from Yahoo Finance: a stock index fund (SPY, the S&P 500 with dividends) back to 2000, Bitcoin back to September 2014, gold (through the GLD fund), GBTC, the US dollar index, and the 3-month US Treasury bill yield, which stands in for a savings account.
- Coin-by-coin facts from free public sources (Coin Metrics, CoinGecko, DefiLlama, GitHub, BscScan, BitInfoCharts, Etherscan), mood and leverage data (Alternative.me, Wikimedia, Deribit, Farside), and live Binance.US order books.

**The ground rules**

1. **Many start dates, not one.** Every plan was started in every month it could be, so the luck of timing shows up instead of hiding.
2. **Coins picked blind.** At each start date, the three most-traded coins of the previous 90 days, using nothing from the future. Early tests hand-picked Bitcoin, Ethereum and Solana, which is hindsight (a one-year hold of Solana from 1 November 2020 returned 124.5 times the money), so the blind version replaced it.
3. **Costs included:** trading fees (assumed 0.4% to 0.6% per trade, because the real fee tier was never checked), the gap between buy and sell prices, and slippage.
4. **Settings decided before looking at results**, and every version reported, including the ones that failed.
5. **"Typical" always means the middle result:** half of the start months did better, half did worse.
6. **Safety.** The research tools use only public data and cannot place orders.

**How we checked ourselves.** Three passes, and each found real errors (Appendix B lists them all):

- An **independent code review** found that a coin (XRP) had been "held" through a 912-day stretch when Binance.US had delisted it. The code silently scored those days as no movement, understating one month's return by about 20 percentage points. Fixed.
- A **source-by-source check of the outside facts** found two event dates wrong by two days each, which changed one conclusion. Fixed.
- A **final pass on the method behind the numbers** found that several "typical" figures had been computed as the upper of the two middle values (overstating them by $1 to $15), a table still on pre-fix numbers, and several sentences that claimed more than the data shows. All corrected. The four main studies were then re-run from scratch and reproduced their saved data files exactly (the content matched line for line).

This is one analyst plus its own review passes. It is not an outside audit.

---

## 3. The evidence

### 3.1 Can a cleverer buying schedule help?

| What was compared | Result |
|---|---|
| $200 all at once versus 12 weekly buys, held 1 year, 60 start months, blind coins | Spreading did better in **27 of 60** (45%) |
| The same, held 2 years, 48 start months (Bitcoin, Ethereum, Solana) | **22 of 48** (46%) |
| The same idea for a 90% stock fund / 10% crypto blend, 60 start months | **22 of 60** (37%); typical result $230 versus $231 |
| Buying more when a coin is further below its recent high: six versions (mild, medium, strong; 1- or 3-month look-back) against steady buying | Better in **19 to 26 of 60**. Typical result $211 to $216 versus $217 for steady buying. The stronger the effect, the worse it did |

An early four-year result looked like proof that spreading helps ($5,205 gained versus $3,768). On the two years held back for checking, it reversed (−$14 versus +$11). It was luck.

**Does picking the right coins help?** Coins picked with hindsight (Bitcoin, Ethereum, Solana) were compared with coins picked blind, $200 held one year, 60 start months:

| | Hindsight picks | Blind picks |
|---|---|---|
| Typical result | $220 | $220 |
| Average result | $774 | $341 |
| Best start month | $9,205 | $2,867 |
| Worst start month | $43 | $43 |
| Doubled or better | 21 of 60 | 12 of 60 |
| Lost money | 25 of 60 | 27 of 60 |

The typical result was identical. The exciting numbers were all hindsight.

**Does reacting to news help?** Fifteen major events were measured, using an equal-weight basket of Bitcoin, Ethereum and Solana. "Good" and "bad" are my labels for how each was understood at the time.

| Date | Event | That day | Next 7 days | Next 30 days |
|---|---|---|---|---|
| 21 May 2021 | China mining crackdown | −11.0% | −9.8% | −5.2% |
| 24 Sep 2021 | China bans all crypto transactions | −6.3% | +14.0% | +43.6% |
| 7 May 2022 | Terra/LUNA collapse begins | −2.4% | −23.4% | −29.8% |
| 8 Nov 2022 | FTX collapse | −14.3% | −18.8% | −18.8% |
| 5 Jun 2023 | SEC sues Binance and Coinbase | −5.8% | −9.7% | +6.3% |
| 10 Jan 2024 | Spot Bitcoin ETFs approved | +4.6% | −3.4% | +0.8% |
| 20 Apr 2024 | Bitcoin halving | +3.6% | −1.9% | +16.9% |
| 5 Nov 2024 | US presidential election | +2.9% | +29.5% | +46.7% |
| 6 Mar 2025 | Strategic Bitcoin Reserve order | −1.2% | −13.3% | −13.8% |
| 29 May 2025 | CLARITY Act introduced | −2.4% | −8.4% | −5.0% |
| 17 Jun 2025 | GENIUS Act passes Senate (68 to 30) | −1.6% | −1.0% | +23.4% |
| 17 Jul 2025 | CLARITY Act passes House (294 to 134) | +1.6% | +3.5% | +11.1% |
| 18 Jul 2025 | GENIUS Act signed into law | +0.7% | +3.5% | +11.1% |
| 14 May 2026 | CLARITY Act advanced by Senate committee (15 to 9) | +1.5% | −5.4% | −24.0% |
| 15 Sep 2026 | CLARITY Act fails Senate vote (49 to 50) | −4.4% | +16.9% | +16.3% |

- **Bad news** (6 events) fell on the day 6 of 6 times, by −7.3% on average. A week later 4 of 6 were still down (average −5.1%); a month later 3 of 6 were (average +2.1%). About half had recovered.
- **Good news** (9 events) barely moved prices: up 6 of 9 times on the day, +1.1% on average.
- **The CLARITY Act** is a proposed law setting US rules for crypto markets. It passed the House on 17 July 2025 and failed a Senate procedural vote on 15 September 2026 (49 to 50; 60 were needed), so it is **not law**. The month after its four milestones ranged from −24.0% to +16.3%. There is no consistent pattern, and each is a single event. The day it failed moved the basket −4.4%, yet 411 of 2,186 ordinary days (19%, about one in five) moved by more.
- The separate stablecoin law, the GENIUS Act, was signed on 18 July 2025.
- **Limits:** 15 events chosen in hindsight, with overlapping 7- and 30-day windows. No single event proves anything.

### 3.2 How rough is the ride?

$200 bought all at once with three blind-picked coins, then left alone. Each row uses every start month that leaves room to finish.

| Held for | Start months | Typical end | Lost money | Lost half from its own high | Typical share of days below the $200 | Longest stretch below the $200 (typical / worst) |
|---|---|---|---|---|---|---|
| 1 year | 60 | $220 | 27 | 26 | 35% | 72 / 364 days |
| 2 years | 48 | $252 | 20 | 32 | 25% | 89 / 729 days |
| 3 years | 34 | $326 | 3 | 27 | 31% | 164 / 1,080 days |
| 5 years | 10 | $194 | 5 | 10 | 44% | 662 / 1,273 days |

- **Lowest point:** $38 (−81%) in the 1-, 2- and 3-year holds; $48 (−76%) in the 5-year holds.
- **"Lost half from its own high"** means that at some point the holding was worth half or less of its highest earlier value. That is different from "fell to half of the $200 you put in", which happened in 14 of 60, 15 of 48, 15 of 34 and 7 of 10 holds.
- **Selling in a panic** after a 50% fall (and never buying back) changed the average result by −$60 (1 year), +$45 (2 years), +$33 (3 years) and −$230 (5 years). No consistent answer.
- **These rows are not a fair comparison with each other.** Each holding length can only use start dates that leave room to finish, so the samples cover different eras. The 5-year rows all begin inside the 2020 to 2021 run-up; the 3-year rows are full of starts in the cheap 2022 crash. Do not read the table as "three years is safer than five".

### 3.3 Crypto versus a stock fund versus savings

**The same $200, the same start month, crypto (blind coins) versus an S&P 500 index fund:**

| Held for | Crypto typical | Fund typical | Crypto ahead head-to-head | Crypto lost money | Fund lost money |
|---|---|---|---|---|---|
| 1 year | $220 | $236 | 28 of 60 | 27 of 60 | 12 of 60 |
| 2 years | $252 | $275 | 24 of 48 | 20 of 48 | 1 of 48 |
| 3 years | $326 | $285 | 17 of 34 | 3 of 34 | 0 of 34 |
| 5 years | $194 | $374 | 2 of 10 | 5 of 10 | 0 of 10 |

| | Crypto | Fund |
|---|---|---|
| Worst drop from a high | −81% to −88% | −24% |
| Typical share of days below the starting money | 25% to 44% | 7% to 12% |
| Longest stretch below the starting money | up to 1,273 days (3½ years) | up to 488 days (16 months) |
| Gain per unit of worst drop | −0.04 to +0.98 | +1.19 to +3.55 |

Similar money at one to three years, but far more pain. **This window flattered stocks:** with dividends the fund rose 2.48 times from October 2020 to September 2026. Identical start dates make the comparison fair within the window, but they do not make the window itself typical.

**Against a savings account** (the 3-month Treasury bill yield: under 0.1% in 2020 to 2021, about 5% in 2023 to 2024, 4.05% in 2025 and 3.66% so far in 2026): over one year the stock fund beat savings in 46 of 60 start months; crypto alone (a basket re-picked monthly) in only 29 of 60, a coin flip. Over three years or more, the stock fund and every stock/crypto blend beat savings in every start month; crypto alone did in 35 of 36 (3 years) and 10 of 12 (5 years).

**A longer look.** Six years holds one crypto boom-and-bust and no big stock crash. Two longer histories test whether the conclusions survive:

*The stock fund since 2000 (includes the 2000 to 2002 and 2008 to 2009 crashes):*

| Held for | Start months | Typical | Worst start | Lost money | Beat savings |
|---|---|---|---|---|---|
| 1 year | 308 | $226 | $113 | 68 (22%) | 236 |
| 3 years | 284 | $276 | $117 | 47 (17%) | 233 |
| 5 years | 260 | $341 | $142 | 28 (11%) | 212 |
| 10 years | 200 | $513 | $168 | 10 (5%) | 187 |

The fund's worst drop was **−55%** (October 2007 to March 2009), not the −24% of 2020 to 2026, and its longest stretch below the starting money was 6.1 years. In 5,000 reshuffled versions of 2000 to 2026, the fund lost money in 29% of 1-year, 15% of 5-year and 6% of 10-year futures, and fell by half in 4% of 5-year and 10% of 10-year futures.

*Bitcoin alone since September 2014 (three to four crypto cycles):* 3- and 5-year holds **never lost money** (0 of 108 and 0 of 84). Bitcoin alone typically turned $200 into $880 (3 years) and $2,220 (5 years), but fell as much as −83% along the way and spent up to 2.8 years below the starting money. **Big caveat:** Bitcoin is the coin that survived and won. Choosing it today is hindsight, and the blind basket above did far worse. Bitcoin's price before about 2019 is stitched together from exchanges that no longer exist and is less reliable.

### 3.4 A small slice, "possible futures", and the "optimal mix" trap

**A small slice of crypto inside a stock fund** (rebalanced once a year; the crypto part follows the three biggest coins, re-picked monthly, switching costs included). Held 3 years, 36 start months:

| Mix | Typical end | Average end | Lost money | Beat savings | Worst-case drop | Typical worst drop |
|---|---|---|---|---|---|---|
| Savings account only | $227 | $224 | 0 of 36 | n/a | 0% | 0% |
| Stock fund only | $281 | $304 | 0 of 36 | 36 of 36 | −24% | −22% |
| Fund 95% + crypto 5% | $306 | $323 | 0 of 36 | 36 of 36 | −29% | −24% |
| Fund 90% + crypto 10% | $343 | $341 | 0 of 36 | 36 of 36 | −35% | −25% |
| Fund 80% + crypto 20% | $386 | $375 | 0 of 36 | 36 of 36 | −43% | −30% |
| Crypto only | $465 | $520 | 0 of 36 | 35 of 36 | −79% | −67% |

- Each extra 10% of crypto added roughly 10 points to the *worst-case* drop (the single worst start month). The *typical* start month's worst drop rose more gently, from 22% to 25% with a 10% slice.
- Over **one year** the slice did not raise the typical result (fund $235; 10% crypto $231; 20% crypto $230). It raised the average ($230, $239, $248), and the number of losing start months edged up (11, 13, 14 of 60).
- **The holding rule matters.** Re-picking the three biggest coins each month lost money in 0 of 12 five-year windows, while buying the 2020 top three once and holding lost in 5 of 10. These are different rules on different start sets, and the monthly version may partly reflect survivorship (only coins still listed today can be picked).

**5,000 possible futures.** Six years of history is really one sequence of events. To see how much depended on the order, real 30-day chunks of the 2020 to 2026 record were reshuffled 5,000 ways. Scenario A: the future looks like 2020 to 2026. Scenario B: the same ups and downs but only half of the growth above savings. Held 5 years:

| Mix | Typical (A) | Bad 1-in-20 (A) | Lose money (A) | Typical (B) | Bad 1-in-20 (B) | Lose money (B) |
|---|---|---|---|---|---|---|
| Savings account only | $234 | $229 | 0% | $234 | $229 | 0% |
| Stock fund only | $425 | $246 | 1% | $304 | $176 | 11% |
| Fund 95% + crypto 5% | $476 | $258 | 1% | $319 | $175 | 10% |
| Fund 90% + crypto 10% | $520 | $258 | 1% | $327 | $169 | 11% |
| Fund 80% + crypto 20% | $607 | $252 | 2% | $340 | $151 | 15% |
| Crypto only | $958 | $63 | 18% | $237 | $16 | 46% |

- A 5% to 10% slice barely changed the odds of losing money; it mostly raised the good outcomes.
- **Crypto alone lost at least half from a high point in 98% (A) to 100% (B) of 5-year futures**, 91% to 96% of 3-year futures and 49% to 61% of 1-year futures.
- The whole range of crypto-only results depends on the assumption: nearly 5 times the money if the future resembles 2020 to 2026, about the same as savings ($237 versus $234) if only half that growth returns.
- **Most important caveat:** the stock fund almost never "falls by half" here only because no big stock crash happened in 2020 to 2026. Reshuffling the 2000 to 2026 history instead (Section 3.3) shows a real chance of it.

**Is there a mathematically "best" mix?** Every mix of crypto, stocks and cash in 10% steps was scored two ways.

- On the **one real 2020 to 2026 path**, ranked by "gain per unit of worst drop", **100% cash came first** (it ended with $241). That is a trap: dividing by an almost-zero drop makes even a tiny gain look superb. Fourth and fifth on that list were a 60% crypto / 40% stocks mix and a 50/50 mix, which ended with $1,239 and $1,149, because that path held the biggest crypto run-up on record.
- Across **1,000 resampled 3-year futures**, the same ratio favoured 10% stocks and 90% cash instead (100% cash second, about 0% chance of losing money). Ranked by typical ending value, **100% crypto came first** ($561 typical) but lost money in 21% of futures, against about $220 for cash.
- The "best" mix changes with the yardstick and the history used. Be sceptical of any single "optimal allocation" claim.
- For a 90% stock / 10% crypto blend, spreading the buying over 12 weeks did no better than buying at once (better in 22 of 60 start months; $230 versus $231).

### 3.5 What sits underneath each coin

Prices say little about whether anyone uses a coin. These tables use free public data; "address" is not the same as "person", so trends matter more than levels.

| Coin | Real use, 3-year change (daily active addresses / transactions) | New coins created per year | Who holds it | Code activity (commits, past year, rough) | Main worry |
|---|---|---|---|---|---|
| Bitcoin | −35% / +41% | 0.82% (hard cap 21 million, 96% issued) | About 15% sits in about 88 large addresses, mostly exchanges and ETF custodians | 3,800 to 4,500 | Price runs ahead of direct use (ETF holders never touch the network); the survivor in hindsight |
| Ethereum | +83% / +113% | 0.82% (no cap) | Broad. The staking contract (74.9%) is pooled from many stakers; the largest named holders are exchanges, each under 2% | About 1,160 (one of several software clients) | Usage on add-on networks is not counted, so use is understated |
| XRP | +83% / +106% | None new; 63% of the 100 billion cap is in circulation, and most of the rest is held back by the company Ripple in escrow (balance not independently checked) | Control concentrated in one company | About 1,300 (a recent surge) | Concentration of control; delisted on Binance.US for 912 days |
| Solana | No free data (money locked in its apps: $6.68 billion, −40% in a year) | No cap | Not studied | About 4,250 (counted differently, about 15% higher basis) | Missing usage data; the widest one-year range of past outcomes ($35 to $1,567) |
| Zcash | −44% / +97%, from a tiny base (about 5,700 addresses a day) | 4.0% (cap 21 million, 81% issued) | Not studied | About 830 (a recent surge) | Price far ahead of use (value per address up 57 times in 3 years); thin dollar market; possible regulatory risk for privacy coins |
| Dogecoin | −21% / −86% (addresses −37% in the last year) | 3.3%, forever (no cap) | Top 10 addresses hold about 58% (Robinhood alone 17%) | 51 | Shrinking use; concentrated ownership |
| BNB | Transactions +362% (16.4 million a day); new addresses +49% (−14% in the last year) | Cap 200 million, 67% issued | Tied to one exchange group | About 1,060 (down sharply in the last 3 months) | Value tied to one exchange; much of the activity is probably automated (very low fees) |

| Coin | Market value | "Expensive?" score today (Section 3.7) | Below its record high | Price impact of a $200 buy (dollar pair) | Money on offer within 1% of the price |
|---|---|---|---|---|---|
| Bitcoin | $1,698 bn | 1.58 | −33% | 0.00% | $1,563,270 |
| Ethereum | $331 bn | 1.15 | −45% | 0.01% | $1,374,951 |
| XRP | $96 bn | 1.02 | −58% | 0.01% | $808,923 |
| Solana | $72 bn | n/a (paid data) | −59% | 0.03% | $750,970 |
| Zcash | $28 bn | 2.60 | −48% (record was its 2016 launch spike) | 0.12% (its USDT pair: 0.03%) | $46,868 (USDT pair: $242,325) |
| Dogecoin | $15 bn | 0.82 | −87% | 0.02% | $160,905 |
| BNB | $103 bn | n/a (paid data) | −43% | 0.03% | $170,988 |

Ownership figures come from BitInfoCharts and Etherscan rich lists. The biggest "addresses" are mostly exchanges holding coins for millions of customers, so concentration by address overstates concentration by person. Bitcoin's own source page contradicted itself (its summary said 7.4% for the top 100 addresses, its table about 15% for the top 88 above 10,000 coins); the table was used. Coins lost for good are not accounted for.

### 3.6 What moves crypto

The "move-together score" runs from −1 (opposite) through 0 (unrelated) to +1 (in lockstep), measured on monthly changes unless stated.

- **Stocks.** Bitcoin versus the stock fund: +0.35 over 144 months, but +0.12 in 2014 to 2019, +0.58 in 2020 to 2022 and +0.44 in 2023 to 2026. In the stock fund's 12 worst months Bitcoin fell in 11 (March 2020: stocks −12.5%, Bitcoin −25.1%; June 2022: −8.2% and −37.8%). In the fund's 12 best months Bitcoin rose in 10, by 17.6% on average. It tends to move with stocks in both directions, sometimes by far more (June 2022: Bitcoin −37.8% against stocks −8.2%) and sometimes by less.
- **Interest rates.** Overall score +0.00: no consistent relationship. During the 2022 to 2023 rate rises Bitcoin fell 32.3% while the stock fund rose 7.4%, but only 16 months had rate moves of 0.25 points or more, and that stretch also contained the Terra and FTX collapses, so the data cannot separate the causes.
- **The US dollar.** Score −0.14. In the 26 months the dollar strengthened 2% or more, Bitcoin averaged −3.1%; in the 21 months it weakened 2% or more, +9.4%. Weak but consistent.
- **Gold.** Score **+0.06**, essentially zero. In gold's 12 worst months Bitcoin rose in 10. "Digital gold" is a comparison of what the two represent, not a price relationship visible in this data.
- **Other coins versus Bitcoin.** Whole-period scores run from 0.20 (Zcash) to 0.74 (Ethereum). In the three months Bitcoin fell 20% or more (May 2021, June 2022, June 2026), **every coin fell too, by 18% to 36% on average**. Owning several coins spreads risk in ordinary months, not in crashes.

**Diversification is weakening.** Tracked in 90-day windows (score against Bitcoin):

| Coin | First 18 months | Middle 24 months | Latest ~30 months |
|---|---|---|---|
| Ethereum | 0.77 | 0.85 | 0.84 |
| XRP | 0.37 | 0.62 | 0.70 |
| Solana | 0.41 | 0.71 | 0.80 |
| Zcash | 0.62 | 0.64 | 0.44 |
| Dogecoin | 0.52 | 0.64 | 0.80 |
| BNB | 0.66 | 0.71 | 0.71 |

Five of six rose (BNB only slightly); Zcash fell. The six-coin average went from about 0.56 to about 0.71.

**The market has calmed, yet coins move together more.** Bitcoin's yearly price swing (annualised volatility) fell from 73% (2014 to 2017) and 74% (2018 to 2020) to 64% (2021 to 2023) and 48% (2024 to 2026, the ETF era), still roughly two and a half to three times the 15% to 20% commonly quoted for the S&P 500. Both findings are real.

**GBTC.** Before January 2024 the main way to own Bitcoin in a brokerage account was GBTC, a fund that traded away from Bitcoin's price. Exact historical figures were not free, so a stand-in was built (GBTC's cumulative return minus Bitcoin's). It swung 122 percentage points before GBTC became a true ETF on 11 January 2024 and 10 points in the 2.7 years since, the tightening expected once arbitrage can hold the price to the coin's value.

### 3.7 Is it expensive right now?

MVRV divides a coin's total market value by what its holders collectively paid for it: 1.0 means holders are at break-even on average, 3 means they are up threefold, below 1 means they are at a loss.

| Coin | Today (26 Sep 2026) | Lowest ever | Highest ever | Higher than today on |
|---|---|---|---|---|
| Bitcoin | 1.58 | 0.56 | 5.88 | 58% |
| Ethereum | 1.15 | 0.30 | 6.09 | 56% |
| XRP | 1.02 | 0.31 | 146.63 | 55% |
| Zcash | **2.60** | 0.26 | 2.91 | under 0.5% |
| Dogecoin | 0.82 | 0.19 | 10.31 | 58% |

Bitcoin's readings at past turning points: peaks of 4.25 (December 2017), 3.38 (April 2021), 2.72 (November 2021), 2.68 (March 2024) and 2.29 (October 2025); bottoms of 0.69 (December 2018) and 0.78 (November 2022); 1.13 in June 2026. Bitcoin, Ethereum, XRP and Dogecoin sit in the middle to low part of their own range. Zcash sits near its all-time high, consistent with its price running ahead of its use. **Did it predict Bitcoin's next 12 months?** Started below 1.0: up every time (22 months, typical +84%). Started between 1.5 and 2.5 (today's zone): down 28 of 70 times, typical +62%. Above 3.5: down 2 of 3. Neighbouring months overlap heavily and come from only about four cycles, so treat it as a "stretched or not" gauge, not a timing signal.

### 3.8 Crowd mood, money flows and borrowed money

**Fear & Greed Index** (Alternative.me, daily since February 2018; 3,157 days). Today it reads 70, "Greed", higher than 79% of days on record. What Bitcoin did next:

| Reading that day | Days | Average next 30 days | Average next 90 days |
|---|---|---|---|
| Extreme fear (0 to 24) | 679 | +3.2% | +2.1% |
| Fear (25 to 44) | 915 | +0.5% | +8.9% |
| Neutral (45 to 55) | 496 | +4.2% | +19.5% |
| Greed (56 to 75) | 785 | +5.0% | +16.4% |
| Extreme greed (76 to 100) | 282 | **+13.1%** | **+39.9%** |

That is the opposite of the popular "sell when everyone is greedy". A likely reason is that Bitcoin trended up strongly across this history and greed readings cluster inside uptrends; it is not a signal to chase greed.

**Public attention.** Wikipedia's "Bitcoin" page averaged 6,656 views a day over the last 30 days, against a peak of 347,624 on 8 December 2017. All ten biggest spikes on record fall in November and December 2017.

**Real money.** Bitcoin ETFs (Farside Investors, read once on 27 September 2026; $65.3 billion of net inflow since January 2024): outflows ran for a week before the vote (−$463 million over 8 to 11 September), then −$450 million on 15 September (the day the CLARITY Act failed) and −$296 million on the 16th. Money flowed back in on each of the next seven trading days, from about $135 million to $999 million a day. That matches the price recovery, but it is one 14-day episode, not a proven pattern.

**Borrowed money ("leverage").** Traders can bet with borrowed money, and when a price move wipes out their deposit the exchange force-closes their bets, which pushes the price further and force-closes more. This project's own tool cannot use leverage; it is studied here only as something happening in the market. Data: Deribit's Bitcoin perpetual futures, back to June 2019.

| Traders' crowding (funding rate) | Days | Average next 30 days | Average next 90 days |
|---|---|---|---|
| Most crowded short bets (lowest 10%) | 267 | **+8.9%** (fell 33% of the time) | +18.0% |
| Middle 80% | 2,142 | +3.4% | +12.2% |
| Most crowded long bets (highest 10%) | 267 | +5.1% (fell 48% of the time) | **+20.5%** |

- **The order flips with the time horizon.** At 30 days, crowded shorts did best; at 90 days, crowded longs did. So this measure disagrees with Fear & Greed at 30 days and roughly agrees at 90. A signal whose direction depends on the horizon chosen is not dependable.
- **New money (stablecoin supply).** Months when the supply of USDT and USDC shrank (19 months) preceded Bitcoin's worst returns (average −5.7%, down 13 of 19); the fastest-growing months (19) preceded the best (+9.8%, down 10 of 19). This one points the intuitive way, but the samples are small.
- **The largest leverage crash on record was 10 to 11 October 2025**: over $19 billion of leveraged positions force-closed, more than 1.6 million accounts, about 9 times the February 2025 crash and 19 times the FTX collapse, by press tallies. The trigger was a tariff announcement with nothing to do with crypto. This project's own Binance.US data confirms it: Bitcoin closed at $124,798 on 6 October and fell to $109,278 by the 11th. The earlier record, about $8.6 billion in a day in May 2021, followed China's crackdown.
- **Across the four biggest crashes** (May 2021, Terra in May 2022, FTX in November 2022, October 2025) the trigger was always news, a stablecoin failure or a geopolitical announcement, not leverage itself. But a market already carrying a lot of one-directional leveraged betting turned an ordinary bad-news day into a much larger move.

### 3.9 What it costs to trade

Live Binance.US order books, three snapshots on 27 September 2026, excluding the exchange's fee. "Price impact" is how much worse than the mid-price a market buy fills.

| Pair | $200 buy | $1,000 buy | $10,000 buy | $50,000 buy | On offer within 1% of the price |
|---|---|---|---|---|---|
| BTC/USD | 0.00% | 0.00% | 0.01% | 0.05% | $1,563,270 |
| ETH/USD | 0.01% | 0.01% | 0.02% | 0.05% | $1,374,951 |
| XRP/USD | 0.01% | 0.01% | 0.02% | 0.08% | $808,923 |
| SOL/USD | 0.03% | 0.05% | 0.08% | 0.14% | $750,970 |
| ZEC/USD | 0.12% | 0.12% | 0.18% | **5.04%** | $46,868 |
| ZEC/USDT | 0.03% | 0.04% | 0.07% | 0.10% | $242,325 |
| DOGE/USD | 0.02% | 0.02% | 0.10% | 0.16% | $160,905 |
| BNB/USD | 0.03% | 0.05% | 0.07% | 0.14% | $170,988 |

- The tests assumed 0.10% (half spread, half slippage), which the table confirms as reasonable and slightly cautious.
- The exchange's own fee is the larger cost. The tests assumed 0.4% for one type of order and 0.6% for the other, about $1.19 on a $200 purchase. Binance.US fees are tiered, and your actual rate was never checked (Section 6).
- Thin spots: Zcash's dollar pair (a $50,000 buy would cost about 5%), then Dogecoin and BNB. Bitcoin, Ethereum and XRP stay under 0.1% even at $50,000.

### 3.10 What $200 has done in the past, coin by coin

Every 1- and 3-year holding period in each asset's own price record, sorted from worst to best: "disaster" is worse than 19 in 20 past periods, "bad" worse than 4 in 5, "good" better than 4 in 5. These are frequencies from the past, not forecasts. A coin that failed and disappeared cannot appear in a record like this.

| Asset (record starts) | 1 year: disaster | bad | typical | good | Lost money in 1 year | 3 years: typical | Lost money in 3 years |
|---|---|---|---|---|---|---|---|
| Savings account (2000) | $200 | $200 | $202 | $208 | 0% | $208 | 0% |
| Stock fund, SPY (2000) | $156 | $194 | $226 | $244 | 22% | $276 | 17% |
| Bitcoin (2014) | $82 | $158 | $345 | $566 | 28% | $895 | 0% |
| Ethereum (2017) | $54 | $115 | $229 | $396 | 45% | $571 | 15% |
| XRP (2017) | $69 | $102 | $195 | $628 | 51% | $425 | 18% |
| Solana (2020) | $35 | $97 | $239 | $1,567 | 43% | $930 | 6% |
| Zcash (2017) | $41 | $79 | $190 | $483 | 51% | $140 | 60% |
| Dogecoin (2017) | $54 | $93 | $209 | $486 | 47% | $1,664 | 13% |
| BNB (2017) | $110 | $157 | $272 | $517 | 34% | $1,447 | 1% |

The records start at different dates, so this is not a like-for-like race. Dogecoin's and BNB's 3-year figures are heavily influenced by their 2020 to 2021 surges from low prices. Note that the stock fund lost money in 17% of its own 3-year periods since 2000.

### 3.11 Background only: what if you were withdrawing, not adding?

Not your situation (you are building savings), but a well-known risk. Real 3-year return sequences for a 100% crypto account were replayed forwards and in reverse order, with $5 a month withdrawn either way. That is 30% of the original $200 a year, a deliberately heavy rate chosen to make the effect visible. With no withdrawals the order cannot matter; every difference here comes from the withdrawals.

- The order changed the ending balance by more than $20 in all 36 of 36 windows. Typical ending balance: $227 forwards, $158 in reverse.
- Starting between March 2021 and May 2022, the forwards account was wiped out to $0 in every window, while the identical returns in reverse left $99 to $301. Starting in March to August 2023 it flipped: reversed accounts hit $0 or close to it while forwards accounts kept $240 to $305.
- The lesson is not that one direction is safer; it is that a crash early in a withdrawal period is far more damaging than the same crash late. Money going in, as in your case, is much less exposed to this.

---

## 4. Outside the price charts: the exchange, the rules, tax

**The exchange.**

- **Binance.US** wound down dollar trading in 2023 and restored it in 2025. Dollar deposits were suspended in June 2023, days after the SEC sued Binance and its US arm; most dollar trading pairs were removed on 26 June 2023; by October 2023 users could no longer withdraw dollars directly; dollar services returned on 19 February 2025. In Binance.US's own price feed, dollar-pair prices stop on 27 June 2023 (Dogecoin, Stellar, Hedera) or 14 July 2023 (Bitcoin, Ethereum, Solana and others) and resume on 19 to 20 February 2025, a **586-day** span between the last price and the next. That is why the long tests use the USDT versions. (Why the major pairs stopped specifically after 14 July was not found in any source.)
- **XRP** was delisted from Binance.US on 13 January 2021 during the SEC's case against Ripple and returned (against USDT) on 14 July 2023: 912 days.
- **Binance** (the global exchange, a separate company from Binance.US) pleaded guilty and agreed to pay about **$4.3 billion** to US authorities on 21 November 2023.
- **Bybit**, a large exchange, lost about **$1.5 billion** to a hack on 21 February 2025, attributed by the FBI to North Korea and reported as the largest theft in crypto history.

**About your view on platform risk.** You told me you see platform trouble as less of an issue now, because governments are increasingly adopting blockchain technology and there have been fewer platform problems in recent years. Rules moving toward clarity is a fair reading of the direction. But the record we checked does not yet show the trouble fading: Binance.US's own dollar service was down for about 20 months, ending February 2025; Binance's settlement came in November 2023; the largest exchange hack on record came in February 2025; and the main US market-structure bill failed in September 2026. None of that says platforms are riskier than before. It says "fewer problems lately" is not yet visible in these events, and whether new rules will reduce the problems is a forward-looking question that price history cannot test.

**The rules.** The **GENIUS Act** (rules for payment stablecoins) was signed on 18 July 2025. The **CLARITY Act** (rules for crypto markets) passed the House 294 to 134 on 17 July 2025 and failed a Senate procedural vote 49 to 50 on 15 September 2026; it is not law. A **Strategic Bitcoin Reserve** executive order was signed on 6 March 2025.

**Tax** (US; a factual note, not tax advice, and none of the results in this report are after tax).

- The IRS treats crypto as **property**. Selling it, swapping it for other crypto, or spending it is a taxable event.
- Held **one year or less**, a gain is short-term; held **more than one year**, it is long-term (taxed at different rates).
- The "**wash sale**" rule, which stops stock investors claiming a loss if they rebuy the same investment within 30 days, does **not** apply to crypto in the two tax-firm articles we could read (March and May 2026). Proposals to extend it exist; a CNBC headline of 28 July 2026 says Congress is renewing the push. None had become law in those sources, and I could not confirm the current status of any specific bill.

**Custody.** Coins held on an exchange are held by the exchange, so access depends on it staying solvent, secure and open (compare Binance.US's dollar suspension). Holding coins yourself removes that dependence but adds the risk of losing the keys, which nobody can recover. We did not research whether, or on what terms, retirement accounts can hold crypto.

---

## 5. The case for, and the case against

**Reasons someone might hold some crypto (what the data supports)**

- Over multi-year holds, past results were positive far more often than not: Bitcoin since 2014 never lost money over 3 or 5 years, and the blind basket lost money in only 3 of 34 three-year holds.
- A 5% to 10% slice lifted typical results for holds of two years or more in both data sets (the 2020 to 2026 basket and 2014 to 2026 Bitcoin), though not over one year in the 2020 to 2026 basket.
- At $200, costs are negligible on the main coins.
- The market has calmed: Bitcoin's yearly price swing fell from 73% to 48%, and ETFs bring a large, measurable flow of ordinary money.
- Plain steady buying did as well as anything cleverer, so simplicity gave up nothing measurable.

**Reasons for caution**

- The drops are severe and long: −81% at worst, up to 3½ years below the money put in, and a loss of half from a high point in most multi-year holds.
- In the same 2020 to 2026 window a plain stock fund produced similar typical money at one to three years with a worst drop of −24%, not −81% to −88%, and a much better gain per unit of pain.
- It has not been a shelter: it fell with stocks in 11 of the stock fund's 12 worst months, and has no gold-like behaviour.
- Owning several coins protects less than it used to, and in crashes every coin fell together.
- Survivorship and hindsight flatter the record: delisted coins are missing, and Bitcoin and Solana look great only because they won.
- Leverage cascades and exchange events can create sudden gaps that calm-period price history does not show.
- Rules are unsettled (CLARITY failed) and some coins have real concentration or usage problems (Dogecoin, Zcash, XRP's escrow).

---

## 6. What this research cannot tell you

1. **One main era.** The blind-basket studies cover October 2020 to September 2026: one crypto boom-and-bust and no big stock crash. Longer history exists only for the stock fund (to 2000) and Bitcoin (to 2014).
2. **Fewer independent observations than the counts suggest.** Sixty one-year start months from about six years of data are really about five to six separate years, because consecutive windows overlap. The 5-year rows have 10 to 12 start months, all in 2020 to 2021.
3. **Survivorship.** Only coins listed today can be picked. Blind picking removes hindsight about *which* coin, not the missing failures. Results lean optimistic.
4. **USDT stands in for the dollar.** Its daily closes ranged from $0.9715 to $1.0176 over the 550 days that could be checked (March 2025 onward), so a "dollar" is off by up to about 3%.
5. **Costs are assumed, not measured.** The fee tier was never checked because the read-only account reader was never successfully run. At $200 it barely matters (a 1% fee costs about $2).
6. **Taxes are ignored.**
7. **Free data has gaps.** Missing: Solana usage history, Solana and BNB valuation, the M2 money supply (the Federal Reserve's FRED site was unreachable from here), Google Trends, crypto-specific betting-market odds, and Congress members' trading disclosures in crypto-related stocks (needs a paid data tier). Developer activity is a rough commit count for one code project per coin.
8. **The event study** uses 15 hindsight-chosen events; the good/bad labels are judgment; the 7- and 30-day windows overlap.
9. **Mood, valuation and leverage results** rest on few independent episodes (about four cycles for valuation), and the leverage data is Bitcoin on one exchange (Deribit), not Binance.US.
10. **Not modelled:** any rule for *selling* (apart from the panic illustration), exchange failure, and your own circumstances.
11. **Reproducibility gaps.** Four studies were re-run from scratch and match their saved data. But the small driver scripts for the first three tests (the start-date sweep, blind selection and dip buying), the BNB usage calculation and the GitHub commit counts were run ad hoc and never saved in the repository. Their result files are saved and were re-summarised for this report, but those tests cannot be re-run from the repository alone.
12. **Errors were found in my own work** (Appendix B). More may remain. This was one analyst plus its own review, not an outside audit.

---

## 7. Questions only you can answer

The data can describe each of these; it cannot answer them.

1. **How long can this money stay untouched?** One-year holds lost money in 27 of 60 start months; the 5-year holds that began in 2020 to 2021 lost money in 5 of 10; Bitcoin alone since 2014 never lost over 3 or 5 years, but it is the survivor.
2. **What would you do at −50%? At −80%?** Half or more of the value was lost from a high point in most multi-year holds, the lowest point was −81%, and the longest stretch below the money put in was 3½ years. The panic-selling illustration found no consistent winner, so the main risk is behaviour, not arithmetic.
3. **How much is the worst case worth to you in dollars?** On $200 the worst case was about −$162. The typical difference between crypto and the stock fund after one year was $16 ($220 versus $236).
4. **What else could this $200 do?** A savings account earned roughly 4% a year recently; a stock fund had a worst 1-year outcome of $113 since 2000; and paying down any debt that costs more than that is a certain return equal to its interest rate.
5. **Is it money you might need in the next few years?** The rough ride in Section 3.2 matters most for money with a deadline.

---

## 8. What could change these conclusions, and what is left

- **A longer, less flattering record for coins**, including ones that were delisted. This is a data problem: free sources do not have it.
- **Saving and re-running the missing drivers** for the first three tests, so every result in this report can be reproduced from the repository.
- **A "watch it live with no money" tracker** that records what a plan would do each day from now on. It is the only genuinely out-of-sample test available.
- **Filling the data gaps** in Section 6 (Solana usage, Solana and BNB valuation, M2, betting-market odds), and reading your real fee tier once with the read-only account tool.
- **Stopping here** is also a legitimate choice: the research answered its question, and the answer was mostly "the things you can control did not matter much".

---

## Appendix A: plain-English glossary

- **Start month / typical result.** Each test was started in every month it could be. "Typical" is the middle outcome: half the start months did better, half did worse.
- **Worst drop / lost half from its high.** The largest fall from a previous high point during the holding period, even if it later recovered. Different from a loss against the money put in.
- **Blind pick / hindsight.** A blind pick chooses coins using only what was known at the time. Hindsight picks coins already known to have done well, which flatters results.
- **Survivorship.** Failed coins vanish from today's lists, so history built from today's lists looks better than reality.
- **Stablecoin / USDT.** A coin designed to hold a price of one US dollar. USDT ("Tether") is the largest.
- **ETF.** A fund that trades like a share. Spot Bitcoin ETFs hold real Bitcoin.
- **Move-together score (correlation).** From −1 to +1: +1 means two things move in lockstep, 0 means unrelated, −1 means opposite.
- **Volatility.** How much a price swings, expressed as a yearly percentage.
- **MVRV.** A coin's market value divided by what its holders collectively paid for it; a rough "stretched or not" gauge.
- **Leverage / liquidation.** Betting with borrowed money. A liquidation is the exchange force-closing a bet when the loss uses up the deposit.
- **Funding rate.** The fee that crowded leveraged traders on one side pay those on the other; a sign of how one-sided the betting is.
- **Possible futures.** Reshuffling real past chunks of history into many alternative orderings to see how much depended on the order.
- **Order book depth / price impact.** How much is on offer near the current price, and how much worse than the mid-price a purchase of a given size fills.
- **Spot.** Buying the coin itself, not a bet on its price.
- **Index fund (SPY).** A fund that holds the S&P 500, 500 large US companies. Its prices here include dividends.
- **Treasury bill yield.** The interest rate on short-term US government debt, used here as the stand-in for a savings account.

---

## Appendix B: corrections made along the way

Nothing was silently overwritten; every item is in the git history.

| What was wrong | How it was found | Effect | Status |
|---|---|---|---|
| XRP's 912-day delisting scored as "no movement" inside the monthly-basket studies | Independent code review | One month's return understated by about 20 points; in the possible-futures study the typical result of every crypto-containing mix rose by about 1% to 16% | Fixed |
| Two event dates wrong by two days (China crackdown 19 to 21 May 2021; Terra 9 to 7 May 2022) | Source-by-source check | The "bad news mostly recovers within a month" conclusion became "about half recovered" | Fixed |
| A results printout hard-coded "the 2021 China crash" as the biggest event-day move | Consequence of the row above | Named the wrong event once the dates changed (FTX, −14.3%, is the biggest) | Fixed |
| "Typical" computed as the upper of the two middle values in four scripts | Final pass on the method | Typical results overstated by $1 to $15 (largest: 3-year crypto, $341 versus $326) | Fixed; scripts and docs updated and re-run |
| The small-slice table still showed pre-fix numbers | Final pass | 90/10 mix, 3 years: $325 shown, $343 correct | Fixed |
| The "before fix" file for that study was a copy of the after-fix data | Final pass | Audit trail only | Replaced with the true pre-fix data from git |
| The review's summary said "every crypto-containing row moved up 1% to 16%" | Final pass | Only 100 of 780 mix-study results changed at all (by −0.1% to +20.7%) | Wording corrected |
| Optimal-allocation note compared a 3-year resampled result ($561) with a 6-year real-path result ($241) | Final pass | Not like-for-like | Corrected |
| "Fell by half in 93% to 100% of 5-year futures" mixed horizons | Final pass | Correct: 98% to 100% (5-year); 91% to 96% (3-year) | Corrected |
| Funding-rate finding described as a clean contradiction of Fear & Greed | Final pass | It holds at 30 days only and flips at 90 days | Reframed |
| ETF-flow text said "four of the next five days" | Final pass | Money came in on each of the next seven trading days | Corrected |
| "A third or more of days below your stake" | Final pass | Typical share is 25% to 44% by holding length | Corrected |
| "About 239,000 daily prices" | Final pass | About 246,000 (245,980) | Corrected |
| A specific tax-bill number seen in a search snippet | Final pass | No readable source confirmed it | Dropped |
| Leverage data: a paging error, then a discovery that OKX keeps only about 3 months of history | Live testing | Switched to Deribit | Fixed |
| A liquidation figure quoted from memory ($8 billion) | Source check | Actually $8.6 billion; checking it surfaced the $19 billion October 2025 event | Added |
| The "gain per unit of drop" ranking favoured 100% cash | Reading the output critically | Added a growth ranking and a warning | Fixed |
| Engine-level issues during construction (a candle-paging bug, a credential prompt that hung the tests, safety-audit false alarms) | Tests and live checks | None on results | Fixed |

**Disclosed but not fixable:** the unsaved drivers for the first three tests, the BNB usage calculation and the GitHub commit counts (Section 6, item 11).

---

## Appendix C: sources

**Outside facts** (the full source table, with more links, is in [`reference_verification.md`](reference_verification.md)):

- Binance.US dollar wind-down and return: [Cointelegraph](https://cointelegraph.com/news/binance-us-suspends-usd-deposits-warns-withdrawal-pause-incoming), [Fox Business](https://www.foxbusiness.com/markets/binance-us-suspends-usd-deposits-after-regulators-crackdown), [Binance.US notice of 26 June 2023 changes](https://support.binance.us/en/articles/9843320-binance-us-provides-update-on-usd-withdrawals-trading-offerings), [CoinDesk, October 2023](https://www.coindesk.com/policy/2023/10/17/binanceus-halts-direct-dollar-withdrawals), [Binance.US blog, USD services return](https://blog.binance.us/usd-services-return/)
- XRP delisting and return: [Crypto Briefing](https://cryptobriefing.com/binance-delists-xrp-trading-ripple-slawsuit/), [AMBCrypto](https://ambcrypto.com/xrp-will-be-delisted-from-binance-us-as-well/), [U.Today](https://u.today/xrp-relisted-on-binance-us-details)
- Binance $4.3 billion settlement: [US Department of Justice](https://www.justice.gov/archives/opa/pr/binance-and-ceo-plead-guilty-federal-charges-4b-resolution), [US Treasury](https://home.treasury.gov/news/press-releases/jy1925)
- Bybit hack: [FBI](https://www.fbi.gov/investigate/cyber/alerts/2025/north-korea-responsible-for-1-5-billion-bybit-hack), [Elliptic](https://www.elliptic.co/blog/bybit-hack-largest-in-history)
- CLARITY Act: [Congress.gov, H.R. 3633](https://www.congress.gov/bill/119th-congress/house-bill/3633/all-info), [CNBC, 15 September 2026](https://www.cnbc.com/2026/09/15/senate-cloture-vote-on-clarity-act-fails-dealing-regulatory-setback-to-crypto-industry.html), [CoinDesk live updates, 15 September 2026](https://www.coindesk.com/business/2026/09/15/live-updates-bitcoin-slides-from-nearly-usd80-000-as-senate-votes-on-clarity-act)
- GENIUS Act: [White House fact sheet](https://www.whitehouse.gov/fact-sheets/2025/07/fact-sheet-president-donald-j-trump-signs-genius-act-into-law/), [Congress.gov, S. 1582](https://www.congress.gov/bill/119th-congress/senate-bill/1582/text)
- Strategic Bitcoin Reserve order: [White House](https://www.whitehouse.gov/fact-sheets/2025/03/fact-sheet-president-donald-j-trump-establishes-the-strategic-bitcoin-reserve-and-u-s-digital-asset-stockpile/)
- October 2025 liquidation: [CoinDesk](https://www.coindesk.com/research/market-spotlight-the-19-billion-liquidation-that-shook-crypto), [Forbes](https://www.forbes.com/sites/digital-assets/2025/10/13/cryptos-black-friday-inside-the-19-billion-market-meltdown/), [KuCoin list of the largest liquidations](https://www.kucoin.com/blog/top-10-largest-crypto-liquidation-events-of-all-time)
- Tax: [IRS, digital assets](https://www.irs.gov/filing/digital-assets); wash-sale status: [Gordon Law Group, 15 May 2026](https://gordonlaw.com/learn/crypto-wash-sale-rule-in-2026-is-the-loophole-finally-closed/), [ChainWise CPA, 8 March 2026](https://chainwisecpa.com/crypto-wash-sale-2026/), [CNBC headline, 28 July 2026](https://www.cnbc.com/2026/07/28/congress-renews-push-to-end-crypto-wash-sale-tax-loophole.html) (the article itself could not be opened)

**Data sources:** Binance.US public API (prices, order books); Yahoo Finance (SPY with dividends, Bitcoin since 2014, GLD, GBTC, dollar index, 3-month Treasury bill yield); Coin Metrics community data (usage, supply, MVRV); CoinGecko (supply, market values); DefiLlama (Solana money locked in apps); GitHub (commit counts); BscScan (BNB usage); BitInfoCharts and Etherscan (largest holders); Alternative.me (Fear & Greed); Wikimedia (page views); Deribit (funding rates); Farside Investors (ETF flows, read once by hand).

---

## Appendix D: where everything lives, and how to re-run

**Project folder:** `D:\Desktop\TraderBOT` (a git repository, with every change and correction in its history).

| What | Where |
|---|---|
| This report | `results/FINAL_REPORT.md` |
| One-file handoff summary for a new Claude conversation | `CONTEXT_FOR_CLAUDE.md` |
| Audits | `results/independent_review.md`, `results/reference_verification.md` |
| Tables in Sections 3.1 to 3.4 | `risk_profile_report.txt` and `risk_profile.csv`; `vs_stock_fund_report.txt` and `crypto_vs_stocks.csv`; `mix_vs_savings_report.txt` and `mix_vs_savings.csv`; `possible_futures_report.txt` and `possible_futures.csv`; `long_history_report.txt` and `long_history.csv`; `event_study_report.txt`; `optimal_allocation_report.txt`. The first three tests' raw results are `start_date_sweep_12m.csv`, `start_date_sweep_24m.csv`, `honest_selection_12m.csv` and `dip_buying_12m.csv` |
| Sections 3.5 to 3.11 | `fundamentals_report.txt`, `ownership_concentration.md`, `developer_activity.csv`, `big_forces_report.txt`, `macro_and_maturity_report.txt`, `rolling_correlation_report.txt`, `valuation_report.txt`, `crowd_mood_report.txt`, `etf_flows_farside.md`, `leverage_and_flows_report.txt`, `trading_cost_report.txt`, `scenarios_report.txt`, `withdrawal_risk_report.txt` |
| Technical documents | `docs/trading-bot/` (architecture, data model, backtesting, safety, experiments) |

**To re-run a study:** `python scripts/<name>.py` (for example `python scripts/risk_profile.py`). The four main studies (`risk_profile`, `vs_stock_fund`, `mix_vs_savings`, `long_history`) were re-run for this report and reproduced their saved data files exactly.
**To run the automated checks:** `python -m unittest discover` runs the automated tests (the 318 written for the research engine and the read-only account reader all passed when this report was written), and `python -m crypto_research safety-check` runs the safety audit (it reported SAFE when this report was written).
