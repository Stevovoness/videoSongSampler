"""Pitch-preserving note stretching and pitch shifting.

Uses the Rubber Band CLI (high quality, formant preserving) when available,
otherwise librosa's phase vocoder.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections import OrderedDict

import numpy as np
import soundfile as sf

from .paths import find_rubberband

SR = 44100
ATTACK = 0.07        # seconds of the onset that are never stretched
RELEASE = 0.03       # fade-out at the end of each note
FADE_IN = 0.004

_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 400

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def backend_name() -> str:
    return "Rubber Band" if find_rubberband() else "librosa phase vocoder (install Rubber Band for better quality)"


def time_map(t: float, note_dur: float, src_len: float, attack: float = ATTACK) -> float:
    """Map time inside a note (output) to time inside the trimmed clip (source).

    The attack plays at normal speed, the rest is slowed (or not) so the clip
    fills the note. Used for both the audio and the video so they stay in sync.
    """
    if note_dur <= src_len or src_len <= attack:
        if src_len <= attack and note_dur > src_len:
            return t * src_len / note_dur
        return t
    if t <= attack:
        return t
    return attack + (t - attack) * (src_len - attack) / (note_dur - attack)


def _envelope(y: np.ndarray, sr: int, fade_out: bool = True) -> np.ndarray:
    n = y.shape[-1]
    fi = min(n, int(FADE_IN * sr))
    if fi:
        y[..., :fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    if fade_out:
        fo = min(n, int(RELEASE * sr))
        if fo:
            y[..., n - fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    return y


def _rubberband(y: np.ndarray, sr: int, out_dur: float | None, semitones: float, keep: int | None) -> np.ndarray:
    exe = find_rubberband()
    with tempfile.TemporaryDirectory(prefix="vs_rb_") as td:
        src, dst = os.path.join(td, "in.wav"), os.path.join(td, "out.wav")
        sf.write(src, y.T, sr, subtype="FLOAT")
        cmd = [exe, "-3", "--formant", "-q"]
        n_in = y.shape[-1]
        if out_dur is not None:
            n_out = int(round(out_dur * sr))
            cmd += ["-D", f"{out_dur:.6f}"]
            if keep and 0 < keep < n_in and keep < n_out:
                mp = os.path.join(td, "map.txt")
                with open(mp, "w") as f:
                    f.write(f"0 0\n{keep} {keep}\n")
                cmd += ["-M", mp]
        else:
            cmd += ["-t", "1"]
        if abs(semitones) > 1e-4:
            cmd += ["-p", f"{semitones:.5f}"]
        cmd += [src, dst]
        r = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
        if r.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError(f"Rubber Band failed: {r.stderr.decode(errors='ignore')[-400:]}")
        out, _ = sf.read(dst, dtype="float32", always_2d=True)
    return out.T.copy()


def _librosa(y: np.ndarray, sr: int, out_dur: float | None, semitones: float, keep: int | None) -> np.ndarray:
    import librosa

    if abs(semitones) > 1e-4:
        y = np.stack([librosa.effects.pitch_shift(ch, sr=sr, n_steps=semitones) for ch in y])
    if out_dur is not None:
        n_out = int(round(out_dur * sr))
        keep = keep if keep and keep < y.shape[-1] and keep < n_out else 0
        head, body = y[:, :keep], y[:, keep:]
        body_out = n_out - keep
        if body.shape[-1] > 0 and body_out > 0:
            rate = body.shape[-1] / body_out
            body = np.stack([librosa.effects.time_stretch(ch, rate=rate) for ch in body])
            # crossfade the seam
            xf = min(256, head.shape[-1], body.shape[-1])
            if xf:
                body[:, :xf] *= np.linspace(0, 1, xf, dtype=np.float32)
        y = np.concatenate([head, body], axis=1)
    return y.astype(np.float32)


def process(y: np.ndarray, sr: int, out_dur: float | None, semitones: float, keep: int | None = None) -> np.ndarray:
    """Stretch to out_dur seconds (pitch untouched) and/or shift pitch by semitones."""
    if find_rubberband():
        try:
            if out_dur is not None and keep and abs(semitones) > 1e-4:
                # Rubber Band ignores -p when a time map is given: shift first, then stretch.
                y = _rubberband(y, sr, None, semitones, None)
                semitones = 0.0
            return _rubberband(y, sr, out_dur, semitones, keep)
        except Exception:  # noqa: BLE001 - fall back rather than fail the render
            pass
    return _librosa(y, sr, out_dur, semitones, keep)


def render_note(clip_id: str, audio: np.ndarray, sr: int, duration: float, semitones: float = 0.0) -> np.ndarray:
    """Return stereo audio exactly `duration` seconds long for one note.

    `audio` is the trimmed clip. If the note is longer than the clip, the clip
    is time-stretched (pitch preserved, attack kept intact). If shorter, it is
    cut with a release fade.
    """
    n_out = max(1, int(round(duration * sr)))
    key = (clip_id, round(semitones, 3), n_out)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    n_src = audio.shape[-1]
    if n_out <= n_src:
        y = audio[:, :n_out].copy()
        if abs(semitones) > 1e-4:
            y = process(audio[:, : min(n_src, n_out + int(0.1 * sr))].copy(), sr, None, semitones)[:, :n_out]
    else:
        y = process(audio.copy(), sr, n_out / sr, semitones, keep=int(ATTACK * sr))
    # enforce exact length
    if y.shape[-1] < n_out:
        y = np.pad(y, ((0, 0), (0, n_out - y.shape[-1])))
    y = _envelope(np.ascontiguousarray(y[:, :n_out], dtype=np.float32), sr)
    with _cache_lock:
        _cache[key] = y
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return y


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
