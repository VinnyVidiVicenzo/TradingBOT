"""Run a slow crypto_research call off the Tk thread, safely.

Tkinter widgets may only be touched from the thread running mainloop(). This
starts ``work()`` on a daemon thread and delivers its outcome back through
``widget.after()`` polling, so ``on_done`` always runs on the Tk thread and
can safely touch widgets.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class JobResult:
    ok: bool
    value: Any = None
    error: Exception | None = None


def run_in_background(
    widget: tk.Widget,
    work: Callable[[], Any],
    on_done: Callable[[JobResult], None],
    *,
    poll_ms: int = 100,
) -> Callable[[], None]:
    """Start ``work()`` on a daemon thread; deliver its outcome via ``on_done``.

    Returns a ``cancel()`` that stops delivery -- Python threads can't be
    killed, so the worker still finishes, but a cancelled job's result is
    dropped on arrival instead of reaching ``on_done``.
    """
    result_queue: "queue.Queue[JobResult]" = queue.Queue(maxsize=1)
    cancelled = False

    def _worker() -> None:
        try:
            result_queue.put(JobResult(ok=True, value=work()))
        except Exception as exc:  # noqa: BLE001 - delivered to on_done, never swallowed
            result_queue.put(JobResult(ok=False, error=exc))

    def _poll() -> None:
        if cancelled:
            return
        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            widget.after(poll_ms, _poll)
            return
        on_done(result)

    threading.Thread(target=_worker, daemon=True).start()
    widget.after(poll_ms, _poll)

    def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    return cancel
