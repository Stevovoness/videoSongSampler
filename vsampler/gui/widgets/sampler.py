"""Low-latency sample pad: plays a clip exactly as it will sound in the rendered video.

Each (clip, trim, shift, volume) combination is rendered once in the background with the same code the
renderer uses, written to a WAV and loaded into a QSoundEffect, which starts playing with very little delay.
Different keys can sound at the same time; pressing a key again restarts it.
"""
from __future__ import annotations

import copy
import os
import shutil
import tempfile

import soundfile as sf
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QSoundEffect

from ...drums import is_drum_key
from ...render import sources
from ...render.renderer import all_slot_keys, project_slot, render_slot_audio
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
        self._n = 0

    def __del__(self):
        shutil.rmtree(getattr(self, "_dir", ""), ignore_errors=True)

    def _sig(self, key: int, shift: float) -> tuple | None:
        try:
            slot = project_slot(self.state.project, key)
        except KeyError:
            return None
        return (key, sources.source_key(slot), round(shift, 3), round(slot.gain_db, 2),
                self.state.project.render.even_volumes, slot.autotune)

    # ------------------------------------------------------------------ public
    def trigger(self, key: int, shift: float = 0.0) -> None:
        """Play slot `key` (pitch-shifted by `shift` semitones) now, or as soon as it's ready."""
        sig = self._sig(key, shift)
        if sig is None:
            return
        fx = self._fx.get(sig)
        if fx is not None and fx.status() == QSoundEffect.Status.Ready:
            fx.stop()
            fx.play()
            return
        self._play_when_ready.add(sig)
        if fx is None:
            self._prepare(sig, key, shift)

    def warm(self) -> None:
        """Get every clip ready to play (unshifted) and drop sounds for clips that changed."""
        wanted = {}
        for key in all_slot_keys(self.state.project):
            slot = project_slot(self.state.project, key)
            if os.path.exists(slot.path):
                shift = 0.0 if is_drum_key(key) else slot.autotune_shift(key)
                wanted[self._sig(key, shift)] = (key, shift)
        # a sound stays valid while its slot's clip, trim and volume settings are unchanged (any shift)
        current = {(s[0], s[1]) + s[3:] for s in wanted}
        for sig in list(self._fx):
            if (sig[0], sig[1]) + sig[3:] not in current:
                self._fx.pop(sig).deleteLater()
        for sig, (key, shift) in wanted.items():
            if sig not in self._fx and sig not in self._loading:
                self._prepare(sig, key, shift)

    # ------------------------------------------------------------------ internals
    def _prepare(self, sig: tuple, key: int, shift: float) -> None:
        if sig in self._loading:
            return
        self._loading.add(sig)
        self._n += 1
        path = os.path.join(self._dir, f"pad{self._n}.wav")
        project = copy.deepcopy(self.state.project)

        def job():
            y = render_slot_audio(project, key, shift)
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
