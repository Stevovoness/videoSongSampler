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


def _melody_notes(song: Song) -> list:
    track = melody_track(song)
    evs = sorted((e for e in song.events if not e.drum and (track is None or e.track == track)),
                 key=lambda e: (e.start, -e.pitch))
    notes = []
    for e in evs:                      # top note of each chord only
        if notes and abs(e.start - notes[-1].start) < 0.03:
            continue
        notes.append(e)
    return notes


def _cut(notes: list, start: float, seconds: float, song_end: float) -> float:
    """The end of a part starting at `start`: about `seconds` later, just after a note."""
    end = min(song_end, start + seconds)
    last = max((e for e in notes if start <= e.start < end), key=lambda e: e.start, default=None)
    return round(min(end, last.end + 0.2), 2) if last is not None else round(end, 2)


def song_parts(song: Song, seconds: float = 22.0, limit: int = 6) -> list[Section]:
    """Parts of the song worth using, best first: where the most repeated melodic phrases appear (choruses
    repeat; verses change), each place only once, then the start of the song. Each part is about `seconds`."""
    notes = _melody_notes(song)
    end_of_song = song.duration
    if len(notes) < PHRASE_NOTES + 2:
        return [Section(0.0, round(min(seconds, end_of_song), 2), 0)]
    beat = sorted(b.start - a.start for a, b in zip(notes, notes[1:]) if b.start > a.start)[len(notes) // 2] or 0.25
    keys = []
    for i in range(len(notes) - PHRASE_NOTES):
        seg = notes[i: i + PHRASE_NOTES + 1]
        intervals = tuple(b.pitch - a.pitch for a, b in zip(seg, seg[1:]))
        rhythm = tuple(round((b.start - a.start) / beat * 2) for a, b in zip(seg, seg[1:]))
        keys.append((intervals, rhythm))
    counts = Counter(keys)
    # first where each different phrase first appears (most repeated first: the chorus, then other repeated
    # parts like the verse), then the later times they come back
    first: dict = {}
    for i, k in enumerate(keys):
        first.setdefault(k, i)
    firsts = sorted(first.values(), key=lambda i: (-counts[keys[i]], i))
    later = sorted((i for i in range(len(keys)) if first[keys[i]] != i), key=lambda i: (-counts[keys[i]], i))
    order = firsts + later
    parts: list[Section] = []
    for i in order:
        start = round(max(0.0, notes[i].start - 0.1), 2)
        if any(abs(start - p.start) < seconds * 0.6 for p in parts):
            continue                   # too close to a part already listed
        parts.append(Section(start, _cut(notes, start, seconds, end_of_song), counts[keys[i]]))
        if len(parts) >= limit - 1:
            break
    if not any(p.start < seconds * 0.6 for p in parts):
        parts.append(Section(0.0, _cut(notes, 0.0, seconds, end_of_song), 0))
    return parts


def find_chorus(song: Song, seconds: float = 22.0) -> Section:
    """The catchiest stretch of about `seconds`: where the most repeated melodic phrase first appears."""
    return song_parts(song, seconds, limit=2)[0]


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
        self._write()
        return sec

    def parts(self, path: Path, seconds: float) -> tuple[list[Section], float]:
        """The song's suggested parts (best first) and its length in seconds."""
        from vsampler.importers import load_song
        song = load_song(str(path), SongOptions())
        return song_parts(song, seconds), round(song.duration, 2)

    def remember(self, title: str, path: Path, start: float, end: float) -> None:
        """Use this part of the song from now on (it's what `section` returns)."""
        key = _words(title).replace(" ", "-")
        self.meta[key] = {**(self.meta.get(key) or {}), "file": str(path), "start": start, "end": end,
                          "chosen": True}
        self._write()

    def _write(self) -> None:
        self.meta_file.write_text(yaml.safe_dump(self.meta, sort_keys=True), encoding="utf-8")
