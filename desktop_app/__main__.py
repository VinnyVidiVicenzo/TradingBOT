"""python -m desktop_app"""

from __future__ import annotations

import logging
import sys

from .logging_setup import configure_logging


def main() -> int:
    configure_logging()
    from .app import App  # deferred: logging must be configured first

    try:
        app = App()
    except Exception:
        logging.getLogger(__name__).exception("failed to start")
        raise
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
