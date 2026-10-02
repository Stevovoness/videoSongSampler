"""Clip finder: scan a long video's sound and suggest every clip point in it.

`analyse` tracks pitch through the whole recording and returns a `Candidate` for every held, clearly pitched
note (often several takes of the same note) and for every percussive hit. The GUI lets the user tick, move and
resize these and turns them into clip slots that point into the long video.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .clips import SR, detect_pitch, measure_loudness

MAX_MINUTES = 30          # the whole audio track is decoded into memory (~21 MB per minute)
LONG_VIDEO_SECONDS = 10   # longer videos go to the clip finder instead of being one clip

_FR = 22050               # analysis sample rate
_HOP = 256                # ~11.6 ms per analysis frame
_CHUNK_S = 30             # pYIN runs in chunks so it can report progress and be cancelled

MIN_NOTE = 0.12           # shortest note worth suggesting (seconds)
MAX_NOTE = 2.0            # longest suggested clip; longer held notes are cut to this
PITCH_TOL = 0.5           # semitones a note may wander from its running median
MAX_HIT = 0.6

ProgressFn = Callable[[float, str], None]


class FinderCancelled(Exception):
    pass


@dataclass
class Candidate:
    start: float                 # seconds in the long video
    end: float
    midi: float | None           # heard pitch (fractional MIDI); None for hits
    kind: str = "note"           # note | hit
    confidence: float = 0.0      # 0..1
    loudness_db: float | None = None

    @property
    def note(self) -> int | None:
        return None if self.midi is None else int(round(self.midi))

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class FinderResult:
    duration: float
    peaks: np.ndarray            # waveform overview, 0..1, evenly spread over the duration
    times: np.ndarray            # analysis frame times (s)
    midi: np.ndarray             # pitch per frame, NaN where there's no clear pitch
    prob: np.ndarray             # voicing probability per frame
    candidates: list[Candidate]


def _noop(_f: float, _m: str) -> None:
    pass


def _overview(mono: np.ndarray, n: int = 4000) -> np.ndarray:
    n = max(1, min(n, len(mono)))
    usable = len(mono) // n * n
    if usable == 0:
        return np.zeros(1, np.float32)
    peaks = np.abs(mono[:usable]).reshape(n, -1).max(axis=1)
    top = peaks.max()
    return (peaks / top).astype(np.float32) if top > 0 else peaks.astype(np.float32)


def _pitch_track(y: np.ndarray, progress: ProgressFn, cancel: threading.Event | None):
    import librosa

    n = 1 + len(y) // _HOP
    f0 = np.full(n, np.nan)
    prob = np.zeros(n)
    chunk = _CHUNK_S * _FR // _HOP * _HOP
    pad = 2048 // _HOP * _HOP
    starts = list(range(0, len(y), chunk))
    for ci, s in enumerate(starts):
        if cancel is not None and cancel.is_set():
            raise FinderCancelled()
        progress(0.05 + 0.8 * ci / len(starts), f"Listening for notes… {int(100 * ci / len(starts))}%")
        a = max(0, s - pad)
        seg = y[a: s + chunk + pad]
        if len(seg) < 2048:
            continue
        cf0, _v, cp = librosa.pyin(seg, fmin=60.0, fmax=4200.0, sr=_FR, frame_length=2048, hop_length=_HOP)
        g0 = a // _HOP
        lo, hi = s // _HOP, min(n, (s + chunk) // _HOP)
        for i in range(len(cf0)):
            g = g0 + i
            if lo <= g < hi:
                f0[g], prob[g] = cf0[i], cp[i]
    return f0, prob


def analyse(audio: np.ndarray, sr: int = SR, progress: ProgressFn = _noop,
            cancel: threading.Event | None = None) -> FinderResult:
    """Suggest every note and hit clip point in a recording (stereo or mono audio at `sr`)."""
    import librosa

    duration = audio.shape[-1] / sr
    if duration > MAX_MINUTES * 60:
        raise ValueError(f"This video is {duration / 60:.0f} minutes long. The clip finder works on videos up to "
                         f"{MAX_MINUTES} minutes; cut out the part you need first.")
    mono = audio.mean(axis=0) if audio.ndim == 2 else audio
    progress(0.0, "Reading the sound…")
    y = librosa.resample(mono.astype(np.float32), orig_sr=sr, target_sr=_FR)
    f0, prob = _pitch_track(y, progress, cancel)
    n = len(f0)
    progress(0.88, "Finding the clip points…")
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=_HOP)[0]
    rms = np.pad(rms, (0, max(0, n - len(rms))))[:n]
    env = librosa.onset.onset_strength(y=y, sr=_FR, hop_length=_HOP)
    env = np.pad(env, (0, max(0, n - len(env))))[:n]
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=_FR, hop_length=_HOP, units="frames")
    times = np.arange(n) * _HOP / _FR
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69.0 + 12.0 * np.log2(f0 / 440.0)
    positive = rms[rms > 0]
    ref = np.percentile(positive, 99) if positive.size else 0.0
    loud = rms > max(ref * 10 ** (-35 / 20), 1e-5)
    ok = np.isfinite(midi) & (prob > 0.5) & loud
    midi_curve = np.where(np.isfinite(midi) & (prob > 0.3) & loud, midi, np.nan)

    notes = _find_notes(audio, sr, times, midi, prob, rms, ok, onsets, ref)
    hits = _find_hits(audio, sr, times, ok, rms, env, onsets, notes)
    cands = sorted(notes + hits, key=lambda c: c.start)
    progress(1.0, "Done")
    return FinderResult(duration, _overview(mono), times, midi_curve, prob, cands)


def _find_notes(audio, sr, times, midi, prob, rms, ok, onsets, ref) -> list[Candidate]:
    n = len(times)
    fps = _FR / _HOP
    is_onset = np.zeros(n, bool)
    is_onset[onsets[onsets < n]] = True
    min_frames, max_frames = int(MIN_NOTE * fps), int(MAX_NOTE * fps)
    out: list[Candidate] = []
    i = 0
    while i < n:
        if not ok[i]:
            i += 1
            continue
        run = [i]
        gap = 0
        j = i + 1
        while j < n:
            if ok[j] and abs(midi[j] - np.median(midi[run])) < PITCH_TOL:
                # a new attack on the same pitch starts a new take
                if is_onset[j] and len(run) >= min_frames and gap == 0 and j - run[0] > min_frames:
                    break
                run.append(j)
                gap = 0
            elif gap < 2 and (not ok[j]):    # tolerate a flicker of lost pitch
                gap += 1
            else:
                break
            j += 1
        i0, i1 = run[0], run[-1] + 1
        nxt = j
        if i1 - i0 > max_frames:
            # a long held note: suggest its first MAX_NOTE seconds, then skip the rest of it
            i1 = i0 + max_frames
        if i1 - i0 >= min_frames:
            sel = np.arange(i0, i1)
            sel = sel[ok[sel]]
            m, w = midi[sel], rms[sel] * prob[sel]
            order = np.argsort(m)
            cw = np.cumsum(w[order])
            med = float(m[order][np.searchsorted(cw, cw[-1] / 2)]) if cw[-1] > 0 else float(np.median(m))
            stability = 1.0 - min(1.0, float(np.std(m)) / PITCH_TOL)
            t0 = times[i0] - 0.03
            near = onsets[(onsets >= i0 - 8) & (onsets <= i0)]
            if near.size:
                t0 = min(t0, times[near[0]] - 0.01)
            t0 = max(0.0, t0)
            t1 = min(audio.shape[-1] / sr, times[i1 - 1] + 0.05 + _HOP / _FR)
            loud_db = measure_loudness(audio[..., int(t0 * sr): int(t1 * sr)], sr)
            loud_f = 0.0 if loud_db is None else float(np.clip((loud_db + 50) / 30, 0, 1))
            len_f = min(1.0, (t1 - t0) / 0.35)
            conf = float(np.mean(prob[sel])) * (0.5 + 0.5 * stability) * (0.6 + 0.4 * len_f) * (0.5 + 0.5 * loud_f)
            out.append(Candidate(t0, t1, med, "note", round(min(1.0, conf), 3), loud_db))
        i = max(nxt, i + 1)
    return out


def _find_hits(audio, sr, times, ok, rms, env, onsets, notes) -> list[Candidate]:
    n = len(times)
    if not len(onsets):
        return []
    strong = np.percentile(env[onsets], 50) * 0.6
    env_ref = max(1e-9, float(np.percentile(env[onsets], 95)))
    look = int(0.15 * _FR / _HOP)
    out: list[Candidate] = []
    for o in onsets:
        if o >= n or env[o] < strong:
            continue
        t = times[o]
        if any(c.start - 0.05 <= t <= c.end for c in notes):
            continue
        window = slice(o, min(n, o + look))
        if np.mean(ok[window]) > 0.4:          # mostly pitched: that's a note, not a hit
            continue
        pk = o + int(np.argmax(rms[o: min(n, o + 6)]))
        peak = rms[pk]
        if peak <= 0:
            continue
        end = pk
        limit = min(n, o + int(MAX_HIT * _FR / _HOP))
        while end < limit and rms[end] > peak * 10 ** (-30 / 20):
            end += 1
        t0, t1 = max(0.0, t - 0.01), min(audio.shape[-1] / sr, times[min(end, n - 1)] + 0.03)
        if t1 - t0 < 0.05:
            continue
        if out and t0 < out[-1].end:          # keep hits from overlapping
            continue
        loud_db = measure_loudness(audio[..., int(t0 * sr): int(t1 * sr)], sr)
        out.append(Candidate(t0, t1, None, "hit", round(float(min(1.0, env[o] / env_ref)), 3), loud_db))
    return out


def best_takes(cands: list[Candidate]) -> set[int]:
    """Indexes of the best take of each note (by confidence), plus every hit."""
    best: dict[int, int] = {}
    for i, c in enumerate(cands):
        if c.kind == "note" and c.note is not None:
            if c.note not in best or c.confidence > cands[best[c.note]].confidence:
                best[c.note] = i
    return set(best.values()) | {i for i, c in enumerate(cands) if c.kind == "hit"}


def refine(audio: np.ndarray, sr: int, start: float, end: float) -> tuple[float | None, float, float | None]:
    """Pitch, confidence and loudness of a clip the user moved or resized."""
    seg = audio[..., int(max(0.0, start) * sr): int(end * sr)]
    if seg.shape[-1] < int(0.05 * sr):
        return None, 0.0, None
    midi, conf = detect_pitch(seg, sr)
    return midi, conf, measure_loudness(seg, sr)
