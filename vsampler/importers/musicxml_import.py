"""MusicXML / MXL sheet music -> Song (via music21's MIDI translation)."""
from __future__ import annotations

import os
import tempfile

import pretty_midi

from ..models import Song
from .midi_import import song_from_pretty_midi


def _strip_repeats(score) -> None:
    """Remove repeat barlines and repeat expressions so the score plays straight through."""
    from music21 import bar, repeat, spanner, stream

    for m in score.recurse().getElementsByClass(stream.Measure):
        for side in ("leftBarline", "rightBarline"):
            if isinstance(getattr(m, side), bar.Repeat):
                setattr(m, side, None)
    for el in list(score.recurse().getElementsByClass((repeat.RepeatExpression, repeat.RepeatMark,
                                                       spanner.RepeatBracket))):
        site = el.activeSite
        if site is not None:
            site.remove(el)


def _percussion_parts(score) -> set[int]:
    """Indexes of parts written as percussion (percussion clef, unpitched notes or a drum instrument)."""
    from music21 import clef, instrument, note, percussion

    out = set()
    for i, part in enumerate(getattr(score, "parts", [])):
        els = part.recurse()
        if (els.getElementsByClass((note.Unpitched, percussion.PercussionChord)).first() is not None
                or els.getElementsByClass(clef.PercussionClef).first() is not None
                or els.getElementsByClass(instrument.UnpitchedPercussion).first() is not None):
            out.add(i)
    return out


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
    drums = _percussion_parts(score)
    try:
        mf = midi.translate.music21ObjectToMidiFile(score)
    except Exception:  # noqa: BLE001 - e.g. "badly formed repeats" from OMR'd scores
        _strip_repeats(score)
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
    # music21 writes one MIDI track per part, in order
    song = song_from_pretty_midi(pm, path, kind, names, drums if len(pm.instruments) == len(names) else None)
    return song
