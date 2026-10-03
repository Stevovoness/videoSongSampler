"""End-to-end rendering: plan -> audio mix -> composited video -> mp4."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import av
import numpy as np
import soundfile as sf

from .. import audio_dsp
from ..clips import level_gain_db, measure_loudness
from ..drums import drum_key, drum_note, is_drum_key
from ..models import ClipSlot, Project, Song
from ..planner import Plan, plan_for_project
from ..songops import apply_options
from .compositor import Compositor
from .layouts import cover
from .overlays import OverlayPainter
from .sources import AudioSource, ClipSource, get_audio_source, get_source, source_key, trimmed_audio

ProgressFn = Callable[[float, str], None]


class Cancelled(Exception):
    pass


@dataclass
class Prepared:
    plan: Plan
    sources: dict[tuple[int, int], ClipSource | AudioSource]   # (slot key, take) -> clip
    duration: float

    @property
    def tile_slots(self) -> list[int]:
        return sorted({k for k, _ in self.sources})


def _noop(_f: float, _m: str) -> None:
    pass


def _workers() -> int:
    """Parallel jobs: Rubber Band runs as separate processes and video decoding releases the GIL."""
    return max(2, min(8, (os.cpu_count() or 4)))


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def project_slot(project: Project, key: int) -> ClipSlot:
    """The clip behind an engine slot key (piano key, or drum_key(n) for a drum pad)."""
    return project.drum_slots[drum_note(key)] if is_drum_key(key) else project.slots[key]


def project_take(project: Project, key: int, take: int = 0) -> ClipSlot:
    """One take of the clip behind a slot key, as a plain ClipSlot."""
    return project_slot(project, key).take(take)


def all_slot_keys(project: Project) -> list[int]:
    return sorted(project.slots) + sorted(drum_key(n) for n in project.drum_slots)


def slot_gain_db(project: Project, slot: ClipSlot, audio: np.ndarray) -> float:
    """User volume plus automatic levelling (if on), in dB. `audio` is the trimmed clip sound."""
    gain_db = slot.gain_db
    if project.render.even_volumes:
        slot.loudness_db = measure_loudness(audio)   # of the current trim
        gain_db += level_gain_db(slot.loudness_db)
    return gain_db


def render_clip_audio(project: Project, slot: ClipSlot, shift: float = 0.0) -> np.ndarray:
    """One full hit of any clip exactly as the renderer makes it (trim, auto-tune / shift, volume levelling).

    `slot` needn't be in the project yet (the clip finder previews clips before they're added).
    """
    audio = trimmed_audio(slot)
    sr = project.render.sample_rate
    y = audio_dsp.render_note(source_key(slot), audio, sr, audio.shape[1] / sr, shift)
    y = y * (10 ** (slot_gain_db(project, slot, audio) / 20))
    peak = float(np.abs(y).max()) if y.size else 0.0
    if peak > 0.99:
        y = y * (0.99 / peak)
    return y.astype(np.float32)


def render_slot_audio(project: Project, key: int, shift: float = 0.0, take: int = 0) -> np.ndarray:
    """One take of a slot as the clickable sample pad plays it."""
    return render_clip_audio(project, project_take(project, key, take), shift)


def prepare(project: Project, song: Song, progress: ProgressFn = _noop,
            cancel: threading.Event | None = None, all_slots: bool = False, audio_only: bool = False,
            max_seconds: float | None = None) -> Prepared:
    """Plan the song and load the clips it uses.

    audio_only: load just the sound of each clip (audio previews), not its video frames.
    max_seconds: only the notes that start in the first max_seconds of the song (quick previews).
    """
    events = apply_options(song, project.song, include_drums=bool(project.drum_slots))
    plan = plan_for_project(events, project)
    if not plan.instances:
        raise ValueError("None of the song's notes have a clip. Add clips for the notes shown in red, "
                         "or turn on 'Octave jump' / 'Use nearest clip and pitch-shift'.")
    if max_seconds is not None:
        first = min(i.start for i in plan.instances)
        plan.instances = [i for i in plan.instances if i.start < first + max_seconds]
    limit = project.render.max_duration
    if limit:   # e.g. Shorts: cut the song off, notes that are still sounding stop at the limit
        plan.instances = [i for i in plan.instances if i.start < limit]
        for i in plan.instances:
            if not i.drum:
                i.duration = max(0.03, min(i.duration, limit - i.start))
    used = sorted({(i.slot, i.take) for i in plan.instances})
    if (all_slots or not project.render.only_used_clips) and not audio_only:
        used = [(k, t) for k in all_slot_keys(project) for t in range(project_slot(project, k).take_count)]
    load = get_audio_source if audio_only else get_source
    sources: dict = {}
    with ThreadPoolExecutor(max_workers=_workers()) as ex:
        futs = {ex.submit(load, project_take(project, key, take)): (key, take) for key, take in used}
        try:
            for n, fut in enumerate(as_completed(futs)):
                _check(cancel)
                progress(n / max(1, len(used)), f"Loading clip {n + 1} of {len(used)}…")
                sources[futs[fut]] = fut.result()
        except BaseException:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
    for inst in plan.instances:
        if inst.drum:   # a drum hit always plays its whole clip, as recorded (never cut short or stretched)
            inst.duration = sources[(inst.slot, inst.take)].length
    duration = max(i.start + i.duration for i in plan.instances) + project.render.tail
    if limit:
        duration = min(duration, limit + project.render.tail)
    return Prepared(plan, sources, duration)


def mix_audio(project: Project, prep: Prepared, progress: ProgressFn = _noop,
              cancel: threading.Event | None = None) -> np.ndarray:
    sr = project.render.sample_rate
    out = np.zeros((2, int(prep.duration * sr) + sr), np.float32)
    insts = prep.plan.instances
    # stretch / tune every distinct note once, several at a time
    def note_key(inst):
        return prep.sources[(inst.slot, inst.take)].key, round(inst.shift, 3), max(1, int(round(inst.duration * sr)))

    jobs = {}
    for inst in insts:
        jobs.setdefault(note_key(inst), inst)
    notes: dict = {}
    with ThreadPoolExecutor(max_workers=_workers()) as ex:
        futs = {}
        for k, inst in jobs.items():
            src = prep.sources[(inst.slot, inst.take)]
            futs[ex.submit(audio_dsp.render_note, src.key, src.audio, sr, inst.duration, inst.shift)] = k
        try:
            for n, fut in enumerate(as_completed(futs)):
                _check(cancel)
                if n % 4 == 0:
                    progress(n / max(1, len(futs)), f"Making note {n + 1} of {len(futs)}…")
                notes[futs[fut]] = fut.result()
        except BaseException:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
    for inst in insts:
        src = prep.sources[(inst.slot, inst.take)]
        y = notes[note_key(inst)]
        gain = 10 ** (slot_gain_db(project, project_take(project, inst.slot, inst.take), src.audio) / 20)
        if project.render.velocity_volume:
            gain *= 0.35 + 0.65 * inst.velocity
        a = int(inst.start * sr)
        b = min(out.shape[1], a + y.shape[1])
        out[:, a:b] += y[:, : b - a] * gain
    out = out[:, : int(prep.duration * sr)]
    peak = float(np.abs(out).max()) if out.size else 0.0
    if peak > 0:
        out *= 0.89 / peak
    if project.render.loudness_db is not None:
        out = set_loudness(out, project.render.loudness_db, sr)
    return out


def set_loudness(audio: np.ndarray, target_db: float, sr: int) -> np.ndarray:
    """Bring audio to target_db (dBFS RMS of its sound), softly limiting peaks so it never clips."""
    loud = measure_loudness(audio, sr)
    if loud is None:
        return audio
    y = audio * np.float32(10 ** ((target_db - loud) / 20))
    knee = np.float32(0.9)
    over = np.abs(y) > knee
    if over.any():
        a = np.abs(y[over])
        y[over] = np.sign(y[over]) * (knee + (1 - knee) * np.tanh((a - knee) / (1 - knee)))
    return y.astype(np.float32)


PREVIEW_FADE = 0.4


def render_audio_preview(project: Project, song: Song, wav_path: str, progress: ProgressFn = _noop,
                         cancel: threading.Event | None = None, max_seconds: float | None = None) -> str:
    """The song's sound with the user's clips (no video), or just its first max_seconds."""
    prep = prepare(project, song, lambda f, m: progress(f * 0.1, m), cancel, audio_only=True,
                   max_seconds=max_seconds)
    if max_seconds is not None:
        first = min(i.start for i in prep.plan.instances)
        prep.duration = min(prep.duration, first + max_seconds + PREVIEW_FADE)
    audio = mix_audio(project, prep, lambda f, m: progress(0.1 + f * 0.9, m), cancel)
    if max_seconds is not None:   # fade out where the preview stops
        n = min(audio.shape[1], int(PREVIEW_FADE * project.render.sample_rate))
        if n:
            audio[:, -n:] *= np.linspace(1, 0, n, dtype=np.float32)
    sf.write(wav_path, audio.T, project.render.sample_rate)
    return wav_path


def _outro_source(project: Project) -> ClipSource | None:
    """The video played after the song (RenderSettings.outro), if there is one."""
    return get_source(ClipSlot(project.render.outro, 0.0, None)) if project.render.outro else None


def preview_frame(project: Project, song: Song, t: float) -> np.ndarray:
    prep = prepare(project, song)
    outro = _outro_source(project)
    total = prep.duration + (outro.length if outro else 0.0)
    comp = Compositor(prep.plan.instances, prep.sources, prep.tile_slots, project.render)
    return OverlayPainter(project.render, total).apply(_frame(comp, outro, prep.duration, project.render, t), t)


def _frame(comp: Compositor, outro: ClipSource | None, song_end: float, rs, t: float) -> np.ndarray:
    if outro is None or t < song_end:
        return comp.frame(t)
    return cover(outro.frames.at(outro.trim_start + min(t - song_end, outro.length)), rs.width, rs.height)


def render_video(project: Project, song: Song, out_path: str, progress: ProgressFn = _noop,
                 cancel: threading.Event | None = None) -> str:
    rs = project.render
    W, H = rs.width // 2 * 2, rs.height // 2 * 2
    prep = prepare(project, song, lambda f, m: progress(f * 0.1, m), cancel)
    audio = mix_audio(project, prep, lambda f, m: progress(0.1 + f * 0.3, m), cancel)
    comp = Compositor(prep.plan.instances, prep.sources, prep.tile_slots, rs)
    sr = rs.sample_rate
    song_end = audio.shape[1] / sr
    outro = _outro_source(project)
    if outro is not None:   # played as recorded, at the same loudness as the song
        target = rs.loudness_db if rs.loudness_db is not None else measure_loudness(audio, sr)
        extra = outro.audio if target is None else set_loudness(outro.audio, target, sr)
        audio = np.concatenate([audio, extra.astype(np.float32)], axis=1)
    total = audio.shape[1] / sr
    painter = OverlayPainter(rs, total)

    n_frames = int(np.ceil(total * rs.fps))
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
            img = painter.apply(_frame(comp, outro, song_end, rs, t), t)
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
