"""Loading note clips: audio, pitch detection, auto-trim and frame storage."""
from __future__ import annotations

import bisect
import threading
from collections import OrderedDict
from dataclasses import dataclass

import av
import cv2
import numpy as np

from .notes import freq_to_midi

SR = 44100
FRAME_MAX_SIDE = 960   # stored frame resolution (longest side)


class ClipError(Exception):
    pass


def trim_thumb_key(slot) -> str:
    """Thumbnail cache key for one clip: many clips can be cut from the same long video."""
    return f"{slot.path}|{slot.trim_start:.3f}"


def _rotate(img: np.ndarray, rotation: int) -> np.ndarray:
    k = int(round(rotation / 90.0)) % 4
    return np.ascontiguousarray(np.rot90(img, k)) if k else img


def load_audio(path: str, sr: int = SR) -> np.ndarray:
    """Decode a media file's first audio track to float32 stereo, shape (2, n)."""
    try:
        container = av.open(path)
    except Exception as e:  # noqa: BLE001
        raise ClipError(f"Could not open {path}: {e}") from e
    with container:
        if not container.streams.audio:
            raise ClipError("This video has no sound track.")
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sr)
        chunks = []
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    if not chunks:
        raise ClipError("The sound track is empty.")
    return np.concatenate(chunks, axis=1).astype(np.float32)


def detect_pitch(audio: np.ndarray, sr: int = SR) -> tuple[float | None, float]:
    """Return (fractional MIDI pitch, confidence 0..1) of a mono/stereo clip."""
    import librosa

    mono = audio.mean(axis=0) if audio.ndim == 2 else audio
    if len(mono) < sr * 0.05:
        return None, 0.0
    # pYIN is slow at 44.1k; 22.05k is plenty for pitch.
    y = librosa.resample(mono, orig_sr=sr, target_sr=22050)
    f0, voiced, prob = librosa.pyin(y, fmin=60.0, fmax=4200.0, sr=22050, frame_length=2048)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    n = min(len(f0), len(rms))
    f0, voiced, prob, rms = f0[:n], voiced[:n], prob[:n], rms[:n]
    ok = voiced & np.isfinite(f0) & (rms > rms.max() * 0.1)
    if ok.sum() < 3:
        return None, 0.0
    midis = 69 + 12 * np.log2(f0[ok] / 440.0)
    weights = rms[ok] * prob[ok]
    # weighted median - robust against the pitch glides babies love
    order = np.argsort(midis)
    cw = np.cumsum(weights[order])
    median = float(midis[order][np.searchsorted(cw, cw[-1] / 2)])
    confidence = float(min(1.0, ok.sum() / max(1, (rms > rms.max() * 0.1).sum())) * prob[ok].mean())
    return median, confidence


def auto_trim(audio: np.ndarray, sr: int = SR, threshold_db: float = -30.0) -> tuple[float, float]:
    """Find where the sound starts and ends (seconds)."""
    mono = np.abs(audio).max(axis=0) if audio.ndim == 2 else np.abs(audio)
    hop = int(sr * 0.005)
    n = len(mono) // hop
    if n < 2:
        return 0.0, len(mono) / sr
    env = mono[: n * hop].reshape(n, hop).max(axis=1)
    peak = env.max()
    if peak <= 0:
        return 0.0, len(mono) / sr
    above = np.nonzero(env > peak * 10 ** (threshold_db / 20))[0]
    start = max(0.0, above[0] * hop / sr - 0.02)
    end = min(len(mono) / sr, (above[-1] + 1) * hop / sr + 0.08)
    return start, end


TARGET_LOUDNESS_DB = -18.0   # every clip is levelled to this
MAX_LEVEL_GAIN_DB = 30.0


def measure_loudness(audio: np.ndarray, sr: int = SR) -> float | None:
    """Loudness (dBFS RMS) of the part of the clip where there is actually sound.

    Uses 50 ms windows and ignores those more than 30 dB below the loudest one, so a clip
    with a short squeal and long silence measures the same as a long steady note.
    """
    mono = audio.mean(axis=0) if audio.ndim == 2 else audio
    win = int(sr * 0.05)
    n = len(mono) // win
    if n < 1:
        return None
    rms = np.sqrt((mono[: n * win].reshape(n, win).astype(np.float64) ** 2).mean(axis=1))
    peak = rms.max()
    if peak <= 1e-6:
        return None
    active = rms[rms > peak * 10 ** (-30 / 20)]
    return float(20 * np.log10(np.sqrt((active ** 2).mean())))


def level_gain_db(loudness_db: float | None) -> float:
    """Gain that brings a clip to the common target loudness."""
    if loudness_db is None:
        return 0.0
    return float(np.clip(TARGET_LOUDNESS_DB - loudness_db, -MAX_LEVEL_GAIN_DB, MAX_LEVEL_GAIN_DB))


@dataclass
class ClipAnalysis:
    duration: float
    detected_midi: float | None
    confidence: float
    trim_start: float
    trim_end: float
    thumbnail: np.ndarray | None  # RGB
    loudness_db: float | None = None


def video_duration(path: str) -> float:
    """Length of a media file in seconds (from its header; nothing is decoded)."""
    with av.open(path) as c:
        if c.duration:
            return c.duration / 1e6
        st = (c.streams.audio or c.streams.video)[0]
        return float(st.duration * st.time_base) if st.duration else 0.0


def grab_frame(path: str, t: float, max_side: int = 320) -> np.ndarray | None:
    """RGB frame nearest to time t (seconds)."""
    with av.open(path) as c:
        if not c.streams.video:
            return None
        st = c.streams.video[0]
        try:
            c.seek(int(max(0.0, t - 0.5) / st.time_base), stream=st)
        except Exception:  # noqa: BLE001
            pass
        best = None
        for frame in c.decode(video=0):
            best = frame
            if frame.time is not None and frame.time >= t:
                break
        if best is None:
            return None
        img = _rotate(best.to_ndarray(format="rgb24"), best.rotation)
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


def analyze_clip(path: str, detect: bool = True) -> tuple[ClipAnalysis, np.ndarray]:
    """Full analysis of a clip. Returns (analysis, stereo audio). detect=False skips pitch (drum clips)."""
    audio = load_audio(path)
    start, end = auto_trim(audio)
    seg = audio[:, int(start * SR): int(end * SR)]
    midi, conf = detect_pitch(seg) if detect else (None, 0.0)
    # thumbnail at the loudest moment
    mono = np.abs(seg).mean(axis=0)
    loud_t = start + (int(np.argmax(mono)) / SR if len(mono) else 0.0)
    thumb = grab_frame(path, loud_t)
    return ClipAnalysis(audio.shape[1] / SR, midi, conf, start, end, thumb, measure_loudness(seg)), audio


class ClipFrames:
    """Frames of a clip stored as JPEG in memory, decoded on demand with an LRU cache."""

    def __init__(self, path: str, t0: float = 0.0, t1: float | None = None, max_side: int = FRAME_MAX_SIDE):
        self.times: list[float] = []
        self._jpgs: list[bytes] = []
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        with av.open(path) as c:
            if not c.streams.video:
                raise ClipError("This file has no video track.")
            st = c.streams.video[0]
            st.thread_type = "AUTO"
            if t0 > 0.5:
                try:
                    c.seek(int((t0 - 0.5) / st.time_base), stream=st)
                except Exception:  # noqa: BLE001
                    pass
            prev = None
            for frame in c.decode(video=0):
                ft = frame.time if frame.time is not None else 0.0
                if ft < t0:
                    prev = frame
                    continue
                if prev is not None and not self.times:
                    self._add(prev, max_side, t0)   # frame showing at t0
                if t1 is not None and ft > t1:
                    break
                self._add(frame, max_side, ft)
                prev = None
            if not self.times and prev is not None:
                self._add(prev, max_side, t0)
        if not self.times:
            raise ClipError("Could not read any video frames.")
        self.size = self.get(0).shape[1::-1]  # (w, h)

    def _add(self, frame, max_side: int, t: float) -> None:
        img = _rotate(frame.to_ndarray(format="bgr24"), frame.rotation)
        h, w = img.shape[:2]
        s = max_side / max(h, w)
        if s < 1:
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        self.times.append(t)
        self._jpgs.append(buf.tobytes())

    def __len__(self) -> int:
        return len(self.times)

    def get(self, i: int) -> np.ndarray:
        """BGR frame i."""
        i = max(0, min(len(self._jpgs) - 1, i))
        with self._lock:
            img = self._cache.get(i)
            if img is not None:
                self._cache.move_to_end(i)
                return img
        img = cv2.imdecode(np.frombuffer(self._jpgs[i], np.uint8), cv2.IMREAD_COLOR)
        with self._lock:
            self._cache[i] = img
            if len(self._cache) > 48:
                self._cache.popitem(last=False)
        return img

    def at(self, t: float, blend: bool = False) -> np.ndarray:
        """Frame at source time t (seconds, absolute in the source clip)."""
        j = bisect.bisect_right(self.times, t) - 1
        if j < 0:
            return self.get(0)
        if not blend or j + 1 >= len(self.times):
            return self.get(j)
        t_a, t_b = self.times[j], self.times[j + 1]
        a = (t - t_a) / (t_b - t_a) if t_b > t_a else 0.0
        if a < 0.08:
            return self.get(j)
        if a > 0.92:
            return self.get(j + 1)
        return cv2.addWeighted(self.get(j), 1 - a, self.get(j + 1), a, 0)
