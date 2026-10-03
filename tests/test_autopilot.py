"""The autopilot package: ideas, themes, songs, writer, judge, the run state machine and the review page."""
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_clip, make_long_clip, make_midi  # noqa: E402

from autopilot import config as config_mod  # noqa: E402
from autopilot import ideas, judge, seasons, songs, writer  # noqa: E402
from vsampler.models import NoteEvent, Song, TextOverlay, Track  # noqa: E402
from vsampler.render.burn import burn_overlays  # noqa: E402

TUNE = [60, 64, 67, 64, 60, 67, 72, 67]


# ---------------------------------------------------------------- adding text without re-rendering
def test_burn_keeps_the_sound_and_adds_text(tmp_path):
    base = make_clip(tmp_path / "base.mp4", 64, seconds=1.5, size=(270, 480))
    out = burn_overlays(base, [TextOverlay("Hello there", 0, None, "hook")], str(tmp_path / "final.mp4"))

    def audio(f):
        with av.open(f) as c:
            return [bytes(p) for p in c.demux(c.streams.audio[0]) if p.size]

    def frame(f, n=20):
        with av.open(f) as c:
            for i, fr in enumerate(c.decode(video=0)):
                if i == n:
                    return fr.to_ndarray(format="bgr24")

    assert audio(out) == audio(base)                       # the sound is copied, not re-encoded
    a, b = frame(base).astype(int), frame(out).astype(int)
    top, bottom = np.abs(a - b)[40:200].mean(), np.abs(a - b)[350:].mean()
    assert top > 10 and bottom < 3                         # the hook is drawn at the top, nothing else changes
    with av.open(out) as c, av.open(base) as d:
        assert c.streams.video[0].frames == d.streams.video[0].frames


# ---------------------------------------------------------------- ideas, themes, songs
IDEAS_TEXT = """😂 Politicians
Donald Trump singing “Fireflies” — Owl City — already an elite combination
Vladimir Putin singing “Call Me Maybe” — Carly Rae Jepsen
The Beatles singing a modern TikTok song
🏆 My top 10
Putin — “Call Me Maybe”
"""


def test_ideas_are_parsed_and_favourites_marked():
    xs = ideas.parse(IDEAS_TEXT)
    assert [(i.who, i.song, i.artist) for i in xs] == [("Donald Trump", "Fireflies", "Owl City"),
                                                       ("Vladimir Putin", "Call Me Maybe", "Carly Rae Jepsen")]
    assert xs[0].category == "Politicians" and xs[0].note == "already an elite combination"
    assert [i.favourite for i in xs] == [False, True]
    assert ideas.find(xs, "Donald Trump — Fireflies") is xs[0]
    picked = ideas.choose(xs, lambda i: True, date(2026, 10, 3), used=set(), recent_people=set())
    assert picked is xs[1]                                 # favourites first
    assert ideas.choose(xs, lambda i: True, date(2026, 10, 3), {xs[1].id}, set()) is xs[0]
    assert ideas.choose(xs, lambda i: False, date(2026, 10, 3), set(), set()) is None


def test_theme_dates():
    assert seasons.easter(2027) == date(2027, 3, 28)
    assert seasons.on("easter-21", 2027) == date(2027, 3, 7)                  # UK Mother's Day
    assert seasons.on("second sunday of may", 2027) == date(2027, 5, 9)
    assert seasons.on("last monday of may", 2026) == date(2026, 5, 25)
    assert seasons.on("us-election", 2026) == date(2026, 11, 3) and seasons.on("us-election", 2027) is None
    themes = seasons.load(Path("autopilot/seasons.yaml"))
    assert [t.name for t in seasons.active(themes, date(2026, 12, 20))] == ["christmas"]
    assert "mothers_day_uk" in [t.name for t in seasons.active(themes, date(2027, 2, 20))]
    assert seasons.active(themes, date(2027, 8, 20)) == []


def test_chorus_is_the_repeated_part():
    rng = np.random.default_rng(3)
    events = [NoteEvent(i * 0.5, i * 0.5 + 0.4, int(rng.integers(55, 70))) for i in range(16)]     # verse: 0-8 s
    for rep in range(3):                                                                            # chorus x3
        t0 = 8 + rep * 4
        events += [NoteEvent(t0 + i * 0.5, t0 + i * 0.5 + 0.4, p) for i, p in enumerate(TUNE)]
    song = Song("x.mid", "midi", events, [Track(0, "Lead", len(events), 55, 72)])
    sec = songs.find_chorus(song, seconds=6)
    assert 7.8 <= sec.start <= 8.05 and sec.repeats >= 3
    assert sec.end <= sec.start + 6.01


def test_song_files_are_found_by_title(tmp_path):
    (tmp_path / "fireflies-owl-city.mxl").write_text("x")
    (tmp_path / "never-gonna-give-you-up.mid").write_text("x")
    assert songs.find_file("Fireflies", [tmp_path]).name == "fireflies-owl-city.mxl"
    assert songs.find_file("Never Gonna Give You Up", [tmp_path]).suffix == ".mid"
    assert songs.find_file("Barbie Girl", [tmp_path]) is None


# ---------------------------------------------------------------- writer and judge
CTX = {"person": "Test Person", "person_kind": "politician", "song": {"title": "Test Tune", "artist": "Nobody"},
       "themes": [], "duration": 12.0, "song_end": 10.0, "frames": [],
       "phrases": [{"start": 0.1, "end": 2.0, "has_highest_note": False}, {"start": 2.5, "end": 4.0},
                   {"start": 4.5, "end": 6.0, "has_highest_note": True}, {"start": 6.5, "end": 9.5}]}


class FakeClient:
    """Stands in for anthropic.Anthropic(): returns the queued JSON answers in turn."""

    def __init__(self, *answers, stop="end_turn"):
        self.answers, self.calls, self.stop = list(answers), [], stop
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = json.dumps(self.answers.pop(0))
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=text)])


def answer(**over):
    d = {"hook": "nobody: / the senate at 3am 🦅", "captions": [{"text": "the high note though", "start": 4.5,
                                                                    "end": 6.0}],
         "cta": "ok now imagine your nan doing this", "title": "t", "description": "Parody.", "hashtags": ["a"]}
    d.update(over)
    return d


def test_writer_asks_claude_and_fixes_problems():
    cfg = config_mod.load("missing.yaml")
    long = answer(captions=[{"text": "this caption is far far far too long to read", "start": 1, "end": 3}])
    client = FakeClient(long, answer())
    d = writer.write(CTX, cfg, note="more eagles", client=client)
    assert d.source == "claude" and d.problems == [] and d.captions[0]["text"] == "the high note though"
    assert len(client.calls) == 2                                          # one retry with the problems listed
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["output_config"]["format"]["type"] == "json_schema"
    assert any("more eagles" in b.get("text", "") for b in first["messages"][0]["content"])
    assert "too long" in client.calls[1]["messages"][-1]["content"]
    with pytest.raises(writer.WriterError):
        writer.write(CTX, cfg, client=FakeClient(answer(), stop="refusal"))


def test_template_text_and_overlays(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    cfg = config_mod.load("missing.yaml")
    d = writer.write(CTX, cfg)
    assert d.source == "template" and writer.check(d, CTX) == []
    ovs = writer.to_overlays(d, CTX, "@me")
    assert [o["style"] for o in ovs][0] == "hook" and ovs[-1] == {"text": "@me", "style": "watermark",
                                                                    "start": 0.0, "end": None}
    assert [TextOverlay.from_dict(o) for o in ovs]
    back = writer.from_overlays(ovs, d)
    assert back.hook == d.hook and back.captions == [{**c, "start": float(c["start"]), "end": float(c["end"])}
                                                      for c in sorted(d.captions, key=lambda c: c["start"])]


def test_judge_rules(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = config_mod.load("missing.yaml")
    good = writer.Draft(**{k: v for k, v in answer().items()})
    assert judge.judge(good, CTX, cfg) == (True, [])
    bad = writer.Draft(**answer(hook="not one note was added", captions=[{"text": "he admits it", "start": 1,
                                                                           "end": 2}]))
    ok, reasons = judge.judge(bad, CTX, cfg)
    assert not ok and any("brag" in r for r in reasons) and any("claim" in r for r in reasons)
    ok, reasons = judge.judge(good, CTX, cfg, client=FakeClient({"ok": False, "reasons": ["no"]}))
    assert not ok and reasons == ["Judge: no"]


# ---------------------------------------------------------------- the whole run and the review page
@pytest.fixture(scope="module")
def project_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("autopilot")
    notes, t = [], 0.4
    for rep in range(3):                                   # three takes of every note
        for p in (60, 64, 67, 72):
            notes.append((p, t, 0.5))
            t += 0.9
    make_long_clip(root / "speech.mp4", notes, total=t + 0.5)
    tune = [(p, i * 0.5, i * 0.5 + 0.45) for i, p in enumerate(TUNE * 3)]
    make_midi(root / "test-tune.mid", tune)
    (root / "ideas.txt").write_text("Test Person singing “Test Tune” — Nobody\n", encoding="utf-8")
    (root / "people.yaml").write_text("- name: Test Person\n  tier: 1\n  kind: politician\n"
                                      "  footage: ['speech.mp4']\n", encoding="utf-8")
    return root


def make_cfg(root: Path, data: str, **over):
    return config_mod.load(str(root / "none.yaml"), root=str(root), data_dir=data, ideas_file="ideas.txt",
                           people_file="people.yaml", seasons_file=str(Path("autopilot/seasons.yaml").resolve()),
                           song_dirs=["."], transcribe=False, min_coverage=0.5,
                           video={"seconds": 4, "layout": "dynamic", "size": [270, 480]}, **over)


def test_review_flow(project_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from autopilot.pipeline import Pipeline
    from autopilot.review.app import create_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pipe = Pipeline(make_cfg(project_dir, "data1"))
    run = pipe.build(pipe.new_run(date(2026, 10, 3)))
    assert run.state == "built" and (pipe.dir(run) / "base.mp4").exists() and run.report["coverage"] >= 0.5
    web = TestClient(create_app(pipe, background=False))
    url = f"/api/run/{run.id}"
    assert web.get(url).status_code == 403
    assert web.get(url, params={"token": "wrong"}).status_code == 403
    q = {"token": run.token}
    s = web.get(url, params=q).json()
    assert s["state"] == "built" and set(s["actions"]) >= {"continue", "regenerate_video"} and "base" in s["videos"]
    assert web.get(f"/run/{run.id}", params=q).status_code == 200
    assert web.get(s["videos"]["base"]).headers["content-type"] == "video/mp4"

    # 1: a different take of the same idea
    s = web.post(url + "/action", params=q, json={"action": "regenerate_video"}).json()
    assert s["state"] == "built" and pipe.load(run.id).seed == 1 and pipe.load(run.id).version == 2

    # 2: the text is drafted (template, no key), edited, previewed, then applied
    s = web.post(url + "/action", params=q, json={"action": "continue"}).json()
    assert s["state"] == "text_drafted" and s["overlays"][0]["style"] == "hook" and s["draft"]["source"] == "template"
    assert web.post(url + "/action", params=q, json={"action": "approve"}).status_code == 409
    edited = [{"text": "my own hook", "style": "hook", "start": 0, "end": None},
              {"text": "my caption", "style": "caption", "start": 0.5, "end": 2.0}]
    s = web.post(url + "/action", params=q, json={"action": "save_text", "overlays": edited}).json()
    assert s["overlays"][0]["text"] == "my own hook" and s["draft"]["source"] == "edited"
    img = web.get(url + "/frame", params={**q, "t": 1.0})
    assert img.headers["content-type"] == "image/jpeg" and len(img.content) > 1000
    s = web.post(url + "/action", params=q, json={"action": "apply_text"}).json()
    assert s["state"] == "final" and "final" in s["videos"]

    # 3: back to the text, change it, apply again (no re-render), approve
    base_mtime = (pipe.dir(run) / "base.mp4").stat().st_mtime
    web.post(url + "/action", params=q, json={"action": "back_to_text"})
    edited[1]["text"] = "changed caption"
    s = web.post(url + "/action", params=q, json={"action": "apply_text", "overlays": edited}).json()
    assert s["state"] == "final" and (pipe.dir(run) / "base.mp4").stat().st_mtime == base_mtime
    s = web.post(url + "/action", params=q, json={"action": "approve"}).json()
    assert s["state"] == "approved" and s["actions"] == []
    meta = json.loads((pipe.dir(run) / "metadata.json").read_text(encoding="utf-8"))
    assert meta["overlays"][1]["text"] == "changed caption" and meta["title"]

    # a restart picks the run up where it was; the idea now counts as used
    again = Pipeline(make_cfg(project_dir, "data1"))
    assert again.load(run.id).state == "approved"
    assert "test-person-test-tune" in again.db.used_ideas()
    assert again.new_run(date(2026, 10, 4)).state == "failed"         # nothing left that can be made


def test_auto_mode_and_expiry(project_dir, monkeypatch):
    from autopilot.pipeline import Pipeline

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pipe = Pipeline(make_cfg(project_dir, "data2", publish_mode="auto"))
    run = pipe.run_auto(pipe.new_run(date(2026, 10, 3)))
    assert run.state == "approved" and (pipe.dir(run) / "final.mp4").exists()

    pipe = Pipeline(make_cfg(project_dir, "data3", review={"deadline_minutes": 0, "host": "x", "port": 1}))
    run = pipe.new_run(date(2026, 10, 3), idea="Test Person — Test Tune")
    run.state, run.created = "built", run.created - 10
    assert pipe.expire_if_due(run).state == "expired"


def test_review_server_restart_clears_a_stuck_job(project_dir):
    from fastapi.testclient import TestClient

    from autopilot.pipeline import Pipeline
    from autopilot.review.app import create_app

    pipe = Pipeline(make_cfg(project_dir, "data4"))
    run = pipe.new_run(date(2026, 10, 3), idea="Test Person — Test Tune")
    run.state, run.busy = "built", "Adding the text…"          # the server stopped half way through a job
    pipe.save(run)
    web = TestClient(create_app(pipe, background=False))
    s = web.get(f"/api/run/{run.id}", params={"token": run.token}).json()
    assert s["busy"] == "" and "interrupted" in s["error"] and "continue" in s["actions"]


def test_song_parts_offer_different_parts():
    verse = [62, 65, 69, 65, 62, 60, 59, 57]
    events, t = [], 0.0
    for block in (verse, TUNE, TUNE, verse, TUNE, TUNE):        # verse, chorus x2, verse, chorus x2
        for p in block:
            events.append(NoteEvent(t, t + 0.4, p))
            t += 0.5
    song = Song("x.mid", "midi", events, [Track(0, "Lead", len(events), 57, 72)])
    parts = songs.song_parts(song, seconds=6)
    assert 3.9 <= parts[0].start <= 4.05                          # the chorus first
    assert any(abs(p.start - 0.0) < 0.2 for p in parts)           # the verse / start of the song is offered too
    assert len({round(p.start) for p in parts}) == len(parts) >= 2
    assert all(p.end - p.start <= 6.01 for p in parts)


def test_review_can_use_another_part_of_the_song(project_dir, monkeypatch):
    import yaml
    from fastapi.testclient import TestClient

    from autopilot.pipeline import Pipeline
    from autopilot.review.app import create_app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pipe = Pipeline(make_cfg(project_dir, "data5"))
    run = pipe.build(pipe.new_run(date(2026, 10, 3)))
    web = TestClient(create_app(pipe, background=False))
    url, q = f"/api/run/{run.id}", {"token": run.token}
    s = web.get(url, params=q).json()
    assert s["parts"] and s["parts"][0]["label"] and s["song_length"] > 10 and "change_section" in s["actions"]
    web.post(url + "/action", params=q, json={"action": "continue"})           # text drafted for the old part

    bad = web.post(url + "/action", params=q, json={"action": "change_section", "start": 3, "end": 4})
    assert bad.status_code == 400 and "seconds long" in bad.json()["detail"]
    s = web.post(url + "/action", params=q, json={"action": "change_section", "start": 6.0, "end": 11.0,
                                                  "remember": True}).json()
    assert s["state"] == "built" and s["section"] == [6.0, 11.0] and s["overlays"] == []
    assert pipe.load(run.id).version == 2
    saved = yaml.safe_load((project_dir / "data5" / "songs.yaml").read_text(encoding="utf-8"))
    assert saved["test-tune"]["start"] == 6.0 and saved["test-tune"]["chosen"]
    # a new run of the same song now uses the remembered part
    again = pipe.build(pipe.new_run(date(2026, 10, 4), idea="Test Person — Test Tune"))
    assert (again.plan["start"], again.plan["end"]) == (6.0, 11.0)
