"""Shorts output, text overlays, song excerpts, the automatic project builder and the command line."""
import json
import sys
from pathlib import Path

import av
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_clip, make_long_clip, make_midi  # noqa: E402

from vsampler import clipfinder, cli
from vsampler.auto import AutoOptions, auto_project
from vsampler.clips import SR, load_audio, measure_loudness
from vsampler.importers import load_song
from vsampler.models import (ClipSlot, NoteEvent, Project, RenderSettings, Song, SongOptions, TextOverlay, Track,
                             apply_preset)
from vsampler.render.overlays import HOOK_BIG_SECONDS, OverlayPainter, safe_area
from vsampler.render.renderer import prepare, set_loudness
from vsampler.songops import apply_options

NOTES = [(60, 0.5, 0.6), (64, 1.6, 0.6), (67, 2.7, 0.5), (60, 3.7, 0.6), (72, 4.8, 0.5), (64, 5.8, 0.6)]


@pytest.fixture(scope="module")
def speech(tmp_path_factory):
    """A long video holding a few notes, analysed once."""
    d = tmp_path_factory.mktemp("auto")
    path = make_long_clip(d / "speech.mp4", NOTES, [7.0, 7.8], total=8.8)
    found = [(path, c) for c in clipfinder.analyse(load_audio(path)).candidates]
    return d, path, found


# ---------------------------------------------------------------- presets + project files
def test_shorts_preset_and_overlays_saved():
    p = Project()
    apply_preset(p.render, "shorts")
    assert (p.render.width, p.render.height, p.render.preset) == (1080, 1920, "shorts")
    assert p.render.max_duration and p.render.max_duration < 60
    p.render.overlays = [TextOverlay("Hook!", 0, None, "hook"), TextOverlay("bye", -3, None, "cta")]
    p.render.outro = "me.mp4"
    p.song.start_s, p.song.end_s = 12.5, 40.0
    q = Project.from_json(p.to_json())
    assert q.render.overlays == p.render.overlays and isinstance(q.render.overlays[0], TextOverlay)
    assert (q.render.outro, q.song.start_s, q.song.end_s) == ("me.mp4", 12.5, 40.0)
    old = Project.from_json('{"render": {"width": 640, "height": 360, "new_thing": 1}}')
    assert old.render.overlays == [] and old.render.preset == "" and old.song.end_s is None
    with pytest.raises(ValueError):
        apply_preset(RenderSettings(), "cinema")


# ---------------------------------------------------------------- song excerpt
def test_song_excerpt():
    events = [NoteEvent(i, i + 0.8, 60 + i, track=0) for i in range(10)]
    song = Song("x.mid", "midi", events, [Track(0, "Lead", 10, 60, 69)])
    evs = apply_options(song, SongOptions(start_s=3.0, end_s=6.5))
    assert [e.pitch for e in evs] == [63, 64, 65, 66]
    assert evs[0].start == pytest.approx(0.0) and evs[-1].start == pytest.approx(3.0)
    assert evs[-1].end == pytest.approx(3.5)                       # cut off at the end of the excerpt
    half = apply_options(song, SongOptions(start_s=3.0, end_s=6.5, tempo_pct=200))
    assert half[-1].start == pytest.approx(1.5)


# ---------------------------------------------------------------- overlays
def test_overlays_stay_in_the_safe_area():
    rs = apply_preset(RenderSettings(), "shorts")
    rs.width, rs.height = 540, 960
    rs.overlays = [TextOverlay("A really long hook line that has to wrap onto a few lines 💀", 0, None, "hook"),
                   TextOverlay("caption", 1, 2, "caption"), TextOverlay("make one for you", -2, None, "cta"),
                   TextOverlay("@me", 0, None, "watermark")]
    painter = OverlayPainter(rs, 10.0)
    blank = np.zeros((960, 540, 3), np.uint8)
    x0, y0, x1, y1 = safe_area(540, 960)
    for t in (0.5, 1.5, 9.0):
        img = painter.apply(blank, t)
        assert img is not blank and img.any()
        drawn = np.argwhere(img.any(axis=2))
        assert drawn[:, 0].min() >= y0 and drawn[:, 0].max() < y1      # rows
        assert drawn[:, 1].min() >= x0 and drawn[:, 1].max() < x1      # columns
    assert not blank.any()                                              # the frame passed in is untouched
    big = (painter.apply(blank, 1.0)[:, :, :] > 0).any(axis=(1, 2)).sum()
    small = (painter.apply(blank, HOOK_BIG_SECONDS + 1)[:, :, :] > 0).any(axis=(1, 2)).sum()
    assert small < big                                                  # the hook shrinks after a few seconds
    cta_shown = painter.apply(blank, 8.5)[400:560].any()
    cta_hidden = painter.apply(blank, 7.0)[400:560].any()
    assert cta_shown and not cta_hidden                                 # negative start counts from the end
    assert OverlayPainter(RenderSettings(), 5.0).apply(blank, 1.0) is blank


def test_set_loudness_never_clips():
    t = np.arange(SR) / SR
    quiet = np.stack([0.05 * np.sin(2 * np.pi * 220 * t)] * 2).astype(np.float32)
    loud = set_loudness(quiet, -14.0, SR)
    assert measure_loudness(loud) == pytest.approx(-14.0, abs=1.0)
    assert np.abs(loud).max() <= 1.0


# ---------------------------------------------------------------- length limit
def test_max_duration_cuts_the_song(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 60)
    proj = Project()
    proj.slots[60] = ClipSlot(clip, 0.2, 0.8, 60.0)
    proj.render.max_duration, proj.render.tail = 1.2, 0.5
    song = load_song(make_midi(tmp_path / "s.mid", [(60, i * 0.5, i * 0.5 + 0.9) for i in range(8)]), proj.song)
    prep = prepare(proj, song)
    assert all(i.start < 1.2 and i.start + i.duration <= 1.2 + 1e-6 for i in prep.plan.instances)
    assert prep.duration == pytest.approx(1.7)


# ---------------------------------------------------------------- automatic build
def test_auto_project_picks_clips_and_key(speech, tmp_path):
    _d, path, found = speech
    # the tune is two semitones above the notes in the video, so the builder should shift it down by 2
    mid = make_midi(tmp_path / "tune.mid", [(62, 0, 0.5), (66, 0.5, 1.0), (69, 1.0, 1.5), (62, 1.5, 2.0),
                                            (74, 2.0, 2.5)])
    project, song, report = auto_project([path], mid, AutoOptions(max_fragment=0.3), found=found)
    assert report.transpose == -2 and project.song.transpose == -2
    assert report.coverage == 1.0 and report.missing == 0 and report.exact == 5
    assert sorted(project.slots) == [60, 64, 67, 72] and project.slots[60].take_count == 2
    assert report.longest_clip <= 0.3 + 1e-6
    assert all(c.path == path and c.end - c.start <= 0.3 + 1e-6 for c in report.clips)
    assert project.song.octave_jump and project.song.allow_pitch_shift
    assert project.drum_slots          # the song has a drum track and the video has hits


def test_auto_project_without_pitched_sounds(tmp_path):
    hits_only = make_long_clip(tmp_path / "claps.mp4", [], [0.5, 1.2, 2.0], total=3.0)
    mid = make_midi(tmp_path / "t.mid", [(60, 0, 0.5)])
    with pytest.raises(ValueError):
        auto_project([hits_only], mid)


# ---------------------------------------------------------------- command line
def test_cli_auto_renders_a_short(speech, tmp_path):
    _d, path, _found = speech
    mid = make_midi(tmp_path / "tune.mid", [(60, 0, 0.5), (64, 0.5, 1.0), (67, 1.0, 1.5), (72, 1.5, 2.0)])
    overlays = tmp_path / "o.json"
    overlays.write_text(json.dumps([{"text": "Hook", "style": "hook"}, {"text": "Your turn", "start": -1,
                                                                         "style": "cta"}]), encoding="utf-8")
    outro = make_clip(tmp_path / "outro.mp4", 67, seconds=0.8, lead=0.0)
    out = tmp_path / "short.mp4"
    code = cli.main(["auto", "--videos", path, "--song", mid, "--preset", "shorts", "--overlays", str(overlays),
                     "--outro", outro, "--out", str(out), "--no-drums"])
    assert code == cli.EXIT_OK
    report = json.loads(out.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["coverage"] == 1.0
    saved = Project.load(out.with_suffix(".vsproj"))
    assert saved.render.preset == "shorts" and saved.render.overlays[1].text == "Your turn"
    with av.open(str(out)) as c:
        v = c.streams.video[0]
        assert (v.codec_context.width, v.codec_context.height) == (1080, 1920)
        song_part = 2.0 + saved.render.tail
        assert float(c.duration / 1e6) == pytest.approx(song_part + 1.2, abs=0.25)   # the song, then the outro

    low = tmp_path / "low.mp4"
    code = cli.main(["auto", "--videos", path, "--song", mid, "--min-coverage", "1.01", "--out", str(low)])
    assert code == cli.EXIT_LOW_COVERAGE and not low.exists() and low.with_suffix(".vsproj").exists()


def test_cli_old_render_form_and_errors(tmp_path):
    out = tmp_path / "x.mp4"
    assert cli.main(["--render", str(tmp_path / "missing.vsproj"), str(out)]) == cli.EXIT_FAILED
    assert "FAILED" in (tmp_path / "x.mp4.log").read_text(encoding="utf-8")
    assert cli.main(["auto", "--song", "x.mid"]) == 2      # --videos and --out are required
