"""A Tkinter desktop GUI over crypto_research.

Reads and runs the research engine (crypto_research) and nothing else. This
package never imports account_tools and never collects a credential of any
kind -- see docs/trading-bot/SAFETY.md. crypto_research/safety.py audits this
package's imports on every `python -m crypto_research safety-check` run,
exactly as it does the engine itself.
"""

from __future__ import annotations

__version__ = "0.1.0"
