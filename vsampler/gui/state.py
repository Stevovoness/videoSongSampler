"""Shared application state with change notifications."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QPixmap

from ..models import Project, Song
from ..planner import Plan, make_plan
from ..songops import apply_options


def to_pixmap(rgb: np.ndarray | None) -> QPixmap | None:
    if rgb is None:
        return None
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())


class AppState(QObject):
    slots_changed = Signal()
    song_changed = Signal()
    options_changed = Signal()
    project_replaced = Signal()

    def __init__(self):
        super().__init__()
        self.project = Project()
        self.song: Song | None = None
        self.thumbs: dict[str, QPixmap] = {}       # clip path -> thumbnail
        self.confidence: dict[str, float] = {}
        self.project_path: str = ""
        self.dirty = False

    # --- helpers --------------------------------------------------------
    def events(self):
        if self.song is None:
            return []
        return apply_options(self.song, self.project.song)

    def plan(self) -> Plan | None:
        if self.song is None:
            return None
        o = self.project.song
        return make_plan(self.events(), self.project.slots, o.allow_pitch_shift, o.max_shift)

    def needed_pitches(self) -> set[int]:
        return {e.pitch for e in self.events()}

    def touch_slots(self) -> None:
        self.dirty = True
        self.slots_changed.emit()

    def touch_options(self) -> None:
        self.dirty = True
        self.options_changed.emit()
