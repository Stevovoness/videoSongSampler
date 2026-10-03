"""Songs: finding the file for a song title, and finding its catchy part (the chorus) automatically."""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from vsampler.auto import melody_track
from vsampler.models import Song, SongOptions

SONG_TYPES = (".mid", ".midi", ".mxl", ".musicxml", ".xml")
PHRASE_NOTES = 7          # notes in the pattern looked for when finding the most repeated phrase


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_file(title: str, dirs: list[Path], artist: str = "") -> Path | None:
    """The song file whose name best matches `title` (e.g. 'fireflies-owl-city.mxl' for 'Fireflies')."""
    want = _words(title)
    best, best_score = None, 0.0
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.suffix.lower() not in SONG_TYPES or "work" in f.parts:
                continue
            name = _words(f.stem)
            score = difflib.SequenceMatcher(None, want, name[: len(want) + 2]).ratio()
            if want and f" {want} " in f" {name} ":
                score += 1.0
            if artist and _words(artist) in name:
                score += 0.3
            if f.suffix.lower() in (".mid", ".midi"):
                score += 0.05            # MIDI loads fastest; otherwise the same
            if score > best_score:
                best, best_score = f, score
    return best if best_score >= 0.85 else None


@dataclass
class Section:
    start: float
    end: float
    repeats: int          # how often its opening phrase comes back (more = more likely the chorus)


def find_chorus(song: Song, seconds: float = 22.0) -> Section:
    """The catchiest stretch of about `seconds`: it starts where the most repeated melodic phrase first
    appears (choruses repeat; verses change). Falls back to the start of the song."""
    track = melody_track(song)
    evs = sorted((e for e in song.events if not e.drum and (track is None or e.track == track)),
                 key=lambda e: (e.start, -e.pitch))
    notes = []
    for e in evs:                      # top note of each chord only
        if notes and abs(e.start - notes[-1].start) < 0.03:
            continue
        notes.append(e)
    if len(notes) < PHRASE_NOTES + 2:
        return Section(0.0, min(seconds, song.duration), 0)
    beat = sorted(b.start - a.start for a, b in zip(notes, notes[1:]) if b.start > a.start)[len(notes) // 2] or 0.25
    keys = []
    for i in range(len(notes) - PHRASE_NOTES):
        seg = notes[i: i + PHRASE_NOTES + 1]
        intervals = tuple(b.pitch - a.pitch for a, b in zip(seg, seg[1:]))
        rhythm = tuple(round((b.start - a.start) / beat * 2) for a, b in zip(seg, seg[1:]))
        keys.append((intervals, rhythm))
    counts = Counter(keys)
    # most repeated phrase; among equals, the one that starts earliest (the first chorus)
    best = max(range(len(keys)), key=lambda i: (counts[keys[i]], -i))
    start = max(0.0, notes[best].start - 0.1)
    end = min(song.duration, start + seconds)
    # end on a note boundary: just after the last note that starts before the cut
    last = max((e for e in notes if e.start < end), key=lambda e: e.start)
    end = min(end, last.end + 0.2)
    return Section(round(start, 2), round(end, 2), counts[keys[best]])


class Library:
    """Song files plus their saved sections (data/songs.yaml), so each song is analysed once."""

    def __init__(self, dirs: list[Path], meta_file: Path):
        self.dirs, self.meta_file = dirs, meta_file
        self.meta: dict = yaml.safe_load(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        self.meta = self.meta or {}

    def find(self, title: str, artist: str = "") -> Path | None:
        entry = self.meta.get(_words(title).replace(" ", "-"), {})
        if entry.get("file") and Path(entry["file"]).exists():
            return Path(entry["file"])
        return find_file(title, self.dirs, artist)

    def section(self, title: str, path: Path, seconds: float) -> Section:
        key = _words(title).replace(" ", "-")
        entry = self.meta.get(key) or {}
        if "start" in entry and "end" in entry:           # set by hand, or found before
            return Section(float(entry["start"]), float(entry["end"]), int(entry.get("repeats", 0)))
        from vsampler.importers import load_song
        sec = find_chorus(load_song(str(path), SongOptions()), seconds)
        self.meta[key] = {"file": str(path), "start": sec.start, "end": sec.end, "repeats": sec.repeats}
        self.meta_file.write_text(yaml.safe_dump(self.meta, sort_keys=True), encoding="utf-8")
        return sec
