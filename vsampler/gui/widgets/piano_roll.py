"""Read-only piano roll that colours notes by whether a clip exists for them."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QWidget

from ...models import NoteEvent
from ...notes import is_black, midi_to_name
from .. import theme


class _Canvas(QWidget):
    LEFT = 44

    def __init__(self):
        super().__init__()
        self.events: list[NoteEvent] = []
        self.exact: set[int] = set()
        self.borrowed: set[int] = set()
        self.px_per_sec = 80.0
        self.row_h = 9
        self.lo, self.hi = 60, 72
        self.playhead: float | None = None
        self.fit_height = 260

    def set_events(self, events, exact, borrowed) -> None:
        self.events, self.exact, self.borrowed = events, exact, borrowed
        if events:
            self.lo = min(e.pitch for e in events) - 2
            self.hi = max(e.pitch for e in events) + 2
        dur = max((e.end for e in events), default=10.0)
        rows = self.hi - self.lo + 1
        self.row_h = max(6, min(22, int(self.fit_height / max(1, rows))))
        self.setMinimumSize(int(self.LEFT + dur * self.px_per_sec + 40), rows * self.row_h + 4)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#121317"))
        rows = self.hi - self.lo + 1
        f = QFont(self.font())
        f.setPointSizeF(7)
        p.setFont(f)
        for i in range(rows):
            m = self.hi - i
            y = i * self.row_h
            if is_black(m):
                p.fillRect(self.LEFT, y, self.width(), self.row_h, QColor("#16171c"))
            if m % 12 == 0:
                p.setPen(QColor("#2c2f38"))
                p.drawLine(self.LEFT, y + self.row_h, self.width(), y + self.row_h)
            if m % 12 == 0 or self.row_h >= 12:
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(2, y - 2, self.LEFT - 6, self.row_h + 4), Qt.AlignRight | Qt.AlignVCenter, midi_to_name(m))
        # seconds grid
        p.setPen(QColor("#22242b"))
        dur = max((e.end for e in self.events), default=10.0)
        for s in range(0, int(dur) + 2):
            x = self.LEFT + s * self.px_per_sec
            p.drawLine(int(x), 0, int(x), rows * self.row_h)
        good, warn, bad = QColor(theme.OK), QColor(theme.WARN), QColor(theme.BAD)
        p.setPen(QPen(QColor("#00000080"), 1))
        for e in self.events:
            c = good if e.pitch in self.exact else warn if e.pitch in self.borrowed else bad
            y = (self.hi - e.pitch) * self.row_h
            r = QRectF(self.LEFT + e.start * self.px_per_sec, y + 1, max(2.0, e.duration * self.px_per_sec - 1), self.row_h - 2)
            p.setBrush(c)
            p.drawRoundedRect(r, 2, 2)
        if self.playhead is not None:
            x = self.LEFT + self.playhead * self.px_per_sec
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawLine(int(x), 0, int(x), self.height())
        p.end()


class PianoRoll(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = _Canvas()
        self.setWidget(self.canvas)
        self.setWidgetResizable(False)
        self.setMinimumHeight(180)

    def set_events(self, events, exact, borrowed) -> None:
        vp = self.viewport().size()
        self.canvas.fit_height = max(120, vp.height() - 20)
        self.canvas.set_events(events, exact, borrowed)
        m = self.canvas.minimumSize()
        self.canvas.resize(max(m.width(), vp.width()), m.height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        c = self.canvas
        self.set_events(c.events, c.exact, c.borrowed)

    def set_playhead(self, t: float | None) -> None:
        self.canvas.playhead = t
        self.canvas.update()
        if t is not None:
            x = int(_Canvas.LEFT + t * self.canvas.px_per_sec)
            self.ensureVisible(x, 0, 120, 0)

    def zoom(self, factor: float) -> None:
        self.canvas.px_per_sec = max(10.0, min(400.0, self.canvas.px_per_sec * factor))
        c = self.canvas
        self.set_events(c.events, c.exact, c.borrowed)
