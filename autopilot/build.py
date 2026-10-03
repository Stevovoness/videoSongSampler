"""Turning an idea into a video without text: footage + song file + chorus -> `vsampler.auto` -> base.mp4."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from vsampler.auto import AutoOptions, auto_project, find_clips
from vsampler.models import RenderSettings, SongOptions, apply_preset
from vsampler.render.renderer import render_video

from . import footage as footage_mod
from .config import Config
from .ideas import Idea
from .songs import Library

ProgressFn = Callable[[float, str], None]


class CannotBuild(Exception):
    """This idea can't be made right now (no footage, no song file, or the clips cover too little)."""


@dataclass
class BuildPlan:
    idea_id: str
    who: str
    song: str
    artist: str
    song_path: str
    start: float
    end: float
    videos: list[str]
    tier: int
    kind: str
    credit: str = ""
    seed: int | None = None
    titles: dict[str, str] = field(default_factory=dict)    # footage file -> readable title


def library(cfg: Config) -> Library:
    return Library([cfg.path(d) for d in cfg.song_dirs], cfg.data / "songs.yaml")


def plan_for(cfg: Config, idea: Idea, blocked: set[str] = frozenset()) -> BuildPlan:
    """Everything needed to build `idea`, or CannotBuild saying what's missing."""
    people = footage_mod.load_people(cfg.path(cfg.people_file))
    person = footage_mod.find_person(people, idea.who)
    if person is None:
        raise CannotBuild(f"{idea.who} isn't in {cfg.people_file} yet (add where their footage comes from).")
    if person.tier not in cfg.footage_tiers:
        raise CannotBuild(f"{person.name}'s footage is tier {person.tier}, which isn't allowed in the settings.")
    lib = library(cfg)
    song = lib.find(idea.song, idea.artist)
    if song is None:
        raise CannotBuild(f"No song file for “{idea.song}”: add a .mid or .mxl to one of {cfg.song_dirs}.")
    videos = footage_mod.get_footage(person, cfg.root, cfg.data / "footage", cfg.footage_tiers, blocked)
    if not videos:
        raise CannotBuild(f"No footage of {person.name} yet.")
    sec = lib.section(idea.song, song, cfg.video["seconds"])
    return BuildPlan(idea.id, person.name, idea.song, idea.artist, str(song), sec.start, sec.end, videos,
                     person.tier, person.kind, person.credit,
                     titles={v: footage_mod.title_of(v) for v in videos})


def can_build(cfg: Config, idea: Idea) -> bool:
    try:
        plan_for(cfg, idea)
        return True
    except CannotBuild:
        return False


def build(cfg: Config, plan: BuildPlan, out_dir: Path, outro: str = "", progress: ProgressFn = lambda f, m: None
          ) -> dict:
    """Pick the clips, check the coverage and render `out_dir/base.mp4` (no text). Returns file paths + report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = AutoOptions(seed=plan.seed, work_dir=str(cfg.work),
                       max_fragment=cfg.tier2_max_fragment if plan.tier >= 2 else None)
    found = find_clips(plan.videos, lambda f, m: progress(f * 0.6, m), opts=opts)
    render = apply_preset(RenderSettings(), "shorts")
    render.layout = cfg.video.get("layout", "dynamic")
    if cfg.video.get("size"):                   # e.g. a small size for quick tests
        render.width, render.height = cfg.video["size"]
    render.max_duration = plan.end - plan.start + 0.5
    render.outro = outro
    project, song, report = auto_project([], plan.song_path, opts, SongOptions(start_s=plan.start, end_s=plan.end),
                                         render, found=found)
    if report.coverage < cfg.min_coverage:
        raise CannotBuild(f"The clips only cover {report.coverage:.0%} of the song (need {cfg.min_coverage:.0%}).")
    base = out_dir / "base.mp4"
    project.render.output_path = str(base)
    project.save(out_dir / "project.vsproj")
    (out_dir / "report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    (out_dir / "plan.json").write_text(json.dumps(asdict(plan), indent=2), encoding="utf-8")
    render_video(project, song, str(base), lambda f, m: progress(0.6 + f * 0.4, m))
    return {"base": str(base), "project": str(out_dir / "project.vsproj"), "report": report.to_dict()}
