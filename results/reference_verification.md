# Reference verification

Every date `event_study.py` originally entered from memory (marked `RECALLED`)
was checked against sources by web search, plus two other significant factual
claims stated in conversation but never explicitly sourced at the time. This
is a different exercise from `independent_review.md`, which audited our own
code and arithmetic; this one checks claims about the outside world.

## What was checked, and the result

| Claim as originally stated | Verified date/fact | Source | Verdict |
|---|---|---|---|
| China mining crackdown, 2021-05-19 | State Council announcement **2021-05-21** | [Decrypt](https://decrypt.co/74187/chinas-2021-bitcoin-crackdown-what-you-need-know), [Fortune](https://fortune.com/2021/06/22/china-bitcoin-mining-ban-sichuan-hydropower) | **Wrong by 2 days -- corrected** |
| China bans all crypto transactions, 2021-09-24 | PBOC "all transactions illegal" declaration, **2021-09-24** | [Baker McKenzie](https://blockchain.bakermckenzie.com/2021/10/04/chinas-central-bank-declares-all-cryptocurrency-transactions-illegal/), [Lexology](https://www.lexology.com/library/detail.aspx?g=b7b49993-fae3-4787-9d9c-ebad3966298b) | Confirmed correct |
| Terra/LUNA collapse begins, 2022-05-09 | UST first depegged the evening of **2022-05-07** | [Harvard Law / CFI](https://corpgov.law.harvard.edu/2023/05/22/anatomy-of-a-run-the-terra-luna-crash/), [MIT Sloan](https://mitsloan.mit.edu/cfi/anatomy-a-run-terra-luna-crash) | **Wrong by 2 days -- corrected** |
| FTX collapse, 2022-11-08 | Withdrawals halted **2022-11-08** (bankruptcy filed 2022-11-11) | [CoinDesk](https://www.coindesk.com/policy/2022/11/11/ftx-files-for-bankruptcy-protections-in-us), [Wikipedia](https://en.wikipedia.org/wiki/Bankruptcy_of_FTX) | Confirmed correct |
| SEC sues Binance and Coinbase, 2023-06-05 | Binance sued **2023-06-05**; Coinbase sued 2023-06-06 | [Goodwin Law](https://www.goodwinlaw.com/en/insights/publications/2023/06/alerts-otherindustries-ftec-sec-sues-binance-and-coinbase), [CNBC](https://www.cnbc.com/2023/06/12/coinbase-sued-by-sec-over-alleged-unregistered-securities.html) | Confirmed correct (dated to the first filing) |
| Spot Bitcoin ETFs approved, 2024-01-10 | SEC approval **2024-01-10**; trading began 2024-01-11 | [40 Act Blog](https://40actblog.sewkis.com/blog/sec-approves-first-round-of-bitcoin-etfs), [Congress.gov](https://www.congress.gov/crs-product/IF12573) | Confirmed correct (dated to approval, not first trade) |
| Bitcoin halving, 2024-04-19 | Block 840,000 -- sources split between Apr 19 (US evening) and Apr 20 (UTC morning); one outlier source claims Apr 22, inconsistent with every other source and the block-840,000 milestone article's own dating, so treated as an error and discarded | [Bitcoin.com News](https://news.bitcoin.com/halving-milestone-block-840000-mined-ushering-in-a-new-chapter-as-bitcoin-miners-face-shrinking-rewards/), [CoinGecko](https://www.coingecko.com/en/coins/bitcoin/bitcoin-halving) | **Ambiguous -- resolved to 2024-04-20 UTC**, matching this project's own UTC convention rather than the US-timezone date |
| US presidential election, 2024-11-05 | **2024-11-05** | Undisputed public record; not separately searched | Confirmed correct |
| Strategic Bitcoin Reserve order, 2025-03-06 | Signed **2025-03-06** | [CNBC](https://www.cnbc.com/2025/03/06/trump-signs-executive-order-for-us-strategic-bitcoin-reserve.html), [White House](https://www.whitehouse.gov/fact-sheets/2025/03/fact-sheet-president-donald-j-trump-establishes-the-strategic-bitcoin-reserve-and-u-s-digital-asset-stockpile/), [Federal Register](https://www.federalregister.gov/documents/2025/03/11/2025-03992/establishment-of-the-strategic-bitcoin-reserve-and-united-states-digital-asset-stockpile) | Confirmed correct |
| Bybit hack, "~$1.5bn, Feb 2025" (stated in conversation, not code) | **2025-02-21**, $1.46-1.5bn (FBI's own figure: $1.5bn) | [FBI](https://www.fbi.gov/investigate/cyber/alerts/2025/north-korea-responsible-for-1-5-billion-bybit-hack), [Elliptic](https://www.elliptic.co/blog/bybit-hack-largest-in-history) | Confirmed correct |
| Binance $4.3bn settlement, "2023" (stated in conversation, not code) | **2023-11-21** | [DOJ](https://www.justice.gov/archives/opa/pr/binance-and-ceo-plead-guilty-federal-charges-4b-resolution), [US Treasury](https://home.treasury.gov/news/press-releases/jy1925) | Confirmed correct |

**Score: 2 of 11 checked claims were actually wrong (not just "might be off" --
genuinely wrong), 1 was a real ambiguity requiring a judgment call, 8 were
already accurate.**

## Why the two errors mattered more than "a couple of days"

Both wrong dates happened to land on days with visibly different price moves
than the correct dates -- these are volatile, fast-moving episodes, so a
2-day slip is not a rounding error the way it might be for a slower-moving
event:

- **China mining crackdown**: the wrong date's single-day move was -26.6%
  (the largest in the whole 15-event dataset). The correct date's move is
  -11.0%. The old date happened to fall in the middle of the broader
  Sichuan-ban cascade a few weeks later, not the original announcement.
- **Terra/LUNA**: the wrong date showed a -13.5% single-day drop with
  -7.5%/-19.6% over the next 7/30 days. The correct (earlier) start date
  shows a smaller -2.4% initial move but -23.4%/-29.8% over the following
  7/30 days -- because the real crash happened mostly AFTER May 7, not on
  it, and the wrong date had already absorbed some of that decline into its
  own "day" figure before the forward window started counting.

Because of this, one aggregate conclusion in `CONTEXT_FOR_CLAUDE.md` actually
changed, not just its footnote: "bad news hits hard and then mostly recovers
within a month" is weaker than first stated. With the corrected dates, half
the bad-news events (3 of 6) are still down a month later, not 2 of 6. See the
Test 5 section of that document for the corrected numbers and full context.

## A second bug this exposed

`event_study.py` had a print statement hard-coded to name "the 2021 China
crash" as the biggest single event-day move in the dataset -- true when it was
written (-26.6% was clearly the largest), but the code never re-derived that
claim from the actual data. Once the date correction shrank China's move to
-11.0%, the label kept citing China even though FTX's -14.3% was now larger.
Fixed to identify the actual largest mover from the data on every run, rather
than asserting a fixed answer.

## Addendum: sources checked for the leverage study (`leverage_and_flows.py`)

| Claim | Verified | Source |
|---|---|---|
| May 19-23 2021 crash: "~$8bn liquidated, largest on record at the time" | **$8.6bn**, precise figure; genuinely the largest single-day liquidation on record *at that time* -- since surpassed (see below) | [KuCoin, top 10 largest liquidation events](https://www.kucoin.com/blog/top-10-largest-crypto-liquidation-events-of-all-time) (states $8.6B for 19 May 2021 and about $19.3B for 10-11 Oct 2025) |
| A previously-unknown-to-me October 2025 event, surfaced only because I checked the May 2021 figure and the search results mentioned it had since been surpassed | **2025-10-10/11: $19bn liquidated, 1.6m accounts, ~9x the Feb 2025 crash, ~19x FTX** -- triggered by Trump's 100% China tariff announcement, a macro/geopolitical event with nothing to do with crypto itself | [CoinDesk](https://www.coindesk.com/research/market-spotlight-the-19-billion-liquidation-that-shook-crypto), [Forbes](https://www.forbes.com/sites/digital-assets/2025/10/13/cryptos-black-friday-inside-the-19-billion-market-meltdown/), [CCN](https://www.ccn.com/education/crypto/cryptos-19-billion-liquidation-explained-trump-china-tariff-leverage-crash/), [FTI Consulting](https://www.fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity), [CoinGecko](https://www.coingecko.com/learn/october-10-crypto-crash-explained) |
| Whether that October 2025 event actually shows up in OUR OWN Binance.US data, on the reported dates, at roughly the reported size | **Independently confirmed**: our own BTCUSDT closes fell from $124,798 (Oct 6) to a low of $109,278 (Oct 11), an ~12.5% close-to-low decline -- consistent with the ~14% intraday figure widely reported (some difference expected: one is a close-to-low measure on one exchange, the other an intraday peak-to-trough figure quoted across the broader market) | Own data, `data/market.sqlite`, cross-checked in `scripts/leverage_and_flows.py` |

This is a good example of why checking one claim is worth doing even when it
seems settled: verifying the smaller, older number (May 2021) is what surfaced
the far larger, more recent, and more directly relevant event (October 2025)
that would otherwise have been missing from this research entirely.

## What was not re-checked

Every other date in this project came either from a live API response (prices,
on-chain metrics, exchange rules -- all timestamped by the source itself, not
typed from memory) or was already sourced with a citation at the time it was
first stated (the CLARITY Act's own timeline, the GENIUS Act, the Farside ETF
flow dates). Those were not re-verified here because they were never
"recalled" claims in the first place -- the risk this pass was built to catch.

## Addendum: claims checked while writing the final report

These were repeated in the docs and in conversation but had never been sourced.

| Claim | Result | Sources |
|---|---|---|
| XRP was delisted from Binance.US on 13 Jan 2021 and returned (XRP/USDT) on 14 Jul 2023: a 912-day gap in our data | **Confirmed.** Our own `XRPUSDT` data has its only long gap between 2021-01-13 and 2023-07-14 (912 days). | [Crypto Briefing](https://cryptobriefing.com/binance-delists-xrp-trading-ripple-slawsuit/), [AMBCrypto](https://ambcrypto.com/xrp-will-be-delisted-from-binance-us-as-well/), [U.Today](https://u.today/xrp-relisted-on-binance-us-details) |
| Binance.US wound down dollar trading in 2023 and restored it on 19 Feb 2025 | **Confirmed, with detail.** USD deposits were suspended in June 2023; most USD Advanced Trading pairs were removed on 26 Jun 2023 (BTC, ETH, ADA, BNB, LTC, MATIC, SOL, VET, USDC and USDT pairs were kept for the time being); direct dollar withdrawals had halted by Oct 2023; dollar services returned on 19 Feb 2025. | [Cointelegraph](https://cointelegraph.com/news/binance-us-suspends-usd-deposits-warns-withdrawal-pause-incoming), [Fox Business](https://www.foxbusiness.com/markets/binance-us-suspends-usd-deposits-after-regulators-crackdown), [Binance.US support notice](https://support.binance.us/en/articles/9843320-binance-us-provides-update-on-usd-withdrawals-trading-offerings), [CoinDesk](https://www.coindesk.com/policy/2023/10/17/binanceus-halts-direct-dollar-withdrawals), [Binance.US blog](https://blog.binance.us/usd-services-return/) |
| The "586-day hole" in dollar-pair prices (14 Jul 2023 to 19 Feb 2025) | **Re-checked against the live Binance.US price feed on 27 Sep 2026.** Last daily price before the hole: 14 Jul 2023 for BTCUSD, ETHUSD, SOLUSD, ADAUSD, BNBUSD, LTCUSD, VETUSD and MATICUSD; 27 Jun 2023 for DOGEUSD, XLMUSD and HBARUSD. First price after: 19 Feb 2025 (20 Feb for LTCUSD and VETUSD; MATICUSD and XRPUSD had no prices in the 15 Jan to 5 Mar 2025 window checked). The 586 days is the span between the last and next price, so 585 days of prices are missing. **Not sourced:** why the major pairs stopped specifically after 14 Jul 2023. | Binance.US public price feed (own check) |
| Solana rose "124-fold in one year" | **Confirmed from our own data**: a one-year hold starting 1 Nov 2020 returned 124.5x (the best any-day start, 4 Nov 2020, was 178x). | `data/market.sqlite` |
| BNB network usage: 16.4m transactions/day, +24% in a year, +362% in 3 years; new addresses/day -14% in a year | **Recomputed exactly** from the saved BscScan files (90-day averages; new addresses 650,483/day vs 760,491 a year earlier). | `data/reference/fundamentals/bscscan_*.csv` |
| Crypto is treated as property for US tax; selling or swapping is a taxable event; held one year or less is short-term, more than one year long-term | **Confirmed.** | [IRS: digital assets](https://www.irs.gov/filing/digital-assets) |
| The tax "wash sale" rule does not apply to crypto | **Confirmed in two tax-firm articles** (IRC section 1091 covers stocks and securities; crypto is property; several proposals exist, none had passed). A CNBC headline of 28 Jul 2026 says Congress is renewing the push to close it; the article itself could not be opened, so its details and anything more recent were **not** confirmed. A specific bill number seen in a search snippet was dropped because no readable source confirmed it. | [Gordon Law Group, 15 May 2026](https://gordonlaw.com/learn/crypto-wash-sale-rule-in-2026-is-the-loophole-finally-closed/), [ChainWise CPA, 8 Mar 2026](https://chainwisecpa.com/crypto-wash-sale-2026/), [CNBC headline](https://www.cnbc.com/2026/07/28/congress-renews-push-to-end-crypto-wash-sale-tax-loophole.html) |
| The stock fund rose "about 2.5x" over 2020-26 | **Confirmed**: adjusted close (dividends included) $310.86 on 2020-10-01 to $771.35 on 2026-09-25 = 2.48x. | `data/reference/spy_adjusted.csv` |

One correction to the table above: of the 11 claims in the first table, the 5 Nov 2024
US election date was recorded as "confirmed" but was **not actually searched**
(treated as undisputed public record), so 7 of the 8 "accurate" verdicts were sourced.
