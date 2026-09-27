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

## What was not re-checked

Every other date in this project came either from a live API response (prices,
on-chain metrics, exchange rules -- all timestamped by the source itself, not
typed from memory) or was already sourced with a citation at the time it was
first stated (the CLARITY Act's own timeline, the GENIUS Act, the Farside ETF
flow dates). Those were not re-verified here because they were never
"recalled" claims in the first place -- the risk this pass was built to catch.
