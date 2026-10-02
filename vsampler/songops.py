"""Transformations applied to an imported song before rendering."""
from __future__ import annotations

from .models import NoteEvent, Song, SongOptions


def skyline(events: list[NoteEvent], chord_window: float = 0.03) -> list[NoteEvent]:
    """Keep only the top melody line: highest note of each onset, cut off by the next one."""
    evs = sorted(events, key=lambda e: (e.start, -e.pitch))
    kept: list[NoteEvent] = []
    for e in evs:
        if kept and e.start - kept[-1].start < chord_window:
            if e.pitch > kept[-1].pitch:
                kept[-1] = e
            continue
        if kept and e.start < kept[-1].end and e.pitch < kept[-1].pitch and e.end <= kept[-1].end:
            continue  # lower note hidden under a sustained melody note
        kept.append(e)
    out = []
    for i, e in enumerate(kept):
        end = e.end
        if i + 1 < len(kept):
            end = min(end, kept[i + 1].start)
        if end - e.start > 0.01:
            out.append(NoteEvent(e.start, end, e.pitch, e.velocity, e.track))
    return out


def default_tracks(song: Song) -> list[int]:
    tracks = [t.index for t in song.tracks if not t.is_drum]
    return tracks or [t.index for t in song.tracks]


def apply_options(song: Song, opts: SongOptions) -> list[NoteEvent]:
    enabled = set(opts.enabled_tracks if opts.enabled_tracks is not None else default_tracks(song))
    scale = 100.0 / max(1.0, opts.tempo_pct)
    evs = [
        NoteEvent(e.start * scale, e.end * scale, e.pitch + opts.transpose, e.velocity, e.track)
        for e in song.events if e.track in enabled
    ]
    evs = [e for e in evs if 0 <= e.pitch <= 127]
    if opts.melody_only:
        evs = skyline(evs)
    if evs:
        # start the video right away rather than with a long silence
        offset = min(e.start for e in evs)
        if offset > 0.5:
            evs = [NoteEvent(e.start - offset + 0.25, e.end - offset + 0.25, e.pitch, e.velocity, e.track) for e in evs]
    evs.sort(key=lambda e: (e.start, e.pitch))
    return evs


def suggest_transpose(song: Song, opts: SongOptions, available: set[int]) -> int:
    """Transposition (-24..24) that lets the most notes use a clip directly."""
    if not available:
        return 0
    enabled = set(opts.enabled_tracks if opts.enabled_tracks is not None else default_tracks(song))
    pitches = [e.pitch for e in song.events if e.track in enabled]
    if not pitches:
        return 0
    best, best_score = 0, -1.0
    for t in range(-24, 25):
        score = sum(1 for p in pitches if p + t in available) - abs(t) * 0.01  # prefer small shifts
        if score > best_score:
            best, best_score = t, score
    return best
