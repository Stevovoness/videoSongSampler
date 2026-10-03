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


def default_tracks(song: Song, include_drums: bool = False) -> list[int]:
    """Tracks played when the user hasn't picked any: all pitched parts, plus drums if there are drum clips."""
    tracks = [t.index for t in song.tracks if include_drums or not t.is_drum]
    return tracks or [t.index for t in song.tracks]


def enabled_tracks(song: Song, opts: SongOptions, include_drums: bool = False) -> set[int]:
    return set(opts.enabled_tracks if opts.enabled_tracks is not None else default_tracks(song, include_drums))


def apply_options(song: Song, opts: SongOptions, include_drums: bool = False) -> list[NoteEvent]:
    """Events to play. Transpose and melody-only apply to pitched notes only; speed applies to everything.

    With `opts.start_s` / `opts.end_s`, only notes starting in that part of the song play (cut off at its end).
    """
    enabled = enabled_tracks(song, opts, include_drums)
    scale = 100.0 / max(1.0, opts.tempo_pct)
    a = max(0.0, opts.start_s or 0.0)
    b = opts.end_s if opts.end_s is not None else float("inf")
    evs = [
        NoteEvent((e.start - a) * scale, (min(e.end, b) - a) * scale, e.pitch + (0 if e.drum else opts.transpose),
                  e.velocity, e.track, e.drum)
        for e in song.events if e.track in enabled and a <= e.start < b
    ]
    evs = [e for e in evs if 0 <= e.pitch <= 127]
    if opts.melody_only:
        evs = skyline([e for e in evs if not e.drum]) + [e for e in evs if e.drum]
    if evs:
        # start the video right away rather than with a long silence
        offset = min(e.start for e in evs)
        if offset > 0.5:
            evs = [NoteEvent(e.start - offset + 0.25, e.end - offset + 0.25, e.pitch, e.velocity, e.track, e.drum)
                   for e in evs]
    evs.sort(key=lambda e: (e.start, e.drum, e.pitch))
    return evs


def suggest_transpose(song: Song, opts: SongOptions, available: set[int]) -> int:
    """Transposition (-24..24) that lets the most notes use a clip directly (drums aren't transposed)."""
    if not available:
        return 0
    enabled = enabled_tracks(song, opts)
    pitches = [e.pitch for e in song.events if e.track in enabled and not e.drum]
    if not pitches:
        return 0
    best, best_score = 0, -1.0
    for t in range(-24, 25):
        score = sum(1 for p in pitches if p + t in available) - abs(t) * 0.01  # prefer small shifts
        if score > best_score:
            best, best_score = t, score
    return best
