"""Shared application state with change notifications."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QPixmap

from ..models import Project, Song
from ..planner import Plan, Resolution, plan_for_project, resolve_drum, resolve_opts
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
        self.thumbs: dict[str, QPixmap] = {}       # clips.trim_thumb_key(clip) -> thumbnail
        self.confidence: dict[str, float] = {}
        self.project_path: str = ""
        self.dirty = False

    # --- helpers --------------------------------------------------------
    def events(self):
        if self.song is None:
            return []
        return apply_options(self.song, self.project.song, include_drums=bool(self.project.drum_slots))

    def plan(self) -> Plan | None:
        if self.song is None:
            return None
        return plan_for_project(self.events(), self.project)

    def needed_pitches(self) -> set[int]:
        return {e.pitch for e in self.events() if not e.drum}

    def needed_drums(self) -> set[int]:
        return {e.pitch for e in self.events() if e.drum}

    def resolve_key(self, midi: int) -> Resolution:
        """What a piano key plays - the same clip and shift the video would use for that note."""
        return resolve_opts(midi, self.project.slots, self.project.song)

    def resolve_pad(self, note: int) -> Resolution:
        return resolve_drum(note, self.project.drum_slots, self.project.song.drum_standins)

    def touch_slots(self) -> None:
        self.dirty = True
        self.slots_changed.emit()

    def touch_options(self) -> None:
        self.dirty = True
        self.options_changed.emit()
