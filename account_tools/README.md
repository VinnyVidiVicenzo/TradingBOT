# account_tools

Read-only Binance.US account reader. A **separate subproject** from
`crypto_research`, which cannot authenticate at all.

Its entire job: read the two facts the research side cannot derive from public
data — your real commission rates and your current balances — and print them as
configuration for you to review and paste.

It issues `GET` only, against five read-only endpoints. There is no order,
cancel, withdraw, transfer or convert path in it. See
[../docs/trading-bot/SAFETY.md](../docs/trading-bot/SAFETY.md).

## 1. Create a read-only key

On the Binance.US API management page:

| Setting | Value |
| --- | --- |
| Enable Reading | **on** (all this tool needs) |
| Enable Spot & Margin Trading | **off** |
| Enable Withdrawals | **off** |
| IP access restriction | restrict to your IP if you can |

This tool **cannot verify** those settings — Binance.US exposes no endpoint
reporting a key's own permissions. Confirm them yourself on that page.

## 2. Set it in your shell

Never in a file in this repository.

PowerShell:

```powershell
$env:BINANCEUS_READ_API_KEY = "<paste key>"
$env:BINANCEUS_READ_API_SECRET = "<paste secret>"
```

bash:

```bash
export BINANCEUS_READ_API_KEY="<paste key>"
export BINANCEUS_READ_API_SECRET="<paste secret>"
```

## 3. Check it works

```bash
python -m account_tools check
```

Reports account flags and non-zero balances, and states plainly that the flags
describe the account rather than the key.

## 4. Read your real fee rates

```bash
python -m account_tools fees --symbols BTCUSD,ETHUSD,SOLUSD --window "2025-03 to 2026-09"
```

Prints a `[costs.fees]` block with `source = "MEASURED -- ..."` and the
observation timestamp. Symbols whose rates differ from the account-wide figure
are pinned individually under `[costs.fees.per_symbol.<SYMBOL>]`.

Paste it over the placeholder block in each config in `../configs/`.

**The rate you read today is not necessarily the rate that applied across your
backtest window.** Fee tiers move with 30-day volume and promotions end. The
generated note says so; keep it.

## 5. Read your balances

```bash
python -m account_tools holdings
```

Prints a `[portfolio]` block that splits cash from positions, because the
research side treats them completely differently: cash is spendable, a holding
never is. `USD` and `USDT` stay separate, and if a stablecoin balance is present
the output reminds you it is not dollars.

No cost basis is emitted. The venue does not report one and this tool will not
invent it — add `cost_basis_per_unit` and `cost_basis_asset` yourself only if
you actually know them.

## 6. Delete the key

The workflow needs it once, for a few seconds. Deleting it afterwards removes
the residual risk entirely; the measured rates stay in your configs.

## Commands

| | |
| --- | --- |
| `check` | Key works, account flags, balances |
| `fees` | `[costs.fees]` block with real rates |
| `holdings` | `[portfolio]` block from balances |
| `balances` | Non-zero balances (`--json` for machine output) |

`--write <path>` on `fees` and `holdings` also saves the block to a file.
`--recv-window-ms` raises the signed-request validity window if your clock is
skewed (max 60000).

## Troubleshooting

| Error | Cause |
| --- | --- |
| `-1022` / signature | Secret pasted with a trailing newline or quotes |
| `-1021` / timestamp | System clock off; sync it or raise `--recv-window-ms` |
| `-2014` / `-2015` | Wrong key, or an IP allowlist excluding this machine |
| `401` on a status endpoint | Expected for a read-only key; `fees` and `holdings` still work |
