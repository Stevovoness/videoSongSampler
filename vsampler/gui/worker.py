"""Run slow jobs off the UI thread."""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    error = Signal(object, str)
    progress = Signal(float, str)


class Task(QRunnable):
    """Runs fn(*args, progress=..., cancel=...) if the function accepts them."""

    def __init__(self, fn: Callable, *args, with_progress: bool = False, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.with_progress = with_progress
        self.cancel = threading.Event()
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            kw = dict(self.kwargs)
            if self.with_progress:
                kw["progress"] = lambda f, m: self.signals.progress.emit(float(f), str(m))
                kw["cancel"] = self.cancel
            result = self.fn(*self.args, **kw)
        except BaseException as e:  # noqa: BLE001
            self.signals.error.emit(e, traceback.format_exc())
        else:
            self.signals.done.emit(result)


_alive: set[Task] = set()


def run_task(fn: Callable, *args, on_done: Callable[[Any], None] | None = None,
             on_error: Callable[[BaseException, str], None] | None = None,
             on_progress: Callable[[float, str], None] | None = None, **kwargs) -> Task:
    task = Task(fn, *args, with_progress=on_progress is not None, **kwargs)
    _alive.add(task)

    def finish(*_a):
        _alive.discard(task)

    if on_done:
        task.signals.done.connect(on_done)
    if on_error:
        task.signals.error.connect(on_error)
    if on_progress:
        task.signals.progress.connect(on_progress)
    task.signals.done.connect(finish)
    task.signals.error.connect(finish)
    QThreadPool.globalInstance().start(task)
    return task
