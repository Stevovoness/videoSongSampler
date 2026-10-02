"""Plain data objects shared by the engine and the GUI."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ClipSlot:
    """A video clip assigned to one note."""
    path: str
    trim_start: float = 0.0          # seconds into the source clip where the sound starts
    trim_end: float | None = None    # seconds; None = end of clip
    detected_midi: float | None = None  # detected pitch (fractional MIDI number)
    autotune: bool = True            # correct the detected pitch to exactly the slot's note
    gain_db: float = 0.0             # user adjustment, on top of automatic levelling
    loudness_db: float | None = None # measured loudness of the trimmed sound (dBFS)

    def autotune_shift(self, slot_midi: int) -> float:
        """Semitones to shift so the clip is exactly in tune (only small corrections)."""
        if not self.autotune or self.detected_midi is None:
            return 0.0
        diff = slot_midi - self.detected_midi
        return diff if abs(diff) <= 1.0 else 0.0


@dataclass
class NoteEvent:
    start: float      # seconds
    end: float        # seconds
    pitch: int        # MIDI number
    velocity: float = 1.0  # 0..1
    track: int = 0
    drum: bool = False     # pitch is a General MIDI drum sound, played on a drum pad

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Track:
    index: int
    name: str
    note_count: int
    low: int
    high: int
    is_drum: bool = False


@dataclass
class Song:
    source_path: str
    kind: str                       # midi / musicxml / audio / sheet
    events: list[NoteEvent]
    tracks: list[Track]

    @property
    def duration(self) -> float:
        return max((e.end for e in self.events), default=0.0)


@dataclass
class SongOptions:
    enabled_tracks: list[int] | None = None   # None = all tracks (drum tracks only if there are drum clips)
    transpose: int = 0
    tempo_pct: float = 100.0
    melody_only: bool = False
    octave_jump: bool = False                # play missing notes from a clip an octave up / down
    octave_max: int = 2
    allow_pitch_shift: bool = False          # use nearest clip for missing notes
    max_shift: int = 12
    drum_standins: bool = True               # missing drum sounds borrow a similar drum clip
    # audio-import options
    audio_onset_threshold: float = 0.5
    audio_min_note_ms: float = 120.0
    audio_melody_only: bool = True


@dataclass
class RenderSettings:
    layout: str = "grid"            # grid | dynamic
    width: int = 1280
    height: int = 720
    fps: int = 30
    background: str = "#101014"
    highlight: str = "#ffcc33"
    show_labels: bool = True
    idle_mode: str = "dim"          # dim | hide  (grid layout)
    only_used_clips: bool = True    # grid shows only clips the song uses
    smooth_slowmo: bool = True      # blend frames when a clip is stretched
    velocity_volume: bool = True    # quieter notes for softer velocities
    even_volumes: bool = True       # level every clip to the same loudness
    tail: float = 1.0               # seconds of silence at end
    sample_rate: int = 44100
    output_path: str = ""


@dataclass
class Project:
    slots: dict[int, ClipSlot] = field(default_factory=dict)
    drum_slots: dict[int, ClipSlot] = field(default_factory=dict)   # GM drum note -> clip
    song_path: str = ""
    song: SongOptions = field(default_factory=SongOptions)
    render: RenderSettings = field(default_factory=RenderSettings)
    audiveris_path: str = ""

    def to_json(self) -> str:
        d = {
            "version": 2,
            "slots": {str(k): asdict(v) for k, v in self.slots.items()},
            "drum_slots": {str(k): asdict(v) for k, v in self.drum_slots.items()},
            "song_path": self.song_path,
            "song": asdict(self.song),
            "render": asdict(self.render),
            "audiveris_path": self.audiveris_path,
        }
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Project":
        d = json.loads(text)
        p = cls()
        p.slots = {int(k): ClipSlot(**v) for k, v in d.get("slots", {}).items()}
        p.drum_slots = {int(k): ClipSlot(**v) for k, v in d.get("drum_slots", {}).items()}
        p.song_path = d.get("song_path", "")
        p.song = SongOptions(**{k: v for k, v in d.get("song", {}).items() if k in SongOptions.__dataclass_fields__})
        p.render = RenderSettings(**{k: v for k, v in d.get("render", {}).items() if k in RenderSettings.__dataclass_fields__})
        p.audiveris_path = d.get("audiveris_path", "")
        return p

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))
