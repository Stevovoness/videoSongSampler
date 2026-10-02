"""Note-name <-> MIDI number helpers. Middle C (C4) = 60."""
from __future__ import annotations

import math
import re

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_RE = re.compile(r"^\s*([A-Ga-g])\s*([#b♯♭]*|sharp|flat)?\s*(-?\d+)\s*$")


def name_to_midi(name: str) -> int:
    m = _RE.match(name.replace("natural", "").replace("♮", ""))
    if not m:
        raise ValueError(f"Not a note name: {name!r}")
    letter, acc, octave = m.group(1).upper(), m.group(2) or "", int(m.group(3))
    shift = 0
    if acc == "sharp":
        shift = 1
    elif acc == "flat":
        shift = -1
    else:
        shift = acc.count("#") + acc.count("♯") - acc.count("b") - acc.count("♭")
    return (octave + 1) * 12 + _BASE[letter] + shift


def midi_to_name(midi: int, flats: bool = False) -> str:
    midi = int(round(midi))
    names = FLAT_NAMES if flats else SHARP_NAMES
    return f"{names[midi % 12]}{midi // 12 - 1}"


def pretty_name(midi: int) -> str:
    """'C#4 / Db4' for black keys, 'B4' for white keys."""
    s, f = midi_to_name(midi), midi_to_name(midi, flats=True)
    return s if s == f else f"{s} / {f}"


def is_black(midi: int) -> bool:
    return midi % 12 in (1, 3, 6, 8, 10)


def freq_to_midi(freq: float) -> float:
    return 69.0 + 12.0 * math.log2(freq / 440.0)


def midi_to_freq(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def describe_detected(midi_float: float) -> str:
    """'C#4 -18¢' style description of a detected pitch."""
    nearest = int(round(midi_float))
    cents = int(round((midi_float - nearest) * 100))
    sign = "+" if cents > 0 else ("−" if cents < 0 else "±")
    return f"{midi_to_name(nearest)} {sign}{abs(cents)}¢"
