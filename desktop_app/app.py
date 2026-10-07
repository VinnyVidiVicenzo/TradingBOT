"""Owns the Tk root window and switches between screens."""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import alternatives, plan_history
from .widgets.nav_bar import NavBar

logger = logging.getLogger(__name__)

ICON_PATH = Path(__file__).resolve().parent / "assets" / "app.ico"

# Wide enough for the results table and the map of starting months side by side.
MIN_WIDTH = 980
MIN_HEIGHT = 660

SCREENS: tuple[tuple[str, str], ...] = (
    ("home", "Home"),
    ("findings", "What we've found so far"),
    ("try_a_plan", "Try your own plan"),
    ("past_plans", "Your past plans"),
    ("safety_check", "Safety check"),
)


class App:
    def __init__(self, *, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or _default_repo_root()
        self.db_path = str(self.repo_root / "data" / "market.sqlite")
        self.plan_history_path = self.repo_root / "experiments" / plan_history.FILENAME
        self._reference: alternatives.ReferenceData | None = None
        self._reference_loaded = False
        self._reference_stamp = None

        self.root = tk.Tk()
        self.root.title("Crypto plan tester -- practice only, never real money")
        self._set_icon()
        self._size_window()
        self.root.report_callback_exception = self._on_callback_exception

        self.nav = NavBar(self.root, SCREENS, self.show)
        self.nav.pack(side="top", fill="x")

        self._container = ttk.Frame(self.root)
        self._container.pack(side="top", fill="both", expand=True, padx=18, pady=14)
        self._container.grid_rowconfigure(0, weight=1)
        self._container.grid_columnconfigure(0, weight=1)

        self._screens: dict[str, ttk.Frame] = {}
        self._build_screens()
        self.show("home")

    @property
    def reference(self) -> alternatives.ReferenceData | None:
        """Stock-fund and savings history for the comparison, or None if it isn't saved."""
        directory = self.repo_root / "data" / "reference"
        stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                      for p in (directory / alternatives.FUND_FILE, directory / alternatives.RATE_FILE))
        if not self._reference_loaded or stamp != self._reference_stamp:
            self._reference = alternatives.load_reference(self.repo_root / "data" / "reference")
            self._reference_loaded = True
            self._reference_stamp = stamp
        return self._reference

    def _set_icon(self) -> None:
        # Only Windows reads .ico files here; anywhere else Tk keeps its own icon.
        try:
            self.root.iconbitmap(default=str(ICON_PATH))
        except tk.TclError:
            logger.info("window icon not applied (%s)", ICON_PATH)

    def _size_window(self) -> None:
        """Open at a comfortable size that still fits a small screen.

        The minimum is wide enough that the map of starting months is never cut
        off; on a screen smaller than the minimum it gives way rather than
        forcing the window off the edge.
        """
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(MIN_WIDTH + 40, screen_w - 80)
        height = min(740, screen_h - 120)
        self.root.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}")
        self.root.minsize(min(MIN_WIDTH, width), min(MIN_HEIGHT, height))

    def _build_screens(self) -> None:
        from .screens import findings, home, past_plans, safety_check, try_a_plan

        factories = {
            "home": home.HomeScreen,
            "findings": findings.FindingsScreen,
            "try_a_plan": try_a_plan.TryAPlanScreen,
            "past_plans": past_plans.PastPlansScreen,
            "safety_check": safety_check.SafetyCheckScreen,
        }
        for key, factory in factories.items():
            screen = factory(self._container, self)
            screen.grid(row=0, column=0, sticky="nsew")
            self._screens[key] = screen

    def show(self, key: str) -> None:
        self._screens[key].tkraise()
        self.nav.set_active(key)
        on_show = getattr(self._screens[key], "on_show", None)
        if on_show is not None:
            on_show()

    def run(self) -> None:
        self.root.mainloop()

    def _on_callback_exception(
        self, exc: type[BaseException], val: BaseException, tb: object
    ) -> None:
        # Tk's default behaviour for an exception raised inside a widget
        # callback is to print it to stderr and otherwise swallow it -- it
        # neither crashes nor propagates. Wrapping mainloop() in try/except
        # alone would catch nothing from inside a button handler; this
        # override is the only hook that sees it.
        logger.error("unhandled error in a UI callback", exc_info=(exc, val, tb))
        messagebox.showerror(
            "Something went wrong",
            "Something unexpected went wrong. Nothing was bought or sold -- this "
            "tool only ever does practice math. The details were written to "
            "logs/desktop_app.log.",
        )


def _default_repo_root() -> Path:
    source_root = Path(__file__).resolve().parent.parent
    return source_root if (source_root / "README.md").is_file() else Path.cwd()
