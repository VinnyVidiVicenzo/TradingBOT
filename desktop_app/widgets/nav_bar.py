"""Persistent navigation between screens."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

from ..tooltip import attach_tooltip


class NavBar(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        items: Sequence[tuple[str, str]],
        on_select: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self._buttons: dict[str, ttk.Button] = {}
        for index, (key, label) in enumerate(items):
            button = ttk.Button(self, text=label, command=lambda k=key: on_select(k))
            if len(items) > 5:
                columns = 4 if len(items) > 6 else 3
                button.grid(row=index // columns, column=index % columns, sticky="ew", padx=4, pady=4)
                self.columnconfigure(index % columns, weight=1)
            else:
                button.pack(side="left", padx=4, pady=6)
            attach_tooltip(button, label.lower())
            self._buttons[key] = button

    def set_active(self, key: str) -> None:
        for candidate, button in self._buttons.items():
            button.state(["pressed"] if candidate == key else ["!pressed"])
