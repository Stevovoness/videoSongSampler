"""Decoded clip audio + frames, cached between renders and previews."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from ..clips import SR, ClipFrames, load_audio
from ..models import ClipSlot


@dataclass
class ClipSource:
    key: str
    audio: np.ndarray        # trimmed stereo audio
    frames: ClipFrames       # frames from trim_start onward (absolute source times)
    trim_start: float
    length: float            # trimmed sound length (seconds)

    @property
    def aspect(self) -> float:
        w, h = self.frames.size
        return w / h


_lock = threading.Lock()
_audio_cache: dict[str, np.ndarray] = {}
_sources: dict[str, ClipSource] = {}


def source_key(slot: ClipSlot) -> str:
    return f"{slot.path}|{slot.trim_start:.4f}|{slot.trim_end}"


def get_audio(path: str) -> np.ndarray:
    with _lock:
        a = _audio_cache.get(path)
    if a is None:
        a = load_audio(path)
        with _lock:
            _audio_cache[path] = a
    return a


def store_audio(path: str, audio: np.ndarray) -> None:
    with _lock:
        _audio_cache[path] = audio


def _trimmed(slot: ClipSlot) -> tuple[np.ndarray, float, float]:
    audio = get_audio(slot.path)
    total = audio.shape[1] / SR
    t0 = max(0.0, min(slot.trim_start, total - 0.02))
    t1 = total if slot.trim_end is None else max(t0 + 0.02, min(slot.trim_end, total))
    return audio[:, int(t0 * SR): int(t1 * SR)], t0, t1


def trimmed_audio(slot: ClipSlot) -> np.ndarray:
    """Just the trimmed sound of a clip (no video frames decoded)."""
    with _lock:
        src = _sources.get(source_key(slot))
    return src.audio if src is not None else np.ascontiguousarray(_trimmed(slot)[0])


def get_source(slot: ClipSlot) -> ClipSource:
    key = source_key(slot)
    with _lock:
        src = _sources.get(key)
    if src is not None:
        return src
    seg, t0, t1 = _trimmed(slot)
    frames = ClipFrames(slot.path, t0, t1 + 0.1)
    src = ClipSource(key, np.ascontiguousarray(seg), frames, t0, seg.shape[1] / SR)
    with _lock:
        # drop stale entries for the same file with old trims
        for k in [k for k in _sources if k.split("|")[0] == slot.path]:
            del _sources[k]
        _sources[key] = src
    return src


def forget_frames(path: str) -> None:
    with _lock:
        for k in [k for k in _sources if k.split("|")[0] == path]:
            del _sources[k]


def forget(path: str) -> None:
    with _lock:
        _audio_cache.pop(path, None)
        for k in [k for k in _sources if k.split("|")[0] == path]:
            del _sources[k]
