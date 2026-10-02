"""Read-only piano roll that colours notes by whether a clip exists for them, with a drum lane underneath."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QWidget

from ...drums import drum_short
from ...models import NoteEvent
from ...notes import is_black, midi_to_name
from ...planner import Plan
from .. import theme


class _Canvas(QWidget):
    LEFT = 64

    def __init__(self):
        super().__init__()
        self.events: list[NoteEvent] = []
        self.plan: Plan | None = None
        self.px_per_sec = 80.0
        self.row_h = 9
        self.lo, self.hi = 60, 72
        self.drums: list[int] = []      # drum sounds used, one lane row each
        self.playhead: float | None = None
        self.fit_height = 260

    @property
    def n_note_rows(self) -> int:
        return self.hi - self.lo + 1 if any(not e.drum for e in self.events) else 0

    def set_events(self, events, plan) -> None:
        self.events, self.plan = events, plan
        notes = [e.pitch for e in events if not e.drum]
        if notes:
            self.lo, self.hi = min(notes) - 2, max(notes) + 2
        self.drums = sorted({e.pitch for e in events if e.drum}, reverse=True)
        dur = max((e.end for e in events), default=10.0)
        rows = self.n_note_rows + len(self.drums) + (1 if self.drums and notes else 0)
        rows = max(rows, 1)
        self.row_h = max(6, min(22, int(self.fit_height / rows)))
        self.setMinimumSize(int(self.LEFT + dur * self.px_per_sec + 40), rows * self.row_h + 4)
        self.update()

    def _color(self, e: NoteEvent) -> QColor:
        p = self.plan
        if p is None:
            return QColor(theme.BAD)
        if e.drum:
            return QColor(theme.OK if e.pitch in p.drum_exact else theme.WARN if e.pitch in p.drum_standin else theme.BAD)
        if e.pitch in p.exact:
            return QColor(theme.OK)
        if e.pitch in p.octave:
            return QColor(theme.OCTAVE)
        return QColor(theme.WARN if e.pitch in p.borrowed else theme.BAD)

    def _y(self, e: NoteEvent) -> int:
        if not e.drum:
            return (self.hi - e.pitch) * self.row_h
        gap = 1 if self.n_note_rows else 0
        return (self.n_note_rows + gap + self.drums.index(e.pitch)) * self.row_h

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#121317"))
        f = QFont(self.font())
        f.setPointSizeF(7)
        p.setFont(f)
        n_rows = self.n_note_rows
        for i in range(n_rows):
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
        # drum lane
        if self.drums:
            top = (n_rows + (1 if n_rows else 0)) * self.row_h
            p.fillRect(0, top, self.width(), len(self.drums) * self.row_h, QColor("#18161c"))
            if n_rows:
                p.setPen(QPen(QColor(theme.ACCENT), 1))
                p.drawLine(0, top - self.row_h // 2, self.width(), top - self.row_h // 2)
            p.setPen(QColor(theme.MUTED))
            for i, n in enumerate(self.drums):
                if self.row_h >= 8 or i % 2 == 0:
                    p.drawText(QRectF(2, top + i * self.row_h - 2, self.LEFT - 6, self.row_h + 4),
                               Qt.AlignRight | Qt.AlignVCenter, drum_short(n))
        total_h = self.height()
        # seconds grid
        p.setPen(QColor("#22242b"))
        dur = max((e.end for e in self.events), default=10.0)
        for s in range(0, int(dur) + 2):
            x = self.LEFT + s * self.px_per_sec
            p.drawLine(int(x), 0, int(x), total_h)
        p.setPen(QPen(QColor("#00000080"), 1))
        for e in self.events:
            p.setBrush(self._color(e))
            y = self._y(e)
            if e.drum:   # drum hits are drawn as short blocks: they play as one-shots
                r = QRectF(self.LEFT + e.start * self.px_per_sec - 2, y + 1, 5, self.row_h - 2)
            else:
                r = QRectF(self.LEFT + e.start * self.px_per_sec, y + 1, max(2.0, e.duration * self.px_per_sec - 1),
                           self.row_h - 2)
            p.drawRoundedRect(r, 2, 2)
        if self.playhead is not None:
            x = self.LEFT + self.playhead * self.px_per_sec
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawLine(int(x), 0, int(x), total_h)
        p.end()


class PianoRoll(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = _Canvas()
        self.setWidget(self.canvas)
        self.setWidgetResizable(False)
        self.setMinimumHeight(180)

    def set_events(self, events, plan) -> None:
        vp = self.viewport().size()
        self.canvas.fit_height = max(120, vp.height() - 20)
        self.canvas.set_events(events, plan)
        m = self.canvas.minimumSize()
        self.canvas.resize(max(m.width(), vp.width()), m.height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        c = self.canvas
        self.set_events(c.events, c.plan)

    def set_playhead(self, t: float | None) -> None:
        self.canvas.playhead = t
        self.canvas.update()
        if t is not None:
            x = int(_Canvas.LEFT + t * self.canvas.px_per_sec)
            self.ensureVisible(x, 0, 120, 0)

    def zoom(self, factor: float) -> None:
        self.canvas.px_per_sec = max(10.0, min(400.0, self.canvas.px_per_sec * factor))
        c = self.canvas
        self.set_events(c.events, c.plan)
