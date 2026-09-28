"""Launcher for the desktop app; the shortcut made by tools/make_shortcuts.ps1 runs this.

It is meant to be started by pythonw.exe, which the shortcut names explicitly,
so no console window appears. That also means stdout/stderr go nowhere -- see
desktop_app/logging_setup.py, which is why a crash here still ends up in
logs/desktop_app.log instead of disappearing silently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
