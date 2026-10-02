"""Low-latency sample pad: plays a clip exactly as it will sound in the rendered video.

Each (clip, trim, shift, volume) combination is rendered once in the background with the same code the
renderer uses, written to a WAV and loaded into a QSoundEffect, which starts playing with very little delay.
Different keys can sound at the same time; pressing a key again restarts it. A slot with several takes plays
them in turn, just like the video does.
"""
from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections import Counter

import soundfile as sf
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QSoundEffect

from ...drums import is_drum_key
from ...models import ClipSlot
from ...render import sources
from ...render.renderer import all_slot_keys, project_slot, render_clip_audio
from ..worker import run_task


class SamplePad(QObject):
    failed = Signal(str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._dir = tempfile.mkdtemp(prefix="vs_pad_")
        self._fx: dict[tuple, QSoundEffect] = {}
        self._loading: set[tuple] = set()
        self._play_when_ready: set[tuple] = set()
        self._presses: Counter = Counter()     # slot key -> presses so far (picks the take)
        self._n = 0

    def __del__(self):
        shutil.rmtree(getattr(self, "_dir", ""), ignore_errors=True)

    def _sig(self, clip: ClipSlot, shift: float) -> tuple:
        return (sources.source_key(clip), round(shift, 3), round(clip.gain_db, 2),
                self.state.project.render.even_volumes)

    # ------------------------------------------------------------------ public
    def trigger(self, key: int, shift: float = 0.0) -> int | None:
        """Play slot `key` (shifted by `shift` semitones, as resolved for take 0) now, or as soon as it's ready.

        Takes rotate on each press. Returns the take played.
        """
        try:
            slot = project_slot(self.state.project, key)
        except KeyError:
            return None
        take = self._presses[key] % slot.take_count
        self._presses[key] += 1
        clip = slot.take(take)
        if not is_drum_key(key):   # each take has its own auto-tune correction
            shift = shift - slot.autotune_shift(key) + clip.autotune_shift(key)
        self.trigger_clip(clip, shift)
        return take

    def trigger_clip(self, clip: ClipSlot, shift: float = 0.0) -> None:
        """Play any clip (it needn't be in the project yet), tuned and levelled like the render."""
        sig = self._sig(clip, shift)
        fx = self._fx.get(sig)
        if fx is not None and fx.status() == QSoundEffect.Status.Ready:
            fx.stop()
            fx.play()
            return
        self._play_when_ready.add(sig)
        if fx is None:
            self._prepare(sig, clip, shift)

    def warm(self) -> None:
        """Get every take of every clip ready to play, and drop sounds for clips that changed."""
        wanted = {}
        project = self.state.project
        for key in all_slot_keys(project):
            slot = project_slot(project, key)
            for t in range(slot.take_count):
                clip = slot.take(t)
                if os.path.exists(clip.path):
                    shift = 0.0 if is_drum_key(key) else clip.autotune_shift(key)
                    wanted[self._sig(clip, shift)] = (clip, shift)
        # a sound stays valid while its clip, trim and volume settings are unchanged (any shift)
        current = {(s[0],) + s[2:] for s in wanted}
        for sig in list(self._fx):
            if (sig[0],) + sig[2:] not in current:
                self._fx.pop(sig).deleteLater()
        for sig, (clip, shift) in wanted.items():
            if sig not in self._fx and sig not in self._loading:
                self._prepare(sig, clip, shift)

    # ------------------------------------------------------------------ internals
    def _prepare(self, sig: tuple, clip: ClipSlot, shift: float) -> None:
        if sig in self._loading:
            return
        self._loading.add(sig)
        self._n += 1
        path = os.path.join(self._dir, f"pad{self._n}.wav")
        project = copy.deepcopy(self.state.project)
        clip = copy.deepcopy(clip)

        def job():
            y = render_clip_audio(project, clip, shift)
            sf.write(path, y.T, project.render.sample_rate, subtype="PCM_16")
            return path

        def done(p):
            self._loading.discard(sig)
            fx = QSoundEffect(self)
            fx.setVolume(1.0)
            fx.statusChanged.connect(lambda: self._status(sig, fx))
            self._fx[sig] = fx
            fx.setSource(QUrl.fromLocalFile(p))

        def fail(e, _tb):
            self._loading.discard(sig)
            self._play_when_ready.discard(sig)
            self.failed.emit(str(e))

        run_task(job, on_done=done, on_error=fail)

    def _status(self, sig: tuple, fx: QSoundEffect) -> None:
        if fx.status() == QSoundEffect.Status.Ready and sig in self._play_when_ready:
            self._play_when_ready.discard(sig)
            fx.play()
        elif fx.status() == QSoundEffect.Status.Error:
            self._play_when_ready.discard(sig)
