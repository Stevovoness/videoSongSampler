import sys
from pathlib import Path

import av
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_clip, make_midi  # noqa: E402

from vsampler import audio_dsp
from vsampler.clips import analyze_clip, detect_pitch
from vsampler.importers import load_song, song_kind
from vsampler.models import ClipSlot, NoteEvent, Project, SongOptions
from vsampler.notes import midi_to_name, name_to_midi
from vsampler.planner import make_plan
from vsampler.render.layouts import fit_grid
from vsampler.render.renderer import render_video
from vsampler.songops import apply_options, skyline, suggest_transpose

SR = 44100


def tone(freq, secs, sr=SR):
    t = np.arange(int(secs * sr)) / sr
    y = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([y, y])


# ---------------------------------------------------------------- notes
@pytest.mark.parametrize("name,midi", [("C4", 60), ("C#4", 61), ("Db4", 61), ("B3", 59), ("A4", 69), ("Bb2", 46),
                                       ("E#4", 65), ("c-1", 0)])
def test_name_to_midi(name, midi):
    assert name_to_midi(name) == midi


def test_midi_to_name_roundtrip():
    for m in range(0, 128):
        assert name_to_midi(midi_to_name(m)) == m


# ---------------------------------------------------------------- dsp
@pytest.mark.parametrize("dur", [0.2, 0.5, 1.0, 2.0, 4.0])
def test_stretch_keeps_pitch_and_length(dur):
    y = tone(440.0, 0.5)
    out = audio_dsp.render_note(f"t{dur}", y, SR, dur, 0.0)
    assert out.shape == (2, int(round(dur * SR)))
    pitch, _ = detect_pitch(out)
    assert abs(pitch - 69.0) < 0.05   # within 5 cents


@pytest.mark.parametrize("shift", [-3.0, 2.0, 0.3])
def test_shift_and_stretch(shift):
    out = audio_dsp.render_note(f"s{shift}", tone(440.0, 0.5), SR, 2.0, shift)
    pitch, _ = detect_pitch(out)
    assert abs(pitch - (69.0 + shift)) < 0.05


def test_time_map():
    assert audio_dsp.time_map(0.5, 0.4, 1.0) == 0.5            # shorter note: real time
    assert audio_dsp.time_map(0.05, 3.0, 1.0) == 0.05          # attack untouched
    assert abs(audio_dsp.time_map(3.0, 3.0, 1.0) - 1.0) < 1e-9  # end of note -> end of clip


# ---------------------------------------------------------------- planning
def slots(*midis):
    return {m: ClipSlot(f"{m}.mp4", detected_midi=m + 0.2) for m in midis}


def test_plan_missing_and_borrowed():
    evs = [NoteEvent(0, 1, 60), NoteEvent(1, 2, 62), NoteEvent(2, 3, 65)]
    p = make_plan(evs, slots(60, 64), allow_shift=False)
    assert p.missing == {62: 1, 65: 1}
    assert len(p.instances) == 1 and abs(p.instances[0].shift + 0.2) < 1e-9  # autotune correction
    p = make_plan(evs, slots(60, 64), allow_shift=True, max_shift=12)
    assert not p.missing
    assert p.borrowed == {62: 64, 65: 64}   # tie at 62 prefers the higher clip
    shifts = {i.target: i.shift for i in p.instances}
    assert abs(shifts[65] - 0.8) < 1e-9


def test_plan_max_shift():
    p = make_plan([NoteEvent(0, 1, 80)], slots(60), allow_shift=True, max_shift=5)
    assert p.missing == {80: 1}


def test_skyline():
    evs = [NoteEvent(0, 2, 60), NoteEvent(0, 2, 64), NoteEvent(1, 2, 67)]
    out = skyline(evs)
    assert [(e.pitch, e.start, e.end) for e in out] == [(64, 0, 1), (67, 1, 2)]


def test_layout_fits():
    rects = fit_grid(7, 1280, 720, 16 / 9)
    assert len(rects) == 7
    for x, y, w, h in rects:
        assert x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720


# ---------------------------------------------------------------- importers + render
@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    clips = {m: make_clip(d / f"c{m}.mp4", m + (0.25 if m == 64 else 0)) for m in (60, 64, 67)}
    mid = make_midi(d / "song.mid", [(60, 0, 0.5), (64, 0.5, 2.5), (67, 2.5, 3), (62, 3, 3.5)])
    return d, clips, mid


def test_analyze_clip(media):
    _, clips, _ = media
    a, _ = analyze_clip(clips[64])
    assert abs(a.detected_midi - 64.25) < 0.1
    assert 0.1 < a.trim_start < 0.25


def test_midi_import(media):
    _, _, mid = media
    song = load_song(mid, SongOptions())
    assert song_kind(mid) == "midi"
    assert [t.name for t in song.tracks] == ["Lead", "Drums"]
    evs = apply_options(song, SongOptions())
    assert len(evs) == 4  # drums excluded by default
    assert suggest_transpose(song, SongOptions(), {60, 64, 67, 62}) == 0


def test_musicxml_import(tmp_path):
    from music21 import note, stream, tempo
    s = stream.Score()
    p = stream.Part()
    p.append(tempo.MetronomeMark(number=120))
    for n in ("C4", "E4", "G4"):
        p.append(note.Note(n, quarterLength=1))
    s.append(p)
    path = tmp_path / "x.musicxml"
    s.write("musicxml", fp=str(path))
    song = load_song(str(path), SongOptions())
    evs = apply_options(song, SongOptions())
    assert [e.pitch for e in evs] == [60, 64, 67]
    assert abs((evs[1].start - evs[0].start) - 0.5) < 0.01


@pytest.mark.parametrize("layout", ["grid", "dynamic"])
def test_render(media, layout):
    d, clips, mid = media
    proj = Project()
    for m, path in clips.items():
        a, _ = analyze_clip(path)
        proj.slots[m] = ClipSlot(path, a.trim_start, a.trim_end, a.detected_midi)
    proj.render.layout = layout
    proj.render.width, proj.render.height = 640, 360
    proj.song.allow_pitch_shift = True
    song = load_song(mid, proj.song)
    out = str(d / f"out_{layout}.mp4")
    render_video(proj, song, out)
    with av.open(out) as c:
        kinds = sorted(s.type for s in c.streams)
        assert kinds == ["audio", "video"]
        dur = float(c.duration / 1e6)
        assert abs(dur - 4.5) < 0.2


# ---------------------------------------------------------------- volume levelling
def test_loudness_levelling():
    from vsampler.clips import TARGET_LOUDNESS_DB, level_gain_db, measure_loudness
    quiet = tone(440.0, 0.5) * 0.02
    loud = tone(440.0, 0.5) * 0.9
    # a short squeal padded with silence measures the same as the squeal alone
    padded = np.concatenate([np.zeros((2, SR)), loud, np.zeros((2, SR))], axis=1)
    assert abs(measure_loudness(padded) - measure_loudness(loud)) < 0.5
    for y in (quiet, loud):
        levelled = measure_loudness(y) + level_gain_db(measure_loudness(y))
        assert abs(levelled - TARGET_LOUDNESS_DB) < 0.1
    assert measure_loudness(np.zeros((2, SR))) is None


def test_render_evens_volumes(tmp_path):
    from vsampler.clips import load_audio
    quiet = make_clip(tmp_path / "q.mp4", 60)
    proj = Project()
    for m, path in ((60, quiet), (64, make_clip(tmp_path / "l.mp4", 64))):
        a, _ = analyze_clip(path)
        proj.slots[m] = ClipSlot(path, a.trim_start, a.trim_end, a.detected_midi, loudness_db=a.loudness_db)
    mid = make_midi(tmp_path / "s.mid", [(60, 0, 0.6), (64, 1.0, 1.6)])
    song = load_song(mid, proj.song)
    proj.render.width, proj.render.height = 320, 180
    proj.render.velocity_volume = False

    def levels(even):
        proj.render.even_volumes = even
        out = str(tmp_path / f"o{even}.mp4")
        render_video(proj, song, out)
        a = load_audio(out)
        r = lambda s, e: 20 * np.log10(np.sqrt((a[:, int(s * SR):int(e * SR)] ** 2).mean()))
        return r(0.05, 0.5), r(1.05, 1.5)

    # simulate the C4 clip having been recorded 20 dB quieter
    from vsampler.render import sources
    src = sources.get_source(proj.slots[60])
    src.audio *= 0.1
    try:
        a_off, b_off = levels(False)
        a_on, b_on = levels(True)
    finally:
        sources.forget(quiet)
    assert abs(a_off - b_off) > 15          # without levelling: very different
    assert abs(a_on - b_on) < 2             # with levelling: about the same
