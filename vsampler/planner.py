"""Decide which clip plays each note, and by how much it must be pitch-shifted.

`resolve` / `resolve_drum` answer "what plays this note?" for a single note. The renderer (via
`make_plan`) and the clickable sample pad both use them, so a key sounds exactly like it will in the video.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .drums import drum_key, drum_note, nearest_drum
from .models import ClipSlot, NoteEvent, SongOptions


@dataclass
class NoteInstance:
    slot: int          # engine slot key that is played (a MIDI note, or drum_key(n) for drums)
    target: int        # MIDI note (or GM drum note) the song asks for
    start: float
    duration: float
    shift: float       # semitones (autotune correction + borrowing)
    velocity: float
    drum: bool = False


@dataclass
class Resolution:
    slot: int | None   # engine slot key, None = nothing to play
    shift: float = 0.0
    how: str = "missing"   # exact | octave | borrowed | missing (drums: exact | standin | missing)


@dataclass
class Plan:
    instances: list[NoteInstance] = field(default_factory=list)
    missing: Counter = field(default_factory=Counter)      # pitch -> count (skipped)
    borrowed: dict[int, int] = field(default_factory=dict)  # pitch -> slot used (pitch-shifted)
    octave: dict[int, int] = field(default_factory=dict)    # pitch -> slot an octave or more away
    exact: set[int] = field(default_factory=set)
    drum_exact: set[int] = field(default_factory=set)
    drum_standin: dict[int, int] = field(default_factory=dict)  # drum note -> drum note whose clip stands in
    drum_missing: Counter = field(default_factory=Counter)

    @property
    def total_missing(self) -> int:
        return sum(self.missing.values()) + sum(self.drum_missing.values())

    @property
    def note_instances(self) -> list[NoteInstance]:
        return [i for i in self.instances if not i.drum]

    @property
    def drum_instances(self) -> list[NoteInstance]:
        return [i for i in self.instances if i.drum]


def nearest_slot(pitch: int, slots: dict[int, ClipSlot], max_shift: int) -> int | None:
    best = None
    for s in slots:
        d = abs(s - pitch)
        if d <= max_shift and (best is None or d < abs(best - pitch) or (d == abs(best - pitch) and s > best)):
            best = s
    return best


def octave_slot(pitch: int, slots: dict[int, ClipSlot], max_octaves: int) -> int | None:
    """The same note name an octave (or more) away that has a clip; closest first, higher on a tie."""
    for k in range(1, max_octaves + 1):
        for cand in (pitch + 12 * k, pitch - 12 * k):
            if cand in slots:
                return cand
    return None


def resolve(pitch: int, slots: dict[int, ClipSlot], octave_jump: bool = False, octave_max: int = 2,
            allow_shift: bool = False, max_shift: int = 12) -> Resolution:
    """What plays pitched note `pitch`: its own clip, then an octave jump, then the nearest clip pitch-shifted."""
    if pitch in slots:
        return Resolution(pitch, slots[pitch].autotune_shift(pitch), "exact")
    if octave_jump:
        s = octave_slot(pitch, slots, octave_max)
        if s is not None:
            return Resolution(s, slots[s].autotune_shift(s), "octave")
    if allow_shift:
        s = nearest_slot(pitch, slots, max_shift)
        if s is not None:
            return Resolution(s, (pitch - s) + slots[s].autotune_shift(s), "borrowed")
    return Resolution(None)


def resolve_drum(note: int, drum_slots: dict[int, ClipSlot], standins: bool = True) -> Resolution:
    """What plays drum sound `note`: its own pad, else (optionally) a similar drum clip. Never pitch-shifted."""
    if note in drum_slots:
        return Resolution(drum_key(note), 0.0, "exact")
    if standins:
        s = nearest_drum(note, drum_slots)
        if s is not None:
            return Resolution(drum_key(s), 0.0, "standin")
    return Resolution(None)


def resolve_opts(pitch: int, slots: dict[int, ClipSlot], o: SongOptions) -> Resolution:
    return resolve(pitch, slots, o.octave_jump, o.octave_max, o.allow_pitch_shift, o.max_shift)


def _clip_length(slot: ClipSlot) -> float:
    return slot.trim_end - slot.trim_start if slot.trim_end is not None else 0.0


def make_plan(events: list[NoteEvent], slots: dict[int, ClipSlot], allow_shift: bool = False, max_shift: int = 12,
              *, octave_jump: bool = False, octave_max: int = 2,
              drum_slots: dict[int, ClipSlot] | None = None, drum_standins: bool = True) -> Plan:
    plan = Plan()
    drum_slots = drum_slots or {}
    choice: dict[tuple[bool, int], Resolution] = {}
    for e in events:
        ck = (e.drum, e.pitch)
        if ck not in choice:
            if e.drum:
                r = resolve_drum(e.pitch, drum_slots, drum_standins)
                if r.how == "exact":
                    plan.drum_exact.add(e.pitch)
                elif r.how == "standin":
                    plan.drum_standin[e.pitch] = drum_note(r.slot)
            else:
                r = resolve(e.pitch, slots, octave_jump, octave_max, allow_shift, max_shift)
                if r.how == "exact":
                    plan.exact.add(e.pitch)
                elif r.how == "octave":
                    plan.octave[e.pitch] = r.slot
                elif r.how == "borrowed":
                    plan.borrowed[e.pitch] = r.slot
            choice[ck] = r
        r = choice[ck]
        if r.slot is None:
            (plan.drum_missing if e.drum else plan.missing)[e.pitch] += 1
            continue
        if e.drum:
            # drums are one-shots: the whole recorded hit plays, never stretched
            dur = max(0.03, _clip_length(drum_slots[drum_note(r.slot)]) or e.duration)
            plan.instances.append(NoteInstance(r.slot, e.pitch, e.start, dur, 0.0, e.velocity, True))
        else:
            plan.instances.append(NoteInstance(r.slot, e.pitch, e.start, max(0.03, e.duration), r.shift, e.velocity))
    return plan


def plan_for_project(events: list[NoteEvent], project) -> Plan:
    o = project.song
    return make_plan(events, project.slots, o.allow_pitch_shift, o.max_shift, octave_jump=o.octave_jump,
                     octave_max=o.octave_max, drum_slots=project.drum_slots, drum_standins=o.drum_standins)
