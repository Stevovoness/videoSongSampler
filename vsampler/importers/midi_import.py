"""MIDI -> Song."""
from __future__ import annotations

import pretty_midi

from ..models import NoteEvent, Song, Track


def song_from_pretty_midi(pm: pretty_midi.PrettyMIDI, source: str, kind: str, names: list[str] | None = None,
                          drum_instruments: set[int] | None = None) -> Song:
    """`drum_instruments`: if given, exactly these instrument indexes are drums, whatever their MIDI channel
    (music21 sometimes puts pitched parts on channel 10)."""
    events: list[NoteEvent] = []
    tracks: list[Track] = []
    for i, inst in enumerate(pm.instruments):
        notes = [n for n in inst.notes if n.end > n.start]
        if not notes:
            continue
        if drum_instruments is not None:
            inst.is_drum = i in drum_instruments
        name = inst.name.strip() if inst.name and inst.name.strip() else ""
        if not name and names and i < len(names):
            name = names[i]
        if not name:
            name = "Drums" if inst.is_drum else pretty_midi.program_to_instrument_name(inst.program)
        idx = len(tracks)
        tracks.append(Track(idx, name, len(notes), min(n.pitch for n in notes), max(n.pitch for n in notes), inst.is_drum))
        for n in notes:
            events.append(NoteEvent(float(n.start), float(n.end), int(n.pitch), n.velocity / 127.0, idx, inst.is_drum))
    events.sort(key=lambda e: (e.start, e.pitch))
    if not events:
        raise ValueError("No notes were found in this song.")
    return Song(source, kind, events, tracks)


def load_midi(path: str) -> Song:
    return song_from_pretty_midi(pretty_midi.PrettyMIDI(path), path, "midi")
