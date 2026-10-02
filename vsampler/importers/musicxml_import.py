"""MusicXML / MXL sheet music -> Song (via music21's MIDI translation)."""
from __future__ import annotations

import os
import tempfile

import pretty_midi

from ..models import Song
from .midi_import import song_from_pretty_midi


def load_musicxml(path: str, kind: str = "musicxml") -> Song:
    from music21 import converter, midi

    score = converter.parse(path)
    try:
        expanded = score.expandRepeats()
        if expanded is not None and len(expanded.flatten().notes):
            score = expanded
    except Exception:  # noqa: BLE001 - repeats are optional
        pass
    names = []
    for p in getattr(score, "parts", []):
        names.append(p.partName or p.id or f"Part {len(names) + 1}")
    mf = midi.translate.music21ObjectToMidiFile(score)
    fd, tmp = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        mf.open(tmp, "wb")
        mf.write()
        mf.close()
        pm = pretty_midi.PrettyMIDI(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    song = song_from_pretty_midi(pm, path, kind, names)
    return song
