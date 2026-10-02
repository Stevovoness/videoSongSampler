"""Zoomable timeline of a long video's sound with editable clip blocks (used by the clip finder).

Shows the waveform, the heard pitch curve, and one block per suggested clip point. Drag a block's edges to
resize it, drag its middle to move it, drag on empty space to draw a new clip, double-click to play it,
press Delete to remove it. Mouse wheel zooms, Shift+wheel (or the scrollbar) scrolls.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QScrollBar, QWidget

from ...drums import drum_short
from ...notes import midi_to_name
from .. import theme

LEFT = 40          # pitch axis labels
LANE = 34          # block lane height
BAR = 14           # scrollbar height
EDGE = 6           # px from a block edge that grabs it for resizing
MIN_LEN = 0.05


class Timeline(QWidget):
    selected = Signal(int)              # index into items, -1 = none
    changed = Signal(int)               # an item was moved / resized (live while dragging)
    edited = Signal(int)                # drag finished
    created = Signal(float, float)      # new clip drawn (start, end)
    deleted = Signal(int)
    play_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.peaks = np.zeros(1, np.float32)
        self.times = np.zeros(0)
        self.midi = np.zeros(0)
        self.items: list = []           # objects with start, end, kind, midi
        self.visible: set[int] = set()  # indexes shown (sensitivity filter)
        self.ticked: set[int] = set()
        self.labels: dict[int, str] = {}
        self.sel = -1
        self.playhead: float | None = None
        self.t0, self.span = 0.0, 10.0
        self.lo, self.hi = 48, 84
        self._drag = None               # (mode, index, t_at_press, start0, end0)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(150)
        self.bar = QScrollBar(Qt.Horizontal, self)
        self.bar.valueChanged.connect(self._scrolled)

    # ------------------------------------------------------------------ data
    def set_data(self, duration: float, peaks: np.ndarray, times: np.ndarray, midi: np.ndarray) -> None:
        self.duration = max(0.1, duration)
        self.peaks, self.times, self.midi = peaks, times, midi
        finite = midi[np.isfinite(midi)] if len(midi) else midi
        if finite.size:
            self.lo = int(math.floor(np.percentile(finite, 1))) - 2
            self.hi = int(math.ceil(np.percentile(finite, 99))) + 2
        self.span = min(self.duration, max(5.0, self.span))
        self.t0 = 0.0
        self._sync_bar()
        self.update()

    def set_items(self, items: list, visible: set[int], ticked: set[int], labels: dict[int, str]) -> None:
        self.items, self.visible, self.ticked, self.labels = items, visible, ticked, labels
        self.update()

    def select(self, i: int, scroll: bool = True) -> None:
        self.sel = i
        if scroll and 0 <= i < len(self.items):
            it = self.items[i]
            if it.start < self.t0 or it.end > self.t0 + self.span:
                self.t0 = max(0.0, min(self.duration - self.span, (it.start + it.end) / 2 - self.span / 2))
                self._sync_bar()
        self.update()

    def set_playhead(self, t: float | None) -> None:
        self.playhead = t
        if t is not None and not (self.t0 <= t <= self.t0 + self.span):
            self.t0 = max(0.0, min(self.duration - self.span, t - self.span * 0.1))
            self._sync_bar()
        self.update()

    # ------------------------------------------------------------------ geometry
    def _w(self) -> float:
        return max(1.0, self.width() - LEFT)

    def x_of(self, t: float) -> float:
        return LEFT + (t - self.t0) / self.span * self._w()

    def t_of(self, x: float) -> float:
        return self.t0 + (x - LEFT) / self._w() * self.span

    def _wave_rect(self) -> QRectF:
        return QRectF(LEFT, 4, self._w(), self.height() - LANE - BAR - 8)

    def _lane_rect(self) -> QRectF:
        return QRectF(LEFT, self.height() - LANE - BAR - 2, self._w(), LANE - 4)

    def _y_of_midi(self, m: float) -> float:
        r = self._wave_rect()
        return r.bottom() - (m - self.lo) / max(1, self.hi - self.lo) * r.height()

    def _sync_bar(self) -> None:
        steps = 1000
        self.bar.blockSignals(True)
        self.bar.setRange(0, max(0, int(steps * (1 - self.span / self.duration))))
        self.bar.setPageStep(max(1, int(steps * self.span / self.duration)))
        self.bar.setValue(int(steps * self.t0 / self.duration))
        self.bar.blockSignals(False)

    def _scrolled(self, v: int) -> None:
        self.t0 = min(max(0.0, v / 1000 * self.duration), max(0.0, self.duration - self.span))
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.bar.setGeometry(LEFT, self.height() - BAR, self.width() - LEFT, BAR)

    def _hit(self, x: float) -> tuple[int, str]:
        """(item index, 'start' | 'end' | 'move') under x, or (-1, '')."""
        best = (-1, "")
        for i in sorted(self.visible, key=lambda i: self.items[i].end - self.items[i].start):
            it = self.items[i]
            xa, xb = self.x_of(it.start), self.x_of(it.end)
            if abs(x - xa) <= EDGE:
                return i, "start"
            if abs(x - xb) <= EDGE:
                return i, "end"
            if xa < x < xb and best[0] < 0:
                best = (i, "move")
        return best

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#121317"))
        wr, lr = self._wave_rect(), self._lane_rect()
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        p.setFont(f)
        # pitch grid + labels
        for m in range(self.lo, self.hi + 1):
            if m % 12 in (0, 4, 7):
                y = self._y_of_midi(m)
                p.setPen(QColor("#24262d") if m % 12 else QColor("#30333c"))
                p.drawLine(QPointF(LEFT, y), QPointF(self.width(), y))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(0, y - 7, LEFT - 4, 14), Qt.AlignRight | Qt.AlignVCenter, midi_to_name(m))
        # time ticks
        step = next(s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300) if self.span / s <= 14)
        t = math.ceil(self.t0 / step) * step
        while t <= self.t0 + self.span:
            x = self.x_of(t)
            p.setPen(QColor("#22242b"))
            p.drawLine(QPointF(x, wr.top()), QPointF(x, lr.bottom()))
            p.setPen(QColor(theme.MUTED))
            label = f"{int(t) // 60}:{t % 60:04.1f}" if step < 1 else f"{int(t) // 60}:{int(t) % 60:02d}"
            p.drawText(QRectF(x + 2, wr.top(), 60, 12), Qt.AlignLeft | Qt.AlignTop, label)
            t += step
        # clip shading behind the waveform
        for i in self.visible:
            it = self.items[i]
            xa, xb = self.x_of(it.start), self.x_of(it.end)
            if xb < LEFT or xa > self.width():
                continue
            c = QColor(theme.OK if i in self.ticked else "#5a5d68")
            c.setAlpha(45 if i != self.sel else 80)
            p.fillRect(QRectF(xa, wr.top(), xb - xa, wr.height()), c)
        # waveform
        if len(self.peaks) > 1:
            mid, amp = wr.center().y(), wr.height() / 2
            n = len(self.peaks)
            path = QPainterPath()
            cols = int(self._w())
            top = []
            for c in range(0, cols, 2):
                t = self.t_of(LEFT + c)
                if t < 0 or t > self.duration:
                    continue
                t2 = self.t_of(LEFT + c + 2)
                a, b = int(t / self.duration * n), max(int(t / self.duration * n) + 1, int(t2 / self.duration * n))
                top.append((LEFT + c, float(self.peaks[a:b].max()) if b <= n else 0.0))
            if top:
                path.moveTo(top[0][0], mid)
                for x, v in top:
                    path.lineTo(x, mid - v * amp * 0.9)
                for x, v in reversed(top):
                    path.lineTo(x, mid + v * amp * 0.9)
                path.closeSubpath()
                p.fillPath(path, QColor(120, 125, 140, 90))
        # pitch curve
        if len(self.times):
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            a = max(0, int(np.searchsorted(self.times, self.t0)) - 1)
            b = min(len(self.times), int(np.searchsorted(self.times, self.t0 + self.span)) + 1)
            stride = max(1, (b - a) // 1500)
            prev = None
            for k in range(a, b, stride):
                m = self.midi[k]
                if not np.isfinite(m):
                    prev = None
                    continue
                pt = QPointF(self.x_of(self.times[k]), self._y_of_midi(m))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt
        # block lane
        p.fillRect(lr, QColor("#17181c"))
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        for i in sorted(self.visible, key=lambda i: i == self.sel):
            it = self.items[i]
            xa, xb = self.x_of(it.start), self.x_of(it.end)
            if xb < LEFT or xa > self.width():
                continue
            r = QRectF(max(LEFT, xa), lr.top() + 2, max(3.0, min(self.width(), xb) - max(LEFT, xa)), lr.height() - 4)
            if i in self.ticked:
                fill = QColor("#7d5cff" if it.kind == "hit" else "#2f8f62")
            else:
                fill = QColor("#3a3d46")
            p.setPen(QPen(QColor(theme.ACCENT), 2) if i == self.sel else QPen(QColor("#0c0c0e"), 1))
            p.setBrush(fill)
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor("#ffffff") if i in self.ticked else QColor(theme.MUTED))
            p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, self.labels.get(i, ""))
        if self._drag is not None and self._drag[0] == "new" and self._drag[4] > self._drag[3]:
            xa, xb = self.x_of(self._drag[3]), self.x_of(self._drag[4])
            c = QColor(theme.ACCENT)
            c.setAlpha(60)
            p.fillRect(QRectF(xa, wr.top(), xb - xa, lr.bottom() - wr.top()), c)
        if self.playhead is not None:
            x = self.x_of(self.playhead)
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.drawLine(QPointF(x, wr.top()), QPointF(x, lr.bottom()))
        p.end()

    # ------------------------------------------------------------------ interaction
    def mousePressEvent(self, e) -> None:
        self.setFocus()
        x = e.position().x()
        if x < LEFT or e.position().y() > self.height() - BAR:
            return
        i, mode = self._hit(x)
        t = self.t_of(x)
        if i >= 0:
            it = self.items[i]
            self._drag = (mode, i, t, it.start, it.end)
            if i != self.sel:
                self.sel = i
                self.selected.emit(i)
        else:
            self._drag = ("new", -1, t, t, t)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        x = e.position().x()
        if self._drag is None:
            _i, mode = self._hit(x)
            self.setCursor(Qt.SizeHorCursor if mode in ("start", "end") else
                           Qt.OpenHandCursor if mode == "move" else Qt.IBeamCursor if x > LEFT else Qt.ArrowCursor)
            return
        mode, i, t_press, s0, e0 = self._drag
        t = min(max(0.0, self.t_of(x)), self.duration)
        if mode == "new":
            self._drag = (mode, i, t_press, min(t_press, t), max(t_press, t))
            self.update()
            return
        it = self.items[i]
        if mode == "start":
            it.start = min(max(0.0, t), it.end - MIN_LEN)
        elif mode == "end":
            it.end = max(min(self.duration, t), it.start + MIN_LEN)
        else:
            d = min(max(t - t_press, -s0), self.duration - e0)
            it.start, it.end = s0 + d, e0 + d
        self.changed.emit(i)
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is None:
            return
        mode, i, _t, s0, e0 = self._drag
        self._drag = None
        if mode == "new":
            if e0 - s0 >= MIN_LEN:
                self.created.emit(s0, e0)
            elif self.sel != -1:
                self.sel = -1
                self.selected.emit(-1)
        else:
            self.edited.emit(i)
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        i, _mode = self._hit(e.position().x())
        if i >= 0:
            self.play_requested.emit(i)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel >= 0:
            self.deleted.emit(self.sel)
        elif e.key() == Qt.Key_Space and self.sel >= 0:
            self.play_requested.emit(self.sel)
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e) -> None:
        dy = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier or e.angleDelta().x():
            self.t0 = min(max(0.0, self.t0 - dy / 120 * self.span * 0.15), max(0.0, self.duration - self.span))
        else:
            anchor = self.t_of(e.position().x())
            factor = 0.8 if dy > 0 else 1.25
            self.span = min(self.duration, max(0.5, self.span * factor))
            frac = (e.position().x() - LEFT) / self._w()
            self.t0 = min(max(0.0, anchor - frac * self.span), max(0.0, self.duration - self.span))
        self._sync_bar()
        self.update()


def item_label(it, assign: tuple[bool, int | None] | None = None) -> str:
    """Block label: the note heard (→ the note / pad it's assigned to if different), or the drum pad."""
    if assign and assign[0]:
        return f"🥁 {drum_short(assign[1])}" if assign[1] is not None else "🥁 hit"
    heard = midi_to_name(int(round(it.midi))) if it.midi is not None else "?"
    if assign and assign[1] is not None and it.midi is not None and assign[1] != int(round(it.midi)):
        return f"{heard}→{midi_to_name(assign[1])}"
    return heard if assign is None or assign[1] is None else midi_to_name(assign[1])
