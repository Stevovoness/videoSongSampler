"""Decide which clip plays each note, and by how much it must be pitch-shifted."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import ClipSlot, NoteEvent


@dataclass
class NoteInstance:
    slot: int          # MIDI note of the clip slot that is played
    target: int        # MIDI note the song asks for
    start: float
    duration: float
    shift: float       # semitones (autotune correction + borrowing)
    velocity: float


@dataclass
class Plan:
    instances: list[NoteInstance] = field(default_factory=list)
    missing: Counter = field(default_factory=Counter)      # pitch -> count (skipped)
    borrowed: dict[int, int] = field(default_factory=dict)  # pitch -> slot used (pitch-shifted)
    exact: set[int] = field(default_factory=set)

    @property
    def total_missing(self) -> int:
        return sum(self.missing.values())


def nearest_slot(pitch: int, slots: dict[int, ClipSlot], max_shift: int) -> int | None:
    best = None
    for s in slots:
        d = abs(s - pitch)
        if d <= max_shift and (best is None or d < abs(best - pitch) or (d == abs(best - pitch) and s > best)):
            best = s
    return best


def make_plan(events: list[NoteEvent], slots: dict[int, ClipSlot], allow_shift: bool, max_shift: int = 12) -> Plan:
    plan = Plan()
    choice: dict[int, int | None] = {}
    for e in events:
        if e.pitch not in choice:
            if e.pitch in slots:
                choice[e.pitch] = e.pitch
                plan.exact.add(e.pitch)
            elif allow_shift:
                choice[e.pitch] = nearest_slot(e.pitch, slots, max_shift)
                if choice[e.pitch] is not None:
                    plan.borrowed[e.pitch] = choice[e.pitch]
            else:
                choice[e.pitch] = None
        s = choice[e.pitch]
        if s is None:
            plan.missing[e.pitch] += 1
            continue
        shift = (e.pitch - s) + slots[s].autotune_shift(s)
        plan.instances.append(NoteInstance(s, e.pitch, e.start, max(0.03, e.duration), shift, e.velocity))
    return plan
