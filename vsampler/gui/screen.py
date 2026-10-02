"""Keep every window on screen.

`ScreenGuard` is installed on the application: whenever any top-level window (main window, dialogs, message
boxes) is shown or resized, it is shrunk to fit the screen's available area (excluding the taskbar) and moved
fully inside it. Windows whose content could need more room than a small screen has wrap that content in
`scrollable()`, so shrinking them never hides anything: scrollbars appear instead.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QSize, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

MARGIN = 8


def available(widget: QWidget | None = None) -> QRect:
    screen = None
    if widget is not None:
        screen = widget.screen() if widget.isVisible() else None
        if screen is None and widget.parentWidget() is not None:
            screen = widget.parentWidget().window().screen()
    screen = screen or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def preferred_size(widget: QWidget, w: int, h: int, frac: float = 0.92) -> QSize:
    """A starting size of about w x h, but never more than `frac` of the screen."""
    a = available(widget)
    return QSize(min(w, int(a.width() * frac)), min(h, int(a.height() * frac)))


def scrollable(content: QWidget) -> QScrollArea:
    """Wrap content so its window can shrink below the content's minimum size (scrollbars appear)."""
    area = QScrollArea()
    area.setWidget(content)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    return area


def fit_on_screen(win: QWidget) -> None:
    """Shrink and move a top-level window so the whole frame (title bar included) is inside the screen."""
    if not win.isWindow() or not win.isVisible() or win.isMaximized() or win.isFullScreen():
        return
    a = available(win).adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
    frame = win.frameGeometry()
    extra_w = frame.width() - win.width()          # window borders
    extra_h = frame.height() - win.height()        # title bar + borders
    max_w, max_h = a.width() - extra_w, a.height() - extra_h
    # a window can't be resized below its minimum size, which Qt derives from the layout: cap that first
    if win.minimumWidth() > max_w or win.minimumHeight() > max_h:
        win.setMinimumSize(min(win.minimumWidth(), max_w), min(win.minimumHeight(), max_h))
    if win.width() > max_w or win.height() > max_h:
        win.resize(min(win.width(), max_w), min(win.height(), max_h))
        frame = win.frameGeometry()
    x = min(max(frame.left(), a.left()), a.right() - frame.width() + 1)
    y = min(max(frame.top(), a.top()), a.bottom() - frame.height() + 1)
    if (x, y) != (frame.left(), frame.top()):
        win.move(x, y)


class ScreenGuard(QObject):
    """Application-wide event filter that calls `fit_on_screen` for every window shown or resized."""

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t in (QEvent.Show, QEvent.Resize) and isinstance(obj, QWidget) and obj.isWindow():
            # after Qt has finished placing the window (and the title bar size is known)
            QTimer.singleShot(0, lambda w=obj: _safe_fit(w))
        return False


def _safe_fit(w: QWidget) -> None:
    try:
        fit_on_screen(w)
    except RuntimeError:   # window already deleted
        pass


def install(app) -> ScreenGuard:
    guard = ScreenGuard(app)
    app.installEventFilter(guard)
    return guard
