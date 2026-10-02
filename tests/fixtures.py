"""Generate synthetic note clips and songs for tests."""
from __future__ import annotations

import colorsys
from pathlib import Path

import av
import numpy as np
import pretty_midi

from vsampler.notes import midi_to_freq, midi_to_name


def make_clip(path: str | Path, midi: float, seconds: float = 0.6, lead: float = 0.2,
              size=(320, 240), fps: int = 30, sr: int = 44100) -> str:
    """A clip with `lead` s of silence then a decaying tone, and a coloured video with a moving bar."""
    path = str(path)
    w, h = size
    c = av.open(path, "w")
    vs = c.add_stream("libx264", rate=fps)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    ast = c.add_stream("aac", rate=sr)
    ast.codec_context.layout = "mono"
    total = lead + seconds + 0.2
    r, g, b = colorsys.hsv_to_rgb((midi % 12) / 12, 0.7, 0.9)
    for i in range(int(total * fps)):
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = (int(b * 255), int(g * 255), int(r * 255))
        x = int((i / (total * fps)) * (w - 20))
        img[:, x:x + 20] = 255
        f = av.VideoFrame.from_ndarray(img, format="bgr24")
        f.pts = i
        for p in vs.encode(f):
            c.mux(p)
    t = np.arange(int(total * sr)) / sr
    tone = np.where(t >= lead, 0.6 * np.sin(2 * np.pi * midi_to_freq(midi) * (t - lead)) * np.exp(-(t - lead) * 1.5), 0)
    tone[t > lead + seconds] = 0
    tone = tone.astype(np.float32)
    for k in range(0, len(tone), 1024):
        seg = tone[k:k + 1024][None, :]
        af = av.AudioFrame.from_ndarray(seg, format="flt", layout="mono")
        af.sample_rate = sr
        af.pts = k
        for p in ast.encode(af):
            c.mux(p)
    for p in vs.encode():
        c.mux(p)
    for p in ast.encode():
        c.mux(p)
    c.close()
    return path


def make_midi(path: str | Path, notes: list[tuple[int, float, float]], chord: bool = False) -> str:
    """notes = [(pitch, start, end)]"""
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(0, name="Lead")
    for p, s, e in notes:
        inst.notes.append(pretty_midi.Note(100, p, s, e))
    pm.instruments.append(inst)
    drums = pretty_midi.Instrument(0, is_drum=True, name="Drums")
    drums.notes.append(pretty_midi.Note(100, 36, 0, 0.1))
    pm.instruments.append(drums)
    pm.write(str(path))
    return str(path)


def clip_name(midi: int) -> str:
    return midi_to_name(midi).replace("#", "s")


def make_long_clip(path: str | Path, notes: list[tuple[float, float, float]], hits: list[float] = (),
                   total: float | None = None, size=(160, 120), fps: int = 15, sr: int = 44100) -> str:
    """One video holding several notes [(midi, start, duration)] and noise-burst hits [time], silence between."""
    path = str(path)
    total = total or max([s + d for _, s, d in notes] + [h + 0.3 for h in hits]) + 0.5
    w, h = size
    c = av.open(path, "w")
    vs = c.add_stream("libx264", rate=fps)
    vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
    ast = c.add_stream("aac", rate=sr)
    ast.codec_context.layout = "mono"
    for i in range(int(total * fps)):
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = (int(255 * i / (total * fps)), 80, 160)
        f = av.VideoFrame.from_ndarray(img, format="bgr24")
        f.pts = i
        for p in vs.encode(f):
            c.mux(p)
    t = np.arange(int(total * sr)) / sr
    y = np.zeros_like(t)
    for midi, start, dur in notes:
        m = (t >= start) & (t < start + dur)
        tt = t[m] - start
        env = np.minimum(1.0, tt / 0.02) * np.minimum(1.0, (start + dur - t[m]) / 0.03)
        y[m] += 0.5 * env * np.sin(2 * np.pi * midi_to_freq(midi) * tt)
    rng = np.random.default_rng(1)
    for hit in hits:
        m = (t >= hit) & (t < hit + 0.12)
        tt = t[m] - hit
        y[m] += 0.8 * rng.standard_normal(m.sum()) * np.exp(-tt * 40)
    y = np.clip(y, -1, 1).astype(np.float32)
    for k in range(0, len(y), 1024):
        af = av.AudioFrame.from_ndarray(y[k:k + 1024][None, :], format="flt", layout="mono")
        af.sample_rate = sr
        af.pts = k
        for p in ast.encode(af):
            c.mux(p)
    for p in vs.encode():
        c.mux(p)
    for p in ast.encode():
        c.mux(p)
    c.close()
    return path
