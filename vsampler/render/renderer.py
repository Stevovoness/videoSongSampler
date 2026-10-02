"""End-to-end rendering: plan -> audio mix -> composited video -> mp4."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import av
import numpy as np
import soundfile as sf

from .. import audio_dsp
from ..models import Project, Song
from ..planner import Plan, make_plan
from ..songops import apply_options
from .compositor import Compositor
from .sources import ClipSource, get_source

ProgressFn = Callable[[float, str], None]


class Cancelled(Exception):
    pass


@dataclass
class Prepared:
    plan: Plan
    sources: dict[int, ClipSource]
    duration: float


def _noop(_f: float, _m: str) -> None:
    pass


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def prepare(project: Project, song: Song, progress: ProgressFn = _noop,
            cancel: threading.Event | None = None, all_slots: bool = False) -> Prepared:
    events = apply_options(song, project.song)
    plan = make_plan(events, project.slots, project.song.allow_pitch_shift, project.song.max_shift)
    if not plan.instances:
        raise ValueError("None of the song's notes have a clip. Add clips for the notes shown in red, "
                         "or turn on 'Use nearest clip and pitch-shift'.")
    used = sorted({i.slot for i in plan.instances})
    if all_slots or not project.render.only_used_clips:
        used = sorted(project.slots)
    sources: dict[int, ClipSource] = {}
    for n, slot in enumerate(used):
        _check(cancel)
        progress(n / max(1, len(used)), f"Loading clip {n + 1} of {len(used)}…")
        sources[slot] = get_source(project.slots[slot])
    duration = max(i.start + i.duration for i in plan.instances) + project.render.tail
    return Prepared(plan, sources, duration)


def mix_audio(project: Project, prep: Prepared, progress: ProgressFn = _noop,
              cancel: threading.Event | None = None) -> np.ndarray:
    sr = project.render.sample_rate
    out = np.zeros((2, int(prep.duration * sr) + sr), np.float32)
    insts = prep.plan.instances
    for n, inst in enumerate(insts):
        _check(cancel)
        if n % 8 == 0:
            progress(n / max(1, len(insts)), f"Making note {n + 1} of {len(insts)}…")
        src = prep.sources[inst.slot]
        y = audio_dsp.render_note(src.key, src.audio, sr, inst.duration, inst.shift)
        gain = 10 ** (project.slots[inst.slot].gain_db / 20)
        if project.render.velocity_volume:
            gain *= 0.35 + 0.65 * inst.velocity
        a = int(inst.start * sr)
        b = min(out.shape[1], a + y.shape[1])
        out[:, a:b] += y[:, : b - a] * gain
    out = out[:, : int(prep.duration * sr)]
    peak = float(np.abs(out).max()) if out.size else 0.0
    if peak > 0:
        out *= 0.89 / peak
    return out


def render_audio_preview(project: Project, song: Song, wav_path: str, progress: ProgressFn = _noop,
                         cancel: threading.Event | None = None) -> str:
    prep = prepare(project, song, lambda f, m: progress(f * 0.3, m), cancel)
    audio = mix_audio(project, prep, lambda f, m: progress(0.3 + f * 0.7, m), cancel)
    sf.write(wav_path, audio.T, project.render.sample_rate)
    return wav_path


def preview_frame(project: Project, song: Song, t: float) -> np.ndarray:
    prep = prepare(project, song)
    comp = Compositor(prep.plan.instances, prep.sources, sorted(prep.sources), project.render)
    return comp.frame(t)


def render_video(project: Project, song: Song, out_path: str, progress: ProgressFn = _noop,
                 cancel: threading.Event | None = None) -> str:
    rs = project.render
    W, H = rs.width // 2 * 2, rs.height // 2 * 2
    prep = prepare(project, song, lambda f, m: progress(f * 0.1, m), cancel)
    audio = mix_audio(project, prep, lambda f, m: progress(0.1 + f * 0.3, m), cancel)
    comp = Compositor(prep.plan.instances, prep.sources, sorted(prep.sources), rs)

    sr = rs.sample_rate
    n_frames = int(np.ceil(prep.duration * rs.fps))
    container = av.open(out_path, mode="w")
    try:
        vs = container.add_stream("libx264", rate=rs.fps)
        vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
        vs.options = {"crf": "18", "preset": "veryfast"}
        ast = container.add_stream("aac", rate=sr)
        ast.codec_context.layout = "stereo"
        ast.bit_rate = 192000

        chunk = 1024
        a_pos = 0
        total_a = audio.shape[1]

        def push_audio(until: int) -> None:
            nonlocal a_pos
            while a_pos < min(until, total_a):
                seg = np.ascontiguousarray(audio[:, a_pos: a_pos + chunk])
                af = av.AudioFrame.from_ndarray(seg, format="fltp", layout="stereo")
                af.sample_rate = sr
                af.pts = a_pos
                for p in ast.encode(af):
                    container.mux(p)
                a_pos += seg.shape[1]

        for i in range(n_frames):
            _check(cancel)
            t = i / rs.fps
            img = comp.frame(t)
            if img.shape[1] != W or img.shape[0] != H:
                img = np.ascontiguousarray(img[:H, :W])
            vf = av.VideoFrame.from_ndarray(img, format="bgr24")
            vf.pts = i
            for p in vs.encode(vf):
                container.mux(p)
            push_audio(int((t + 1.0 / rs.fps) * sr))
            if i % 5 == 0:
                progress(0.4 + 0.6 * i / n_frames, f"Rendering video frame {i + 1} of {n_frames}…")
        push_audio(total_a)
        for p in vs.encode():
            container.mux(p)
        for p in ast.encode():
            container.mux(p)
    finally:
        container.close()
    progress(1.0, "Done!")
    return out_path
