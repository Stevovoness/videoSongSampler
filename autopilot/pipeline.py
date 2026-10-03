"""One run = one day's video, as a small state machine saved in runs/<id>/state.json after every step.

    picked -> built (video without text)          [review 1: watch it; regenerate, or continue]
           -> text_drafted (writer + judge)       [review 2: edit the text, regenerate it, or apply it]
           -> final (text burned on, in seconds)  [review 3: approve, back to the text, or regenerate]
           -> approved                            (publishing comes later)
    any review step -> skipped; an unanswered review -> expired; errors -> failed
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from vsampler.models import Project, TextOverlay
from vsampler.render.burn import burn_overlays

from . import build as build_mod
from . import ideas as ideas_mod
from . import judge as judge_mod
from . import outro as outro_mod
from . import seasons as seasons_mod
from . import writer
from .config import Config
from .context import Transcriber, build_context
from .db import DB

WAITING = {"built", "text_drafted", "final"}          # states where the run waits for you
FINISHED = {"approved", "skipped", "expired", "failed"}
ACTIONS = {
    "built": {"continue", "regenerate_video", "regenerate_idea", "change_section", "skip"},
    "text_drafted": {"save_text", "apply_text", "regenerate_text", "regenerate_video", "regenerate_idea",
                     "change_section", "skip"},
    "final": {"approve", "back_to_text", "save_text", "apply_text", "regenerate_text", "regenerate_video",
              "regenerate_idea", "change_section", "skip"},
}
MIN_PART, MAX_PART = 5.0, 60.0           # seconds of song a video may use
MAX_IDEA_TRIES = 5


class InvalidAction(Exception):
    pass


@dataclass
class Run:
    id: str
    day: str
    created: float
    token: str
    state: str = "picked"
    idea: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)
    tried_ideas: list[str] = field(default_factory=list)
    seed: int = 0
    section: list[float] | None = None   # the part of the song you chose (None: the chorus found automatically)
    parts: list[dict] = field(default_factory=list)   # suggested parts of the song: start, end, repeats
    song_length: float = 0.0
    themes: list[dict] = field(default_factory=list)
    outro: str = ""
    draft: dict = field(default_factory=dict)
    overlays: list[dict] = field(default_factory=list)
    judge: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    busy: str = ""
    progress: float = 0.0
    error: str = ""
    version: int = 0                     # changes whenever a video file changes (for the review page)
    log: list[str] = field(default_factory=list)

    @property
    def actions(self) -> list[str]:
        return sorted(ACTIONS.get(self.state, set())) if not self.busy else []


class Pipeline:
    def __init__(self, cfg: Config, client=None, transcriber: Transcriber | None = None):
        self.cfg = cfg
        self.client = client                  # Claude client (tests pass a stand-in); None = from environment
        self.db = DB(cfg.data / "autopilot.db")
        self._transcriber = transcriber
        self._save_lock = threading.Lock()
        self._last_save = 0.0

    # ------------------------------------------------------------------ storage
    def dir(self, run: Run | str) -> Path:
        return self.cfg.runs / (run if isinstance(run, str) else run.id)

    def save(self, run: Run) -> None:
        with self._save_lock:
            d = self.dir(run)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "state.json.tmp"
            tmp.write_text(json.dumps(asdict(run), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(d / "state.json")

    def load(self, run_id: str) -> Run:
        data = json.loads((self.dir(run_id) / "state.json").read_text(encoding="utf-8"))
        return Run(**{k: v for k, v in data.items() if k in Run.__dataclass_fields__})

    def runs(self) -> list[Run]:
        out = []
        for d in sorted(self.cfg.runs.iterdir(), reverse=True):
            if (d / "state.json").exists():
                out.append(self.load(d.name))
        return out

    def _note(self, run: Run, msg: str) -> None:
        run.log.append(f"{datetime.now():%H:%M:%S} {msg}")

    def _progress(self, run: Run):
        def prog(f: float, m: str) -> None:
            run.progress, run.busy = round(f, 3), m or run.busy
            if time.time() - self._last_save > 0.5:
                self._last_save = time.time()
                self.save(run)
        return prog

    # ------------------------------------------------------------------ ideas
    def all_ideas(self) -> list[ideas_mod.Idea]:
        saved = self.cfg.data / "ideas.yaml"
        if not saved.exists():
            ideas_mod.save(ideas_mod.load(self.cfg.path(self.cfg.ideas_file)), saved)
        return ideas_mod.load(saved)

    def _themes(self, day: date) -> list[dict]:
        return [asdict(t) for t in seasons_mod.active(seasons_mod.load(self.cfg.path(self.cfg.seasons_file)), day)]

    def _pick_idea(self, run: Run, query: str | None = None) -> ideas_mod.Idea | None:
        everything = self.all_ideas()
        if query:
            found = ideas_mod.find(everything, query)
            if found is None:
                parsed = ideas_mod.parse(query.replace(" — ", " singing “", 1) + "”") if "—" in query else []
                found = parsed[0] if parsed else None
            return found
        day = date.fromisoformat(run.day)
        words = {w for t in run.themes for w in t.get("words", [])}
        used = self.db.used_ideas() | set(run.tried_ideas)
        recent = self.db.recent_people(day, self.cfg.person_cooldown_days)
        return ideas_mod.choose(everything, lambda i: build_mod.can_build(self.cfg, i), day, used, recent, words)

    # ------------------------------------------------------------------ steps
    def new_run(self, day: date | None = None, idea: str | None = None) -> Run:
        day = day or date.today()
        n = sum(1 for d in self.cfg.runs.glob(f"{day.isoformat()}-*"))
        run = Run(f"{day.isoformat()}-{n + 1}", day.isoformat(), time.time(), secrets.token_urlsafe(16))
        run.themes = self._themes(day)
        picked = self._pick_idea(run, idea)
        if picked is None:
            run.state, run.error = "failed", "No idea can be made right now (footage or song files missing)."
        else:
            run.idea = asdict(picked)
            self._note(run, f"Picked {picked.label}")
        self.save(run)
        return run

    def build(self, run: Run, new_idea: bool = False, keep_idea: bool = False) -> Run:
        """Make the video without text; on a problem, move on to another idea (up to MAX_IDEA_TRIES).
        keep_idea: raise CannotBuild instead of moving on (e.g. when you chose the song part yourself)."""
        names = [t["name"] for t in run.themes]
        run.outro = outro_mod.pick(self.cfg.data / "outros", names, date.fromisoformat(run.day))
        for _ in range(MAX_IDEA_TRIES):
            if new_idea or not run.idea:
                if run.idea:
                    run.tried_ideas.append(run.idea["id"])
                picked = self._pick_idea(run)
                if picked is None:
                    break
                run.idea, run.seed, new_idea = asdict(picked), 0, False
                run.section, run.parts = None, []
                self._note(run, f"Picked {picked.label}")
            idea = ideas_mod.Idea(**run.idea)
            try:
                run.busy, run.progress = f"Building {idea.label}…", 0.0
                self.save(run)
                plan = build_mod.plan_for(self.cfg, idea, self.db.blocked_channels())
                plan.seed = run.seed or None
                if run.section:
                    plan.start, plan.end = run.section
                if not run.parts:
                    parts, run.song_length = build_mod.library(self.cfg).parts(Path(plan.song_path),
                                                                               self.cfg.video["seconds"])
                    run.parts = [asdict(x) for x in parts]
                out = build_mod.build(self.cfg, plan, self.dir(run), run.outro, self._progress(run))
            except build_mod.CannotBuild as e:
                self._note(run, f"Can't build {idea.label}: {e}")
                if keep_idea:
                    raise
                new_idea = True
                continue
            run.plan, run.report = asdict(plan), out["report"]
            run.state, run.busy, run.error = "built", "", ""
            run.draft, run.overlays, run.judge = {}, [], {}
            run.version += 1
            (self.dir(run) / "context.json").unlink(missing_ok=True)
            (self.dir(run) / "final.mp4").unlink(missing_ok=True)
            self.db.record(run.id, run.day, idea.id, plan.who, plan.song, "built")
            self._note(run, f"Built {idea.label}: coverage {out['report']['coverage']:.0%}")
            self.save(run)
            return run
        run.state, run.busy = "failed", ""
        run.error = "Couldn't build any idea: " + (run.log[-1] if run.log else "")
        self.db.record(run.id, run.day, run.idea.get("id"), None, None, "failed")
        self.save(run)
        return run

    def check_section(self, run: Run, start: float, end: float) -> str:
        """Why a part of the song can't be used ("" if it can)."""
        length = run.song_length or float("inf")
        if not 0 <= start < end <= length + 0.01:
            return f"Pick a part between 0 and {length:.0f} seconds, with the start before the end."
        if not MIN_PART <= end - start <= MAX_PART:
            return f"A part should be {MIN_PART:.0f} to {MAX_PART:.0f} seconds long."
        return ""

    def change_section(self, run: Run, start: float, end: float, remember: bool = False) -> Run:
        """Rebuild the video from another part of the song (and, with `remember`, use it for this song from now)."""
        start, end = round(float(start), 2), round(float(end), 2)
        problem = self.check_section(run, start, end)
        if problem:
            raise InvalidAction(problem)
        before = run.section
        run.section = [start, end]
        self._note(run, f"Song part {start:.1f}–{end:.1f} s")
        try:
            self.build(run, keep_idea=True)
        except build_mod.CannotBuild as e:
            run.section, run.busy = before, ""
            self.save(run)
            raise InvalidAction(f"That part of the song doesn't work with these clips: {e}") from None
        if remember and run.plan:
            build_mod.library(self.cfg).remember(run.plan["song"], Path(run.plan["song_path"]), start, end)
            self._note(run, "Remembered as this song's part")
            self.save(run)
        return run

    def ensure_parts(self, run: Run) -> Run:
        """Fill in the song's suggested parts for a run built before they were stored."""
        if run.plan and not run.parts and not run.busy:
            try:
                parts, run.song_length = build_mod.library(self.cfg).parts(Path(run.plan["song_path"]),
                                                                           self.cfg.video["seconds"])
                run.parts = [asdict(x) for x in parts]
                self.save(run)
            except Exception as e:  # noqa: BLE001 - the chooser just stays hidden
                self._note(run, f"Couldn't list the song's parts: {e}")
        return run

    def _context(self, run: Run) -> dict:
        f = self.dir(run) / "context.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        tr = self._transcriber
        if tr is None and self.cfg.transcribe:
            tr = self._transcriber = Transcriber(self.cfg.work / "transcripts", self.cfg.whisper_model)
        return build_context(self.dir(run), run.plan, run.idea, run.themes, tr, self._progress(run))

    def draft_text(self, run: Run, note: str = "") -> Run:
        run.busy, run.progress = "Working out what happens in the video…", 0.0
        self.save(run)
        ctx = self._context(run)
        run.busy = "Writing the text…"
        self.save(run)
        try:
            d = writer.write(ctx, self.cfg, note, self.client)
        except writer.WriterError as e:
            self._note(run, f"Writer: {e}; using the template text")
            d = writer.template(ctx, self.cfg)
        if run.plan.get("credit"):
            d.description += f"\n{run.plan['credit']}"
        ok, reasons = judge_mod.judge(d, ctx, self.cfg, self.client)
        run.draft, run.judge = d.to_dict(), {"ok": ok, "reasons": reasons}
        run.overlays = writer.to_overlays(d, ctx, self.cfg.links.get("handle", ""))
        run.state, run.busy = "text_drafted", ""
        self._note(run, f"Text drafted ({d.source}); judge {'passed' if ok else 'flagged: ' + '; '.join(reasons)}")
        self.save(run)
        return run

    def save_text(self, run: Run, overlays: list[dict]) -> Run:
        clean = [{k: o.get(k) for k in ("text", "style", "start", "end")} for o in overlays if str(o.get("text", "")).strip()]
        for o in clean:
            o["start"] = float(o["start"] or 0)
            o["end"] = None if o["end"] in (None, "") else float(o["end"])
            TextOverlay.from_dict(o)                      # raises if a field is wrong
        run.overlays = clean
        if run.draft:
            d = writer.from_overlays(clean, writer.Draft(**{**run.draft, "problems": []}))
            ctx = json.loads((self.dir(run) / "context.json").read_text(encoding="utf-8")) \
                if (self.dir(run) / "context.json").exists() else {}
            ok, reasons = judge_mod.judge(d, ctx, self.cfg, None)     # rules only: fast, free
            run.draft = {**run.draft, "hook": d.hook, "captions": d.captions, "cta": d.cta, "source": "edited"}
            run.judge = {"ok": ok, "reasons": reasons}
        run.state = "text_drafted"
        self.save(run)
        return run

    def apply_text(self, run: Run) -> Run:
        run.busy, run.progress = "Adding the text…", 0.0
        self.save(run)
        project = Project.load(self.dir(run) / "project.vsproj")
        burn_overlays(str(self.dir(run) / "base.mp4"), [TextOverlay.from_dict(o) for o in run.overlays],
                      str(self.dir(run) / "final.mp4"), project.render, self._progress(run))
        run.state, run.busy = "final", ""
        run.version += 1
        self._note(run, "Text applied")
        self.save(run)
        return run

    def approve(self, run: Run) -> Run:
        meta = {"title": run.draft.get("title", ""), "description": run.draft.get("description", ""),
                "hashtags": run.draft.get("hashtags", []), "video": str(self.dir(run) / "final.mp4"),
                "idea": run.idea, "plan": run.plan, "overlays": run.overlays, "judge": run.judge,
                "coverage": run.report.get("coverage"), "approved_at": datetime.now().isoformat(timespec="seconds")}
        (self.dir(run) / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        run.state = "approved"
        self.db.record(run.id, run.day, run.idea.get("id"), run.plan.get("who"), run.plan.get("song"), "approved")
        self._note(run, "Approved")
        self.save(run)
        return run

    def skip(self, run: Run, why: str = "Skipped") -> Run:
        run.state, run.busy = "skipped", ""
        self.db.record(run.id, run.day, run.idea.get("id"), run.plan.get("who"), run.plan.get("song"), "skipped")
        self._note(run, why)
        self.save(run)
        return run

    def expire_if_due(self, run: Run) -> Run:
        """In approval mode, a run nobody answered in time is skipped for the day."""
        deadline = run.created + 60 * self.cfg.review["deadline_minutes"]
        if run.state in WAITING and not run.busy and time.time() > deadline:
            run.state = "expired"
            self.db.record(run.id, run.day, run.idea.get("id"), run.plan.get("who"), run.plan.get("song"), "expired")
            self._note(run, "No answer before the deadline: skipped for today")
            self.save(run)
        return run

    # ------------------------------------------------------------------ actions (from the review page)
    def act(self, run: Run, action: str, **kw) -> Run:
        """Run one review action (blocking). Raises InvalidAction if it isn't allowed right now."""
        if action not in ACTIONS.get(run.state, set()) or run.busy:
            raise InvalidAction(f"Can't {action.replace('_', ' ')} while the video is {run.state}.")
        try:
            if action == "continue":
                return self.draft_text(run)
            if action == "regenerate_video":
                run.seed += 1
                return self.build(run)
            if action == "regenerate_idea":
                return self.build(run, new_idea=True)
            if action == "change_section":
                return self.change_section(run, kw["start"], kw["end"], bool(kw.get("remember")))
            if action == "regenerate_text":
                return self.draft_text(run, kw.get("note", ""))
            if action == "save_text":
                return self.save_text(run, kw["overlays"])
            if action == "apply_text":
                if kw.get("overlays") is not None:
                    self.save_text(run, kw["overlays"])
                return self.apply_text(run)
            if action == "back_to_text":
                run.state = "text_drafted"
                self.save(run)
                return run
            if action == "approve":
                return self.approve(run)
            if action == "skip":
                return self.skip(run)
        except Exception as e:  # noqa: BLE001 - shown on the review page; the run stays where it was
            run.busy, run.error = "", f"{e}"
            self._note(run, "Error: " + traceback.format_exc(limit=3))
            self.save(run)
            raise
        raise InvalidAction(action)

    # ------------------------------------------------------------------ no review (publish_mode: auto)
    def run_auto(self, run: Run) -> Run:
        """Build, write, check and approve without stopping. The judge must pass (one rewrite allowed)."""
        for _ in range(2):
            if run.state not in ("built",):
                self.build(run, new_idea=bool(run.plan))
            if run.state == "failed":
                return run
            self.draft_text(run)
            if not run.judge.get("ok"):
                self.draft_text(run, "Fix these problems: " + "; ".join(run.judge.get("reasons", [])))
            if run.judge.get("ok"):
                self.apply_text(run)
                return self.approve(run)
            self._note(run, "The judge flagged the text twice: trying another idea")
            run.state = "picked"
        return self.skip(run, "The judge flagged every attempt: skipped for today")
