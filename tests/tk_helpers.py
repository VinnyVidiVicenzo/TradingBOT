"""Base classes for tests that need a real (never-shown) Tk root.

Tkinter objects (widgets and, above all, ``tk.Variable``) must be finalised on
the thread that owns the Tcl interpreter, while it is still alive. Left to the
garbage collector they can be finalised on a background worker thread instead,
where Tkinter waits a full second per object for a main loop that is not
running and then prints "main thread is not in main loop". A test that runs a
sweep on a worker thread would stall for seconds at a time. Tearing everything
down here, on the main thread and in the right order, avoids that.
"""

from __future__ import annotations

import gc
import tempfile
import tkinter as tk
import unittest
from pathlib import Path


class TkTestCase(unittest.TestCase):
    """Provides ``self.root``; skips itself when no display is available."""

    def make_root(self) -> tk.Tk:
        root = tk.Tk()
        root.withdraw()
        return root

    def setUp(self) -> None:
        try:
            self.root = self.make_root()
        except tk.TclError as exc:
            self.skipTest(f"no display available for Tk: {exc}")
        self.addCleanup(self._tear_down_tk)

    def _tear_down_tk(self) -> None:
        root = self.root
        for child in list(root.winfo_children()):
            child.destroy()
        # Drop every widget or variable the test kept, then collect while the
        # interpreter is still alive and we are on the main thread.
        for name, value in list(vars(self).items()):
            if value is not root and isinstance(value, (tk.Misc, tk.Variable)):
                delattr(self, name)
        gc.collect()
        root.destroy()
        del self.root
        gc.collect()


class AppTestCase(TkTestCase):
    """The real desktop_app.App, built over a throwaway repository folder."""

    def make_root(self) -> tk.Tk:
        from desktop_app.app import App

        self._repo = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo.cleanup)
        self.repo_root = Path(self._repo.name)
        self.app = App(repo_root=self.repo_root)
        self.app.root.withdraw()
        return self.app.root

    def setUp(self) -> None:
        super().setUp()
        # Runs before the base class's teardown (cleanups are last-in, first-out),
        # so the App's screens and variables are released while Tk is still alive.
        self.addCleanup(lambda: setattr(self, "app", None))


def show_offscreen(root: tk.Tk, width: int, height: int) -> None:
    """Map the window far off every screen, so geometry is really laid out.

    A withdrawn window never gets its children sized, but showing it for real
    would flash a window on whoever is running the tests.
    """
    root.geometry(f"{width}x{height}+6000+6000")
    root.deiconify()
    root.update()
    root.update()
