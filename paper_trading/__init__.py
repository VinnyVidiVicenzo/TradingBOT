"""Paper-trading rehearsal: watch a plan against live prices, with no money and no keys.

This zone reads public Binance.US market data and re-runs the research
backtester (``crypto_research.backtest``) with an end date that keeps moving
forward to the newest closed price. It contains no execution logic of its own:
every order it ever produces is a hypothetical ``SimulatedOrder`` from the same
engine a backtest uses, imported unchanged. See docs/trading-bot/SAFETY.md on
why paper trading is required to be a separate process rather than a mode the
research engine grows, and docs/trading-bot/PAPER_TRADING.md for how this one
works.

Nothing here can authenticate, place an order, or move money. It holds no
credentials and needs none: a rehearsal never touches a real account.
"""

from __future__ import annotations

__version__ = "1.0.0"
