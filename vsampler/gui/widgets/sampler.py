"""Low-latency sample pad: plays a clip exactly as it will sound in the rendered video.

Each (clip, trim, shift, volume) combination is rendered once in the background with the same code the
renderer uses and kept in memory. A small mixer streams straight to the sound card (QAudioSink in pull mode), so
every press sounds immediately: different keys overlap, and pressing a key again restarts it. A slot with several
takes plays them in turn, just like the video does.
"""
from __future__ import annotations

import copy
import threading
from collections import Counter, OrderedDict

import numpy as np
from PySide6.QtCore import QIODevice, QObject, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from ...drums import is_drum_key
from ...models import ClipSlot
from ...render import sources
from ...render.renderer import all_slot_keys, project_slot, render_clip_audio
from ..worker import run_task

_CACHE_MAX = 300           # rendered sounds kept in memory (~350 KB per second of sound)
_LATENCY = 0.04            # seconds of audio buffered by the sound card


class _Mixer(QIODevice):
    """Mixes the sounds that are playing and hands them to the sound card on request."""

    def __init__(self, channels: int, fmt: QAudioFormat.SampleFormat):
        super().__init__()
        self.channels, self.fmt = channels, fmt
        self.voices: dict[object, list] = {}      # key -> [float32 array (n, ch), position]
        self.lock = threading.Lock()
        self.frames_out = 0
        self.open(QIODevice.ReadOnly)

    def play(self, key, data: np.ndarray) -> None:
        with self.lock:
            self.voices[key] = [data, 0]         # same key again: restart it

    def stop_all(self) -> None:
        with self.lock:
            self.voices.clear()

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return 1 << 16

    def readData(self, maxlen: int) -> bytes:
        width = 2 if self.fmt == QAudioFormat.Int16 else 4
        frames = maxlen // (width * self.channels)
        if frames <= 0:
            return b""
        out = np.zeros((frames, self.channels), np.float32)
        with self.lock:
            for key in list(self.voices):
                data, pos = self.voices[key]
                chunk = data[pos: pos + frames]
                out[: len(chunk)] += chunk
                pos += len(chunk)
                if pos >= len(data):
                    del self.voices[key]
                else:
                    self.voices[key][1] = pos
        self.frames_out += frames
        np.clip(out, -1.0, 1.0, out=out)
        if self.fmt == QAudioFormat.Int16:
            return (out * 32767).astype(np.int16).tobytes()
        if self.fmt == QAudioFormat.Int32:
            return (out * 2147483647).astype(np.int32).tobytes()
        return out.tobytes()

    def writeData(self, _data) -> int:
        return -1


class SamplePad(QObject):
    failed = Signal(str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._sounds: OrderedDict[tuple, np.ndarray] = OrderedDict()   # sig -> audio ready for the sound card
        self._loading: set[tuple] = set()
        self._play_when_ready: set[tuple] = set()
        self._presses: Counter = Counter()     # slot key -> presses so far (picks the take)
        self._sink: QAudioSink | None = None
        self._mixer: _Mixer | None = None
        self._rate, self._channels = 44100, 2
        self._open_output()

    # ------------------------------------------------------------------ output
    def _open_output(self) -> None:
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(44100)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
            if fmt.sampleFormat() not in (QAudioFormat.Int16, QAudioFormat.Int32, QAudioFormat.Float):
                fmt.setSampleFormat(QAudioFormat.Float)
        self._rate, self._channels = fmt.sampleRate(), max(1, fmt.channelCount())
        self._mixer = _Mixer(self._channels, fmt.sampleFormat())
        self._sink = QAudioSink(device, fmt, self)
        width = 2 if fmt.sampleFormat() == QAudioFormat.Int16 else 4
        self._sink.setBufferSize(int(self._rate * _LATENCY) * self._channels * width)
        self._sink.stateChanged.connect(self._sink_state)
        self._sink.start(self._mixer)

    def _sink_state(self, st) -> None:
        # the sink stops if the sound device goes away or errors: try to restart it
        if st == QAudio.StoppedState and self._sink is not None and self._sink.error() != QAudio.NoError:
            self._sink.deleteLater()
            self._sink = None
            self._open_output()

    def _to_device(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Stereo (2, n) float audio -> (n, channels) at the sound card's rate."""
        if sr != self._rate:
            n = int(round(y.shape[1] * self._rate / sr))
            x_old = np.linspace(0, 1, y.shape[1], endpoint=False)
            x_new = np.linspace(0, 1, n, endpoint=False)
            y = np.stack([np.interp(x_new, x_old, ch) for ch in y])
        if self._channels == 1:
            out = y.mean(axis=0)[:, None]
        elif self._channels == 2:
            out = y.T
        else:
            out = np.zeros((y.shape[1], self._channels), np.float32)
            out[:, :2] = y.T
        return np.ascontiguousarray(out, dtype=np.float32)

    def stop_all(self) -> None:
        if self._mixer is not None:
            self._mixer.stop_all()

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
        data = self._sounds.get(sig)
        if data is not None:
            self._sounds.move_to_end(sig)
            if self._mixer is not None:
                self._mixer.play(sig, data)
            return
        self._play_when_ready.add(sig)
        self._prepare(sig, clip, shift)

    def is_ready(self, clip: ClipSlot, shift: float = 0.0) -> bool:
        return self._sig(clip, shift) in self._sounds

    def warm(self) -> None:
        """Get every take of every clip ready to play."""
        project = self.state.project
        for key in all_slot_keys(project):
            slot = project_slot(project, key)
            for t in range(slot.take_count):
                clip = slot.take(t)
                shift = 0.0 if is_drum_key(key) else clip.autotune_shift(key)
                sig = self._sig(clip, shift)
                if sig not in self._sounds:
                    self._prepare(sig, clip, shift)

    # ------------------------------------------------------------------ internals
    def _prepare(self, sig: tuple, clip: ClipSlot, shift: float) -> None:
        if sig in self._loading:
            return
        self._loading.add(sig)
        project = copy.deepcopy(self.state.project)
        clip = copy.deepcopy(clip)
        sr = project.render.sample_rate

        def job():
            return self._to_device(render_clip_audio(project, clip, shift), sr)

        def done(data):
            self._loading.discard(sig)
            self._sounds[sig] = data
            while len(self._sounds) > _CACHE_MAX:
                self._sounds.popitem(last=False)
            if sig in self._play_when_ready:
                self._play_when_ready.discard(sig)
                if self._mixer is not None:
                    self._mixer.play(sig, data)

        def fail(e, _tb):
            self._loading.discard(sig)
            self._play_when_ready.discard(sig)
            self.failed.emit(str(e))

        run_task(job, on_done=done, on_error=fail)
