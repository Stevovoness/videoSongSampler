"""General MIDI drum sounds, drum pad layout and drum stand-ins.

Drum clips live in `Project.drum_slots`, keyed by General MIDI drum note (36 = kick, 38 = snare, ...).
Inside the engine (plans, clip sources, collage tiles) a drum clip is referred to by `drum_key(note)`,
which is offset so it never collides with a piano-key slot.
"""
from __future__ import annotations

from .models import ClipSlot
from .notes import midi_to_name

DRUM_KEY_OFFSET = 1000

# note -> (full name, short pad label)
GM_DRUMS: dict[int, tuple[str, str]] = {
    35: ("Acoustic Bass Drum", "Kick 2"), 36: ("Bass Drum", "Kick"), 37: ("Side Stick", "Side stick"),
    38: ("Acoustic Snare", "Snare"), 39: ("Hand Clap", "Clap"), 40: ("Electric Snare", "Snare 2"),
    41: ("Low Floor Tom", "Floor tom"), 42: ("Closed Hi-Hat", "Closed hat"), 43: ("High Floor Tom", "Floor tom 2"),
    44: ("Pedal Hi-Hat", "Pedal hat"), 45: ("Low Tom", "Low tom"), 46: ("Open Hi-Hat", "Open hat"),
    47: ("Low-Mid Tom", "Mid tom"), 48: ("Hi-Mid Tom", "Mid tom 2"), 49: ("Crash Cymbal 1", "Crash"),
    50: ("High Tom", "High tom"), 51: ("Ride Cymbal 1", "Ride"), 52: ("Chinese Cymbal", "China"),
    53: ("Ride Bell", "Ride bell"), 54: ("Tambourine", "Tambourine"), 55: ("Splash Cymbal", "Splash"),
    56: ("Cowbell", "Cowbell"), 57: ("Crash Cymbal 2", "Crash 2"), 58: ("Vibraslap", "Vibraslap"),
    59: ("Ride Cymbal 2", "Ride 2"), 60: ("Hi Bongo", "Bongo hi"), 61: ("Low Bongo", "Bongo lo"),
    62: ("Mute Hi Conga", "Conga mute"), 63: ("Open Hi Conga", "Conga hi"), 64: ("Low Conga", "Conga lo"),
    65: ("High Timbale", "Timbale hi"), 66: ("Low Timbale", "Timbale lo"), 67: ("High Agogo", "Agogo hi"),
    68: ("Low Agogo", "Agogo lo"), 69: ("Cabasa", "Cabasa"), 70: ("Maracas", "Maracas"),
    71: ("Short Whistle", "Whistle"), 72: ("Long Whistle", "Whistle 2"), 73: ("Short Guiro", "Guiro"),
    74: ("Long Guiro", "Guiro 2"), 75: ("Claves", "Claves"), 76: ("Hi Wood Block", "Woodblock hi"),
    77: ("Low Wood Block", "Woodblock lo"), 78: ("Mute Cuica", "Cuica mute"), 79: ("Open Cuica", "Cuica"),
    80: ("Mute Triangle", "Triangle mute"), 81: ("Open Triangle", "Triangle"),
}

# The 16 pads shown by default, in pad order (rows of 4, top to bottom) -
# matches the computer keys 1234 / QWER / ASDF / ZXCV.
CORE_KIT = [49, 51, 46, 42,
            50, 47, 45, 44,
            38, 40, 37, 39,
            36, 35, 54, 56]

_FAMILIES = {
    "kick": {35, 36},
    "snare": {37, 38, 39, 40},
    "hat": {42, 44, 46},
    "tom": {41, 43, 45, 47, 48, 50},
    "cymbal": {49, 51, 52, 53, 55, 57, 59},
    "hand": {60, 61, 62, 63, 64, 65, 66},
}


def drum_family(note: int) -> str:
    for fam, notes in _FAMILIES.items():
        if note in notes:
            return fam
    return "perc"


def drum_name(note: int) -> str:
    return GM_DRUMS.get(note, (f"Drum {note}", f"Drum {note}"))[0]


def drum_short(note: int) -> str:
    return GM_DRUMS.get(note, (f"Drum {note}", f"Drum {note}"))[1]


def drum_key(note: int) -> int:
    return DRUM_KEY_OFFSET + note


def is_drum_key(key: int) -> bool:
    return key >= DRUM_KEY_OFFSET


def drum_note(key: int) -> int:
    return key - DRUM_KEY_OFFSET


def slot_label(key: int) -> str:
    """Tile / list label for an engine slot key: 'Snare' for drums, 'C#4' for notes."""
    return drum_short(drum_note(key)) if is_drum_key(key) else midi_to_name(key)


def nearest_drum(note: int, drum_slots: dict[int, ClipSlot]) -> int | None:
    """A drum clip to stand in for `note`: same family first (closest number), else any drum clip."""
    if not drum_slots:
        return None
    fam = drum_family(note)
    same = [n for n in drum_slots if drum_family(n) == fam]
    pool = same or list(drum_slots)
    return min(pool, key=lambda n: (abs(n - note), n))
