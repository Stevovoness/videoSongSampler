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


# ---------------------------------------------------------------- octave jump
from vsampler.drums import drum_key  # noqa: E402
from vsampler.models import Song, Track  # noqa: E402
from vsampler.planner import resolve, resolve_drum  # noqa: E402


def test_octave_jump():
    s = slots(60, 64)
    r = resolve(72, s, octave_jump=True)
    assert (r.slot, r.how) == (60, "octave") and abs(r.shift + 0.2) < 1e-9   # plays C4 as-is (only auto-tune)
    assert resolve(52, s, octave_jump=True).slot == 64                       # too low -> an octave up
    assert resolve(84, s, octave_jump=True, octave_max=1).how == "missing"
    assert resolve(84, s, octave_jump=True, octave_max=2).slot == 60
    assert resolve(60, s, octave_jump=True).how == "exact"
    assert resolve(76, s, octave_jump=False).how == "missing"
    # octave jump is tried before pitch-shifting
    assert resolve(76, s, octave_jump=True, allow_shift=True).how == "octave"
    r = resolve(70, s, octave_jump=True, allow_shift=True)
    assert (r.slot, r.how) == (64, "borrowed")
    p = make_plan([NoteEvent(0, 1, 72), NoteEvent(1, 2, 61)], s, octave_jump=True)
    assert p.octave == {72: 60} and p.missing == {61: 1}
    assert p.instances[0].slot == 60 and p.instances[0].target == 72


# ---------------------------------------------------------------- drums
def test_drum_resolution():
    d = {38: ClipSlot("snare.mp4"), 36: ClipSlot("kick.mp4")}
    assert resolve_drum(36, d).how == "exact" and resolve_drum(36, d).slot == drum_key(36)
    assert (resolve_drum(40, d).slot, resolve_drum(40, d).how) == (drum_key(38), "standin")  # same family
    assert resolve_drum(35, d).slot == drum_key(36)
    assert resolve_drum(42, d).slot == drum_key(38)          # no hi-hat family clip: nearest drum clip
    assert resolve_drum(42, d, standins=False).how == "missing"
    assert resolve_drum(42, {}).how == "missing"


def _drum_song():
    evs = [NoteEvent(0, 1, 60, track=0), NoteEvent(0, 1, 55, track=0), NoteEvent(0, 0.1, 36, track=1, drum=True),
           NoteEvent(0, 0.1, 38, track=1, drum=True)]
    return Song("x.mid", "midi", evs, [Track(0, "Lead", 2, 55, 60), Track(1, "Drums", 2, 36, 38, True)])


def test_drums_in_song_options():
    song = _drum_song()
    opts = SongOptions(transpose=5, melody_only=True)
    assert [e.pitch for e in apply_options(song, opts)] == [65]               # drums off without drum clips
    evs = apply_options(song, opts, include_drums=True)
    assert sorted((e.pitch, e.drum) for e in evs) == [(36, True), (38, True), (65, False)]  # not transposed
    assert suggest_transpose(song, SongOptions(enabled_tracks=[0, 1]), {60, 55}) == 0


def test_plan_with_drums():
    song = _drum_song()
    evs = apply_options(song, SongOptions(), include_drums=True)
    p = make_plan(evs, slots(60, 55), drum_slots={36: ClipSlot("k.mp4", 0.0, 0.4)}, drum_standins=False)
    assert p.drum_exact == {36} and p.drum_missing == {38: 1}
    hit = p.drum_instances[0]
    assert hit.slot == drum_key(36) and hit.shift == 0 and abs(hit.duration - 0.4) < 1e-9   # whole clip, as recorded
    p = make_plan(evs, slots(60, 55), drum_slots={36: ClipSlot("k.mp4")})
    assert p.drum_standin == {38: 36} and not p.drum_missing


def test_project_json_drums_and_old_files():
    p = Project()
    p.slots[60] = ClipSlot("a.mp4")
    p.drum_slots[38] = ClipSlot("snare.mp4", autotune=False)
    p.song.octave_jump = True
    q = Project.from_json(p.to_json())
    assert q.drum_slots[38].path == "snare.mp4" and not q.drum_slots[38].autotune and q.song.octave_jump
    old = Project.from_json('{"version": 1, "slots": {"60": {"path": "a.mp4"}}, "song": {"transpose": 2}}')
    assert old.drum_slots == {} and old.song.transpose == 2 and not old.song.octave_jump


def test_render_with_drums(media):
    from vsampler.render.renderer import prepare, render_slot_audio
    d, clips, mid = media
    proj = Project()
    for m, path in clips.items():
        a, _ = analyze_clip(path)
        proj.slots[m] = ClipSlot(path, a.trim_start, a.trim_end, a.detected_midi)
    a, _ = analyze_clip(clips[67], detect=False)
    assert a.detected_midi is None
    proj.drum_slots[36] = ClipSlot(clips[67], a.trim_start, a.trim_end, autotune=False)
    proj.render.width, proj.render.height = 320, 180
    song = load_song(mid, proj.song)
    prep = prepare(proj, song)
    hits = prep.plan.drum_instances
    assert len(hits) == 1 and hits[0].slot == drum_key(36)
    assert abs(hits[0].duration - prep.sources[(drum_key(36), 0)].length) < 1e-9   # never stretched or cut
    out = str(d / "out_drums.mp4")
    render_video(proj, song, out)
    with av.open(out) as c:
        assert sorted(s.type for s in c.streams) == ["audio", "video"]
    # the sample pad hears exactly the trimmed clip
    y = render_slot_audio(proj, drum_key(36))
    assert y.shape[0] == 2 and abs(y.shape[1] / SR - (a.trim_end - a.trim_start)) < 0.01
    assert render_slot_audio(proj, 64, proj.slots[64].autotune_shift(64)).shape[0] == 2


def test_musicxml_percussion_part(tmp_path):
    from music21 import clef, instrument, note, stream, tempo
    s = stream.Score()
    lead = stream.Part()
    lead.partName = "Lead"
    lead.append(tempo.MetronomeMark(number=120))
    for n in ("C4", "E4", "G4"):
        lead.append(note.Note(n, quarterLength=1))
    drums = stream.Part()
    drums.partName = "Drums"
    drums.insert(0, instrument.SnareDrum())
    drums.append(clef.PercussionClef())
    for _ in range(3):
        u = note.Unpitched(quarterLength=1)
        u.displayStep, u.displayOctave = "C", 5
        drums.append(u)
    s.append(lead)
    s.append(drums)
    path = tmp_path / "perc.musicxml"
    s.write("musicxml", fp=str(path))
    song = load_song(str(path), SongOptions())
    assert [t.is_drum for t in song.tracks] == [False, True]
    drum_evs = [e for e in song.events if e.drum]
    assert len(drum_evs) == 3
    assert [e.pitch for e in apply_options(song, SongOptions())] == [60, 64, 67]


# ---------------------------------------------------------------- clip finder + takes
from fixtures import make_long_clip  # noqa: E402

from vsampler import clipfinder  # noqa: E402
from vsampler.clips import load_audio  # noqa: E402
from vsampler.models import Take  # noqa: E402

LONG_NOTES = [(60, 0.5, 0.6), (64, 1.6, 0.6), (67, 2.7, 0.5), (60, 3.7, 0.6), (72, 4.8, 0.5), (64, 5.8, 0.6)]
LONG_HITS = [7.0, 7.8]


@pytest.fixture(scope="module")
def long_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("long")
    path = make_long_clip(d / "long.mp4", LONG_NOTES, LONG_HITS, total=8.8)
    audio = load_audio(path)
    return path, audio, clipfinder.analyse(audio)


def test_finder_suggests_every_clip_point(long_video):
    _, _, res = long_video
    notes = [c for c in res.candidates if c.kind == "note"]
    hits = [c for c in res.candidates if c.kind == "hit"]
    for midi, start, _dur in LONG_NOTES:
        found = [c for c in notes if abs(c.start - start) < 0.08]
        assert found, f"no suggestion near {start}s"
        assert abs(found[0].midi - midi) < 0.3
    assert len(notes) == len(LONG_NOTES)
    assert sorted(c.note for c in notes).count(60) == 2 and sorted(c.note for c in notes).count(64) == 2  # 2 takes
    assert len(hits) == 2 and all(any(abs(h.start - t) < 0.06 for h in hits) for t in LONG_HITS)
    best = clipfinder.best_takes(res.candidates)
    assert sorted(res.candidates[i].note for i in best if res.candidates[i].kind == "note") == [60, 64, 67, 72]
    assert res.duration == pytest.approx(8.8, abs=0.1) and len(res.peaks) > 100


def test_finder_refine_after_move(long_video):
    _, audio, res = long_video
    c = next(c for c in res.candidates if c.note == 67)
    midi, _conf, loud = clipfinder.refine(audio, SR, c.start + 2.1, c.end + 2.1)   # moved onto the C5 note
    assert abs(midi - 72) < 0.3 and loud is not None


def test_takes_rotate_and_tune_separately():
    slot = ClipSlot("a.mp4", 0, 1, detected_midi=60.2,
                    extra_takes=[Take("a.mp4", 2, 3, detected_midi=59.9), Take("b.mp4", 0, 1, detected_midi=60.0)])
    p = make_plan([NoteEvent(i, i + 0.5, 60) for i in range(5)], {60: slot})
    assert [i.take for i in p.instances] == [0, 1, 2, 0, 1]
    assert [round(i.shift, 3) for i in p.instances[:3]] == [-0.2, 0.1, 0.0]
    assert slot.take(1).trim_start == 2 and slot.take(2).path == "b.mp4" and slot.take(4).trim_start == 2


def test_project_json_takes():
    p = Project()
    p.slots[60] = ClipSlot("long.mp4", 1.0, 1.5, extra_takes=[Take("long.mp4", 4.0, 4.6, 60.1)])
    q = Project.from_json(p.to_json())
    assert q.slots[60].take_count == 2 and q.slots[60].extra_takes[0].trim_start == 4.0
    old = Project.from_json('{"slots": {"60": {"path": "a.mp4", "trim_start": 0.1}}}')
    assert old.slots[60].extra_takes == []


def test_sources_keep_many_clips_of_one_video(long_video):
    from vsampler.render import sources
    path, _, _ = long_video
    a, b = ClipSlot(path, 0.5, 1.1), ClipSlot(path, 1.6, 2.2)
    sa, sb = sources.get_source(a), sources.get_source(b)
    assert sources.get_source(a) is sa and sources.get_source(b) is sb


def test_render_from_one_long_video(long_video, tmp_path):
    from vsampler.render.renderer import prepare
    path, _, res = long_video
    proj = Project()
    for i in sorted(clipfinder.best_takes(res.candidates)):
        c = res.candidates[i]
        if c.kind == "note":
            takes = [Take(path, o.start, o.end, o.midi, o.loudness_db) for o in res.candidates
                     if o is not c and o.note == c.note]
            proj.slots[c.note] = ClipSlot(path, c.start, c.end, c.midi, loudness_db=c.loudness_db, extra_takes=takes)
    assert proj.slots[60].take_count == 2
    mid = make_midi(tmp_path / "s.mid", [(60, 0, 0.5), (64, 0.5, 1.0), (60, 1.0, 1.5), (67, 1.5, 2.0)])
    song = load_song(mid, proj.song)
    prep = prepare(proj, song)
    assert [(i.target, i.take) for i in prep.plan.instances] == [(60, 0), (64, 0), (60, 1), (67, 0)]
    assert (60, 1) in prep.sources and prep.sources[(60, 0)].trim_start != prep.sources[(60, 1)].trim_start
    proj.render.width, proj.render.height = 320, 180
    out = str(tmp_path / "o.mp4")
    render_video(proj, song, out)
    a = load_audio(out)
    pitch, _ = detect_pitch(a[:, int(0.6 * SR): int(0.95 * SR)])
    assert abs(pitch - 64) < 0.3


# ---------------------------------------------------------------- quick previews
def test_audio_preview_is_limited_and_skips_video(media, tmp_path, monkeypatch):
    from vsampler.render import renderer, sources
    _d, clips, _ = media
    proj = Project()
    for m, path in clips.items():
        a, _ = analyze_clip(path)
        proj.slots[m] = ClipSlot(path, a.trim_start, a.trim_end, a.detected_midi)
    proj.song.allow_pitch_shift = True
    mid = make_midi(tmp_path / "long.mid", [(60 + (i % 3) * 4, i * 0.5, i * 0.5 + 0.4) for i in range(40)])  # 20 s
    song = load_song(mid, proj.song)
    monkeypatch.setattr(sources, "ClipFrames", lambda *a, **k: pytest.fail("audio preview decoded video frames"))
    sources._sources.clear()
    out = renderer.render_audio_preview(proj, song, str(tmp_path / "p.wav"), max_seconds=5)
    a = load_audio(out)
    assert 5.0 <= a.shape[1] / SR <= 5.8                       # just the start, with a short fade-out
    assert np.abs(a[:, -100:]).max() < 0.02
    whole = load_audio(renderer.render_audio_preview(proj, song, str(tmp_path / "w.wav")))
    assert whole.shape[1] / SR > 19
