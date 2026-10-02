"""Clickable piano keyboard where each key is a clip slot. Accepts dropped video files."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ...notes import is_black, midi_to_name, pretty_name
from .. import theme

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".3gp", ".mts", ".flv")


class PianoKeyboard(QWidget):
    key_clicked = Signal(int)
    files_dropped = Signal(int, list)

    def __init__(self, lo: int = 48, hi: int = 84, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        self.assigned: set[int] = set()
        self.needed: set[int] = set()
        self.borrowed: set[int] = set()
        self.octave: set[int] = set()
        self.letters: dict[int, str] = {}          # computer-keyboard key that plays each note
        self.takes: dict[int, int] = {}            # notes with several takes -> how many
        self.describe = None                       # optional fn(midi) -> what the key plays (tooltip)
        self.selected: int | None = None
        self._lit: set[int] = set()
        self._hover: int | None = None
        self._drop: int | None = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)

    def set_range(self, lo: int, hi: int) -> None:
        while is_black(lo):
            lo -= 1
        while is_black(hi):
            hi += 1
        self.lo, self.hi = lo, hi
        self.update()

    def set_state(self, assigned: set[int], needed: set[int], borrowed: set[int],
                  octave: set[int] = frozenset()) -> None:
        self.assigned, self.needed, self.borrowed, self.octave = assigned, needed, borrowed, set(octave)
        self.update()

    def set_letters(self, letters: dict[int, str]) -> None:
        self.letters = letters
        self.update()

    def select(self, midi: int | None) -> None:
        self.selected = midi
        self.update()

    def flash(self, midi: int, ms: int = 220) -> None:
        """Show a key as pressed for a moment (it's being played)."""
        self._lit.add(midi)
        self.update()

        def off():
            self._lit.discard(midi)
            self.update()

        QTimer.singleShot(ms, off)

    # ------------------------------------------------------------ geometry
    def _whites(self) -> list[int]:
        return [m for m in range(self.lo, self.hi + 1) if not is_black(m)]

    def _key_rects(self) -> list[tuple[int, QRectF, bool]]:
        whites = self._whites()
        if not whites:
            return []
        ww = self.width() / len(whites)
        h = self.height() - 1
        rects = []
        pos = {}
        for i, m in enumerate(whites):
            pos[m] = i
            rects.append((m, QRectF(i * ww, 0, ww, h), False))
        bw, bh = ww * 0.62, h * 0.6
        for m in range(self.lo, self.hi + 1):
            if is_black(m) and (m - 1) in pos:
                x = (pos[m - 1] + 1) * ww - bw / 2
                rects.append((m, QRectF(x, 0, bw, bh), True))
        return rects

    def key_at(self, x: float, y: float) -> int | None:
        rects = self._key_rects()
        for m, r, black in reversed(rects):  # blacks are drawn on top
            if black and r.contains(x, y):
                return m
        for m, r, black in rects:
            if not black and r.contains(x, y):
                return m
        return None

    # ------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        rects = self._key_rects()
        for m, r, black in sorted(rects, key=lambda k: k[2]):
            if m in self.assigned:
                fill = QColor("#2f8f62") if not black else QColor("#1f6b48")
            else:
                fill = QColor("#f4f4f6") if not black else QColor("#1b1c20")
            if m == self._hover or m == self._drop:
                fill = fill.lighter(125) if black or m in self.assigned else QColor("#ffe7b3")
            if m in self._lit:
                fill = QColor(theme.ACCENT)
            p.setPen(QPen(QColor("#0c0c0e"), 1))
            p.setBrush(fill)
            p.drawRoundedRect(r.adjusted(0.5, 0, -0.5, 0), 4, 4)
            # status marker: red = needed, no clip · blue = octave jump · amber = borrowed · accent = needed + have
            if m in self.needed:
                if m in self.assigned:
                    color = QColor(theme.ACCENT)
                elif m in self.octave:
                    color = QColor(theme.OCTAVE)
                elif m in self.borrowed:
                    color = QColor(theme.WARN)
                else:
                    color = QColor(theme.BAD)
                d = min(r.width() * 0.45, 12)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(QRectF(r.center().x() - d / 2, r.top() + 8, d, d))
            if m == self.selected:
                p.setPen(QPen(QColor(theme.ACCENT), 3))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 4, 4)
            # label
            show = not black and (m % 12 == 0 or m in self.assigned or r.width() > 34) or (black and m in self.assigned)
            if show:
                f.setPointSizeF(max(6.5, min(9.0, r.width() / 4.2)))
                f.setBold(m % 12 == 0 or m in self.assigned)
                p.setFont(f)
                light = black or m in self.assigned
                p.setPen(QColor("#ffffff") if light else QColor("#333"))
                name = midi_to_name(m)
                p.drawText(r.adjusted(0, 0, 0, -6), Qt.AlignHCenter | Qt.AlignBottom, name)
            # several takes: small "×3" badge
            n_takes = self.takes.get(m, 1)
            if n_takes > 1:
                f.setPointSizeF(7)
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor("#ffffff"))
                p.drawText(QRectF(r.left(), r.top() + (22 if m in self.needed else 6), r.width(), 12),
                           Qt.AlignHCenter | Qt.AlignTop, f"×{n_takes}")
            # computer-keyboard letter that plays this key
            letter = self.letters.get(m)
            if letter:
                f.setPointSizeF(max(6.5, min(8.5, r.width() / 4.5)))
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor(theme.ACCENT) if black or m in self.assigned else QColor("#8a6400"))
                ly = r.top() + (r.height() * 0.30 if black else r.height() * 0.62)
                p.drawText(QRectF(r.left(), ly, r.width(), 16), Qt.AlignHCenter | Qt.AlignTop, letter)
        p.end()

    # ------------------------------------------------------------ interaction
    def mouseMoveEvent(self, e) -> None:
        k = self.key_at(e.position().x(), e.position().y())
        if k != self._hover:
            self._hover = k
            self.update()
            if k is not None:
                status = self.describe(k) if self.describe else (
                    "clip loaded" if k in self.assigned else "no clip yet — click or drop a video")
                if k in self.needed and k not in self.assigned:
                    status += " (the song needs this note!)"
                if self.takes.get(k, 1) > 1:
                    status += f" · {self.takes[k]} takes, used in turn"
                if k in self.letters:
                    status += f"   ·   key: {self.letters[k]}"
                QToolTip.showText(e.globalPosition().toPoint(), f"{pretty_name(k)}: {status}", self)

    def leaveEvent(self, _e) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:
        k = self.key_at(e.position().x(), e.position().y())
        if k is not None:
            self.key_clicked.emit(k)

    def _paths(self, e) -> list[str]:
        if not e.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(VIDEO_EXT)]

    def dragEnterEvent(self, e) -> None:
        if self._paths(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        k = self.key_at(e.position().x(), e.position().y())
        if k != self._drop:
            self._drop = k
            self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:
        self._drop = None
        self.update()

    def dropEvent(self, e) -> None:
        paths = self._paths(e)
        k = self.key_at(e.position().x(), e.position().y())
        self._drop = None
        self.update()
        if paths and k is not None:
            self.files_dropped.emit(k, paths[:1])
            e.acceptProposedAction()
