# Bitcoin ETF money flows around the CLARITY Act failure

Source: Farside Investors (farside.co.uk/btc), read via a live browser on
2026-09-27 -- the site blocks plain automated requests, so this is a one-off
snapshot rather than a scripted download. Figures are US$ millions moved in (+)
or out (-) of US spot Bitcoin ETFs each trading day.

## Why this matters more than a sentiment score

Every other crowd-mood measure in this project (Fear & Greed, Wikipedia views)
describes how people *feel* or what they're *reading*. ETF flows are what large
investors actually *did* with real money that day. It is the closest thing to a
direct read of institutional behaviour that is free and public.

## The days either side of the failed Senate vote (15 Sep 2026)

| Date | Total flow (US$m) | Note |
|---|---|---|
| 08 Sep | -46.6 | |
| 09 Sep | -120.2 | |
| 10 Sep | -282.7 | |
| 11 Sep | -13.2 | |
| 14 Sep | +159.9 | |
| **15 Sep** | **-450.4** | **CLARITY Act cloture vote fails, 49-50** |
| 16 Sep | -295.9 | outflow continues |
| 17 Sep | +159.5 | |
| 18 Sep | +433.0 | |
| **21 Sep** | **+999.0** | |
| 22 Sep | +714.7 | |
| 23 Sep | +346.9 | |
| 24 Sep | +190.7 | |
| 25 Sep | +134.5 | |

Lifetime stats (since the ETFs launched, Jan 2024): total inflow **$65.3bn**,
average day **+$96.1m**, biggest single day **+$1,119.9m**, worst single day
**-$1,113.7m**.

## Reading it against the price-based event study

The earlier event study (`results/event_study.py`) found the basket fell 4.4%
on the day the bill failed, then rose 16.9% over the following week. This table
shows real money doing the same thing: **-$450m and -$296m out on the two days
around the failure, then inflows on each of the next seven trading days (17-25
Sep), from about $135m to $999m a day.** (Outflows had also been running for
a week before the vote: -$463m over 8-11 Sep.) That is not a mood score -- it is what large investors actually
bought and sold, and it moved in the same direction and on the same timeline as
the price recovery.

One caution: 14 trading days is a single episode, not a pattern proven across
many votes. It corroborates the specific CLARITY Act finding; it does not
generalise to "outflows always reverse this fast."

## What this cannot tell us

- No history before this snapshot without either a paid feed or repeated manual
  reads of the site (each visit only shows the trailing window plus lifetime
  totals).
- Flows are net of the whole ETF complex; a headline outflow can hide one large
  investor selling while many small ones bought, or vice versa.
