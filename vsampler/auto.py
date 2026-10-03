"""Build a whole project with no one at the controls: long videos + a song in, a ready-to-render project out.

`auto_project` runs the clip finder over each video, puts the best clip of every note on its key (more good ones
become takes), picks the key change that lets the song use the most clips, and reports how much of the song
the clips cover, so a pipeline can reject a poor build before spending time rendering it.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import clipfinder
from .clips import SR, load_audio
from .media import split_video
from .models import ClipSlot, Project, RenderSettings, Song, SongOptions, Take
from .notes import midi_to_name
from .planner import make_plan, plan_for_project
from .songops import apply_options

ProgressFn = Callable[[float, str], None]

DRUM_PADS = (36, 38, 42)   # kick, snare, closed hi-hat: the loudest hits first


@dataclass
class AutoOptions:
    min_confidence: float = 0.3      # ignore weaker clip-finder suggestions
    max_takes: int = 4               # clips kept per note (they take turns)
    max_fragment: float | None = None  # cut every clip to at most this many seconds
    melody_only: bool = True         # play just the tune's top line
    drums: bool = True               # put percussive hits on drum pads when the song has drums
    max_shift: int = 5               # furthest a clip may be pitch-shifted to fill a missing note
    transpose: int | None = None     # None: choose automatically
    voice: bool = True               # sources are speech: listen over the speaking-voice range (much faster)
    workers: int = 0                 # processes for pitch tracking (0: one per CPU core)
    work_dir: str = ""               # where long videos' pieces and saved analyses go ("" : the app's cache)


@dataclass
class ClipUse:
    """Where one clip in the project came from (for checking sources and answering disputes)."""
    path: str
    start: float
    end: float
    key: str                         # e.g. "C4 take 2" or "drum 38 take 1"


@dataclass
class AutoReport:
    coverage: float = 0.0            # share of the song's notes played by a clip (0..1, exact or octave counts fully)
    notes: int = 0
    exact: int = 0
    octave: int = 0
    borrowed: int = 0
    missing: int = 0
    transpose: int = 0
    clip_notes: list[int] = field(default_factory=list)   # MIDI notes that have a clip
    clips: list[ClipUse] = field(default_factory=list)
    longest_clip: float = 0.0
    duration: float = 0.0            # seconds of song that will play

    def to_dict(self) -> dict:
        return asdict(self)


def _noop(_f: float, _m: str) -> None:
    pass


def _work_dir(opts: AutoOptions) -> Path:
    if opts.work_dir:
        return Path(opts.work_dir)
    from .paths import cache_dir
    return cache_dir() / "auto"


def _saved_analysis(piece: str, rng: tuple[float, float]) -> Path:
    return Path(piece + f".clips-{int(rng[0])}-{int(rng[1])}.json")


def find_clips(videos: list[str], progress: ProgressFn = _noop, cancel: threading.Event | None = None,
               opts: AutoOptions | None = None) -> list[tuple[str, clipfinder.Candidate]]:
    """Every clip-finder suggestion in every video, as (video file, candidate).

    Long videos are cut into ~10-minute pieces first (`media.split_video`), and the returned clips point into
    the pieces. Each piece's analysis is saved next to it, so running again (e.g. with another song) is instant.
    """
    opts = opts or AutoOptions()
    rng = clipfinder.VOICE_RANGE if opts.voice else clipfinder.FULL_RANGE
    workers = opts.workers or max(1, (os.cpu_count() or 2) - 1)
    pieces = [piece for v in videos for piece in split_video(v, _work_dir(opts) / "pieces")]
    found: list[tuple[str, clipfinder.Candidate]] = []
    for n, piece in enumerate(pieces):
        def prog(f: float, m: str, n=n) -> None:
            progress((n + f) / len(pieces), f"Part {n + 1} of {len(pieces)}: {m}")

        saved = _saved_analysis(piece, rng)
        st = os.stat(piece)
        stamp = f"{st.st_size}|{st.st_mtime_ns}"
        data = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
        if data.get("stamp") == stamp:
            cands = [clipfinder.Candidate(**c) for c in data["clips"]]
        else:
            audio = load_audio(piece)
            limit = clipfinder.MAX_MINUTES * 60 * SR
            res = clipfinder.VOICE_RESOLUTION if opts.voice else 0.1
            cands = clipfinder.analyse(audio[:, :limit], SR, prog, cancel, rng, workers, res).candidates
            try:
                saved.write_text(json.dumps({"stamp": stamp, "clips": [asdict(c) for c in cands]}), encoding="utf-8")
            except OSError:
                pass   # e.g. a read-only folder: just don't keep it
        found += [(piece, c) for c in cands]
    return found


def build_slots(found: list[tuple[str, clipfinder.Candidate]], opts: AutoOptions
                ) -> tuple[dict[int, ClipSlot], dict[int, ClipSlot]]:
    """Note slots and drum pads from clip-finder suggestions: the most confident clip first, then takes."""
    def cut(c: clipfinder.Candidate) -> tuple[float, float]:
        end = c.end if opts.max_fragment is None else min(c.end, c.start + opts.max_fragment)
        return c.start, end

    def slot_from(group: list[tuple[str, clipfinder.Candidate]], pitched: bool) -> ClipSlot:
        group = sorted(group, key=lambda pc: -pc[1].confidence)[: max(1, opts.max_takes)]
        takes = [Take(p, *cut(c), c.midi if pitched else None, c.loudness_db) for p, c in group]
        t0 = takes[0]
        return ClipSlot(t0.path, t0.trim_start, t0.trim_end, t0.detected_midi, autotune=pitched,
                        loudness_db=t0.loudness_db, extra_takes=takes[1:])

    by_note: dict[int, list] = {}
    hits = []
    for path, c in found:
        if c.confidence < opts.min_confidence:
            continue
        if c.kind == "note" and c.note is not None:
            by_note.setdefault(c.note, []).append((path, c))
        elif c.kind == "hit":
            hits.append((path, c))
    slots = {n: slot_from(g, True) for n, g in by_note.items()}
    drum_slots: dict[int, ClipSlot] = {}
    if opts.drums and hits:
        hits.sort(key=lambda pc: -(pc[1].loudness_db if pc[1].loudness_db is not None else -99))
        per = max(1, len(hits) // len(DRUM_PADS))
        for i, pad in enumerate(DRUM_PADS):
            group = hits[i * per:(i + 1) * per]
            if group:
                drum_slots[pad] = slot_from(group, False)
    return slots, drum_slots


def melody_track(song: Song) -> int | None:
    """The track most likely to hold the tune: the highest-sounding pitched track with a fair number of notes."""
    tracks = [t for t in song.tracks if not t.is_drum and t.note_count >= 8]
    if len(tracks) < 2:
        return None
    def mean_pitch(i: int) -> float:
        ps = [e.pitch for e in song.events if e.track == i]
        return sum(ps) / len(ps) if ps else 0.0
    return max(tracks, key=lambda t: mean_pitch(t.index)).index


def clip_centre(slots: dict[int, ClipSlot]) -> float:
    """Middle pitch of the clips, weighted by how many takes each note has (where the voice mostly sits)."""
    keys = sorted(slots)
    weights = [slots[k].take_count for k in keys]
    half, run = sum(weights) / 2, 0.0
    for k, w in zip(keys, weights):
        run += w
        if run >= half:
            return float(k)
    return float(keys[-1])


def best_transpose(song: Song, opts: SongOptions, slots: dict[int, ClipSlot]) -> int:
    """The key change (-24..24) under which the clips play the most of the song, counting octave jumps
    and short pitch-shifts as partly good.

    When several key changes play the song equally well, the one that puts the tune nearest the middle of the
    clips wins: that's where the voice has the most takes (high outliers are often instruments, not the voice).
    """
    events = apply_options(song, SongOptions(**{**asdict(opts), "transpose": 0}))
    events = [e for e in events if not e.drum]
    if not events or not slots:
        return 0
    pitches = sorted(e.pitch for e in events)
    tune_mid = pitches[len(pitches) // 2]
    centre = clip_centre(slots)
    best, best_score = 0, float("-inf")
    for t in range(-24, 25):
        for e in events:
            e.pitch += t
        plan = make_plan(events, slots, opts.allow_pitch_shift, opts.max_shift,
                         octave_jump=opts.octave_jump, octave_max=opts.octave_max)
        for e in events:
            e.pitch -= t
        counts = {"exact": 0, "octave": 0, "borrowed": 0}
        for i in plan.instances:
            counts["exact" if i.target == i.slot else "octave" if (i.target - i.slot) % 12 == 0 else "borrowed"] += 1
        # tie-break: worth about one note per octave away from the clips' middle
        off_centre = abs(tune_mid + t - centre) / 12 * max(1.0, len(events) / 50)
        score = counts["exact"] + 0.9 * counts["octave"] + 0.5 * counts["borrowed"] - off_centre
        if score > best_score:
            best, best_score = t, score
    return best


def report_for(project: Project, song: Song) -> AutoReport:
    events = apply_options(song, project.song, include_drums=bool(project.drum_slots))
    plan = plan_for_project(events, project)
    notes = [e for e in events if not e.drum]
    r = AutoReport(notes=len(notes), transpose=project.song.transpose, clip_notes=sorted(project.slots))
    for i in plan.note_instances:
        if i.target == i.slot:
            r.exact += 1
        elif (i.target - i.slot) % 12 == 0:
            r.octave += 1
        else:
            r.borrowed += 1
    r.missing = sum(plan.missing.values())
    r.coverage = round((r.exact + r.octave + 0.5 * r.borrowed) / r.notes, 3) if r.notes else 0.0
    limit = project.render.max_duration
    end = max((e.end for e in events), default=0.0)
    r.duration = round(min(end, limit) if limit else end, 2)
    for label, slots in ((midi_to_name, project.slots), (lambda n: f"drum {n}", project.drum_slots)):
        for n, slot in sorted(slots.items()):
            for k in range(slot.take_count):
                t = slot.take(k)
                end_t = t.trim_end if t.trim_end is not None else t.trim_start
                r.clips.append(ClipUse(t.path, round(t.trim_start, 3), round(end_t, 3), f"{label(n)} take {k + 1}"))
                r.longest_clip = max(r.longest_clip, round(end_t - t.trim_start, 3))
    return r


def auto_project(videos: list[str], song_path: str, opts: AutoOptions | None = None,
                 song_opts: SongOptions | None = None, render: RenderSettings | None = None,
                 progress: ProgressFn = _noop, cancel: threading.Event | None = None,
                 found: list[tuple[str, clipfinder.Candidate]] | None = None) -> tuple[Project, Song, AutoReport]:
    """A ready-to-render project that plays `song_path` with clips cut from `videos`.

    `found` skips the (slow) clip finder when the videos have already been analysed.
    """
    from .importers import load_song

    opts = opts or AutoOptions()
    if found is None:
        found = find_clips(videos, lambda f, m: progress(f * 0.9, m), cancel, opts)
    progress(0.9, "Choosing clips…")
    project = Project(render=render or RenderSettings())
    project.slots, project.drum_slots = build_slots(found, opts)
    if not project.slots:
        raise ValueError("No clearly pitched sounds were found in these videos, so there is nothing to sing with.")
    project.song_path = song_path
    so = song_opts or SongOptions()
    so.melody_only = opts.melody_only
    so.octave_jump = True
    so.allow_pitch_shift = True
    so.max_shift = opts.max_shift
    project.song = so
    song = load_song(song_path, so)
    has_drums = any(t.is_drum for t in song.tracks)
    if not has_drums:
        project.drum_slots = {}
    if so.enabled_tracks is None and opts.melody_only:
        tune = melody_track(song)
        if tune is not None:   # just the tune (plus drums), not a piano part's bass line
            so.enabled_tracks = [tune] + [t.index for t in song.tracks if t.is_drum and project.drum_slots]
    so.transpose = opts.transpose if opts.transpose is not None else best_transpose(song, so, project.slots)
    progress(1.0, "Done")
    return project, song, report_for(project, song)
