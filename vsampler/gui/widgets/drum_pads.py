"""Grid of drum pads (one per General MIDI drum sound). Click to select + play, drop a video to assign."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QToolTip, QWidget

from ...drums import CORE_KIT, GM_DRUMS, drum_name, drum_short
from .. import theme
from .keyboard import VIDEO_EXT

ROWS = 4


class DrumPads(QWidget):
    key_clicked = Signal(int)          # GM drum note
    files_dropped = Signal(int, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assigned: set[int] = set()
        self.needed: set[int] = set()
        self.standin: dict[int, int] = {}        # needed note -> note whose clip stands in
        self.thumbs: dict[int, QPixmap] = {}
        self.letters: dict[int, str] = {}
        self.show_all = False
        self.selected: int | None = None
        self._lit: set[int] = set()
        self._hover: int | None = None
        self._drop: int | None = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setMinimumHeight(200)

    # ------------------------------------------------------------ state
    def set_state(self, assigned: set[int], needed: set[int], standin: dict[int, int],
                  thumbs: dict[int, QPixmap]) -> None:
        self.assigned, self.needed, self.standin, self.thumbs = assigned, needed, standin, thumbs
        self.update()

    def set_show_all(self, on: bool) -> None:
        self.show_all = on
        self.update()

    def set_letters(self, letters: dict[int, str]) -> None:
        self.letters = letters
        self.update()

    def select(self, note: int | None) -> None:
        self.selected = note
        self.update()

    def flash(self, note: int, ms: int = 160) -> None:
        self._lit.add(note)
        self.update()

        def off():
            self._lit.discard(note)
            self.update()

        QTimer.singleShot(ms, off)

    def pads(self) -> list[int]:
        """Pads on screen, in order: the 16-pad core kit, then the song's / assigned extras (or all drums)."""
        extra = set(GM_DRUMS) if self.show_all else (self.needed | self.assigned)
        return CORE_KIT + sorted(extra - set(CORE_KIT))

    # ------------------------------------------------------------ geometry
    def _rects(self) -> list[tuple[int, QRectF]]:
        pads = self.pads()
        gap = 6.0
        n_cols = 4 + math.ceil((len(pads) - len(CORE_KIT)) / ROWS)
        ph = (self.height() - gap * (ROWS + 1)) / ROWS
        pw = min(ph * 1.45, (self.width() - gap * (n_cols + 2)) / n_cols)
        out = []
        for i, note in enumerate(pads):
            if i < len(CORE_KIT):
                col, row = i % 4, i // 4
                x = gap + col * (pw + gap)
            else:
                j = i - len(CORE_KIT)
                col, row = 4 + j // ROWS, j % ROWS
                x = gap * 3 + col * (pw + gap)    # small gutter between the kit and the extras
            out.append((note, QRectF(x, gap + row * (ph + gap), pw, ph)))
        return out

    def pad_at(self, x: float, y: float) -> int | None:
        for note, r in self._rects():
            if r.contains(x, y):
                return note
        return None

    # ------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        f = QFont(self.font())
        for note, r in self._rects():
            has = note in self.assigned
            fill = QColor("#1f4d38") if has else QColor("#24262d")
            if note == self._hover or note == self._drop:
                fill = fill.lighter(135)
            path = QPainterPath()
            path.addRoundedRect(r, 9, 9)
            p.setPen(QPen(QColor("#0c0c0e"), 1))
            p.fillPath(path, fill)
            # clip thumbnail as the pad's face
            pm = self.thumbs.get(note) if has else None
            if pm is not None and not pm.isNull():
                p.save()
                p.setClipPath(path)
                scaled = pm.scaled(int(r.width()), int(r.height()), Qt.KeepAspectRatioByExpanding,
                                   Qt.SmoothTransformation)
                sx, sy = (scaled.width() - r.width()) / 2, (scaled.height() - r.height()) / 2
                p.setOpacity(0.55 if note not in self._lit else 0.9)
                p.drawPixmap(r.topLeft(), scaled, QRectF(sx, sy, r.width(), r.height()))
                p.restore()
            if note in self._lit:
                p.fillPath(path, QColor(255, 182, 39, 150))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(theme.ACCENT), 3) if note == self.selected else QPen(QColor("#3a3e48"), 1))
            p.drawPath(path)
            # name
            f.setPointSizeF(max(7.0, min(10.0, r.width() / 9)))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT) if has else QColor(theme.MUTED))
            p.drawText(r.adjusted(8, 6, -20, -6), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, drum_short(note))
            # status dot
            if note in self.needed:
                color = QColor(theme.ACCENT) if has else QColor(theme.WARN) if note in self.standin else QColor(theme.BAD)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(QRectF(r.right() - 16, r.top() + 8, 9, 9))
            # computer key
            letter = self.letters.get(note)
            if letter:
                f.setPointSizeF(8)
                p.setFont(f)
                chip = QRectF(r.right() - 24, r.bottom() - 22, 18, 16)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 140))
                p.drawRoundedRect(chip, 4, 4)
                p.setPen(QColor(theme.ACCENT))
                p.drawText(chip, Qt.AlignCenter, letter)
        p.end()

    # ------------------------------------------------------------ interaction
    def mouseMoveEvent(self, e) -> None:
        k = self.pad_at(e.position().x(), e.position().y())
        if k != self._hover:
            self._hover = k
            self.update()
            if k is not None:
                if k in self.assigned:
                    status = "clip loaded — click to play"
                elif k in self.standin:
                    status = f"no clip — {drum_short(self.standin[k])} stands in"
                else:
                    status = "no clip yet — click or drop a video"
                if k in self.needed and k not in self.assigned:
                    status += " (the song uses this sound)"
                if k in self.letters:
                    status += f"   ·   key: {self.letters[k]}"
                QToolTip.showText(e.globalPosition().toPoint(), f"{drum_name(k)} (drum {k}): {status}", self)

    def leaveEvent(self, _e) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:
        k = self.pad_at(e.position().x(), e.position().y())
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
        k = self.pad_at(e.position().x(), e.position().y())
        if k != self._drop:
            self._drop = k
            self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:
        self._drop = None
        self.update()

    def dropEvent(self, e) -> None:
        paths = self._paths(e)
        k = self.pad_at(e.position().x(), e.position().y())
        self._drop = None
        self.update()
        if paths and k is not None:
            self.files_dropped.emit(k, paths[:1])
            e.acceptProposedAction()
