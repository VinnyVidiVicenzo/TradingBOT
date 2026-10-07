"""Binance.US's own public trade feed: the project's first live, free provider.

See ``adapter.py`` for what is fetched and how a trade is classified, and
``poller.py`` for how polling stays gap-free and honestly reports coverage.
"""

from .adapter import BinanceUSTradesAdapter, RawTrade
from .poller import PollReport, PollTarget, poll

__all__ = ["BinanceUSTradesAdapter", "PollReport", "PollTarget", "RawTrade", "poll"]
