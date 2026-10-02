"""Builds each output video frame from the clips that are sounding."""
from __future__ import annotations

import bisect

import cv2
import numpy as np

from ..audio_dsp import time_map
from ..models import RenderSettings
from ..drums import slot_label
from ..notes import midi_to_name
from ..planner import NoteInstance
from .layouts import cover, draw_label, fit_grid, hex_to_bgr
from .sources import ClipSource

MIN_VISIBLE = 0.12   # very short notes stay lit at least this long


class Compositor:
    def __init__(self, instances: list[NoteInstance], sources: dict[int, ClipSource],
                 tile_slots: list[int], settings: RenderSettings):
        self.s = settings
        self.W, self.H = settings.width, settings.height
        self.sources = sources
        self.instances = sorted(instances, key=lambda i: i.start)
        self.starts = [i.start for i in self.instances]
        self.max_vis = max((max(i.duration, MIN_VISIBLE) for i in self.instances), default=0.0)
        self.bg = hex_to_bgr(settings.background)
        self.hl = hex_to_bgr(settings.highlight)
        aspects = sorted(src.aspect for src in sources.values()) or [16 / 9]
        self.aspect = aspects[len(aspects) // 2]
        self.tile_slots = [s for s in tile_slots if s in sources]
        self._last = None
        self._dyn_rects: dict[int, list] = {}
        if settings.layout == "grid":
            self._build_grid_base()

    # ------------------------------------------------------------------ helpers
    def _active(self, t: float) -> dict[int, NoteInstance]:
        out: dict[int, NoteInstance] = {}
        j = bisect.bisect_right(self.starts, t)
        k = j - 1
        while k >= 0 and self.starts[k] >= t - self.max_vis:
            inst = self.instances[k]
            if t < inst.start + max(inst.duration, MIN_VISIBLE) and inst.slot not in out:
                out[inst.slot] = inst   # latest start wins
            k -= 1
        return out

    def _clip_frame(self, inst: NoteInstance, t: float) -> np.ndarray:
        src = self.sources[inst.slot]
        local = min(t - inst.start, inst.duration)
        src_t = src.trim_start + time_map(local, inst.duration, src.length)
        slow = inst.duration > src.length * 1.2
        return src.frames.at(src_t, blend=self.s.smooth_slowmo and slow)

    @staticmethod
    def _label(inst: NoteInstance) -> str:
        return slot_label(inst.slot) if inst.drum else midi_to_name(inst.target)

    def _border(self, img: np.ndarray, x: int, y: int, w: int, h: int) -> None:
        th = max(3, h // 35)
        cv2.rectangle(img, (x + th // 2, y + th // 2), (x + w - 1 - th // 2, y + h - 1 - th // 2), self.hl, th)

    # ------------------------------------------------------------------ grid
    def _build_grid_base(self) -> None:
        self.rects = dict(zip(self.tile_slots, fit_grid(len(self.tile_slots), self.W, self.H, self.aspect)))
        base = np.zeros((self.H, self.W, 3), np.uint8)
        base[:] = self.bg
        for slot, (x, y, w, h) in self.rects.items():
            if self.s.idle_mode == "dim":
                tile = cover(self.sources[slot].frames.get(0), w, h)
                tile = (tile.astype(np.float32) * 0.33).astype(np.uint8)
            else:
                tile = np.full((h, w, 3), [int(c * 0.6 + 12) for c in self.bg], np.uint8)
            base[y:y + h, x:x + w] = tile
            if self.s.show_labels:
                draw_label(base, slot_label(slot), x, y, h, None)
        self.base = base

    def _grid(self, t: float) -> np.ndarray:
        img = self.base.copy()
        for slot, inst in self._active(t).items():
            if slot not in self.rects:
                continue
            x, y, w, h = self.rects[slot]
            img[y:y + h, x:x + w] = cover(self._clip_frame(inst, t), w, h)
            self._border(img, x, y, w, h)
            if self.s.show_labels:
                draw_label(img, self._label(inst), x, y, h, self.hl)
        return img

    # ------------------------------------------------------------------ dynamic
    def _dynamic(self, t: float) -> np.ndarray:
        active = self._active(t)
        if not active:
            if self._last is not None:
                return self._last
            img = np.zeros((self.H, self.W, 3), np.uint8)
            img[:] = self.bg
            return img
        insts = sorted(active.values(), key=lambda i: (i.drum, i.target))
        n = len(insts)
        if n not in self._dyn_rects:
            gap = 0 if n == 1 else 6
            self._dyn_rects[n] = fit_grid(n, self.W, self.H, self.W / self.H if n == 1 else self.aspect, gap)
        img = np.zeros((self.H, self.W, 3), np.uint8)
        img[:] = self.bg
        for inst, (x, y, w, h) in zip(insts, self._dyn_rects[n]):
            img[y:y + h, x:x + w] = cover(self._clip_frame(inst, t), w, h)
            if self.s.show_labels:
                draw_label(img, self._label(inst), x, y, h, self.hl)
        self._last = img
        return img

    def frame(self, t: float) -> np.ndarray:
        """BGR uint8 image at time t (seconds)."""
        return self._grid(t) if self.s.layout == "grid" else self._dynamic(t)
