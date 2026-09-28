"""Configures logs/desktop_app.log.

The only place a crash is visible when launched via run_desktop_app.pyw,
which runs under pythonw.exe and has no console for stdout/stderr to go to.
"""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(repo_root: Path | None = None) -> None:
    root = repo_root or Path(__file__).resolve().parent.parent
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "desktop_app.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
