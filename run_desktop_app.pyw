"""Double-click launcher.

Associated with pythonw.exe on Windows, so no console window appears. That
also means stdout/stderr go nowhere -- see desktop_app/logging_setup.py,
which is why a crash here still ends up in logs/desktop_app.log instead of
disappearing silently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
