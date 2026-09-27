"""Read-only authenticated access to one Binance.US account.

This is a SEPARATE SUBPROJECT from ``crypto_research`` on purpose.

``crypto_research`` cannot authenticate at all: it has no signing code, no
credential handling, and a transport restricted to public endpoints. That is
the guarantee the backtester rests on, and adding authentication to it would
dissolve it. So authentication lives here instead, behind a hard boundary:

* ``crypto_research`` never imports ``account_tools`` -- a test asserts it.
  The backtester therefore cannot reach live account state even by accident.
* ``account_tools`` issues GET only, against a whitelist of five read-only
  account endpoints. There is no order, cancel, withdraw, transfer or convert
  path, and a test asserts the whitelist contains none.
* Credentials are read from environment variables at call time and are never
  written to disk, never logged, and never placed in a config or a run record.

What this subproject is for: reading the two facts the research side cannot
derive from public data -- the commission rates that actually apply to the
account, and its current balances -- and printing them as configuration for a
human to review and paste. It emits text; it does not edit experiments.

See docs/trading-bot/SAFETY.md.
"""

__version__ = "0.1.0"
