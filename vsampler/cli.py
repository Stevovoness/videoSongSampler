"""Command line: use the app without its window, e.g. on a server.

    python main.py analyse speech.mp4
    python main.py auto --videos speech.mp4 --song tune.mid --preset shorts --start 30 --end 60 --out short.mp4
    python main.py text --video plain.mp4 --overlays text.json --out final.mp4   (add text, no re-render)
    python main.py render my_song.vsproj out.mp4        (or the older form: --render my_song.vsproj out.mp4)

Progress goes to stderr (and to <out>.log); the result is printed to stdout as JSON.
Exit codes: 0 done, 1 failed, 2 bad arguments, 3 the clips cover too little of the song (nothing rendered).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Callable, TextIO

EXIT_OK, EXIT_FAILED, EXIT_LOW_COVERAGE = 0, 1, 3


class _Log:
    """Progress messages to stderr and, optionally, a log file next to the output."""

    def __init__(self, path: str | None = None):
        self.file: TextIO | None = open(path, "w", encoding="utf-8") if path else None
        self._last = -1

    def say(self, msg: str) -> None:
        if sys.stderr is not None:
            print(msg, file=sys.stderr, flush=True)
        if self.file:
            self.file.write(msg + "\n")
            self.file.flush()

    def progress(self) -> Callable[[float, str], None]:
        def prog(f: float, m: str) -> None:
            if int(f * 10) != self._last:
                self._last = int(f * 10)
                self.say(f"{int(f * 100):3d}%  {m}")
        return prog

    def close(self) -> None:
        if self.file:
            self.file.close()


def _emit(result: dict) -> None:
    if sys.stdout is not None:
        print(json.dumps(result, indent=2), flush=True)


def cmd_analyse(a: argparse.Namespace) -> int:
    from . import clipfinder
    from .clips import SR, load_audio
    from .notes import midi_to_name

    log = _Log()
    res = clipfinder.analyse(load_audio(a.video), SR, log.progress())
    best = clipfinder.best_takes(res.candidates)
    _emit({
        "video": a.video,
        "duration": round(res.duration, 2),
        "candidates": [
            {"kind": c.kind, "start": round(c.start, 3), "end": round(c.end, 3),
             "note": midi_to_name(c.note) if c.note is not None else None,
             "midi": None if c.midi is None else round(c.midi, 2), "confidence": c.confidence, "best": i in best}
            for i, c in enumerate(res.candidates)],
    })
    return EXIT_OK


def _load_overlays(path: str | None) -> list:
    from .models import TextOverlay

    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("overlays", [])
    return [TextOverlay.from_dict(o) for o in data]


def cmd_auto(a: argparse.Namespace) -> int:
    from .auto import AutoOptions, auto_project
    from .models import RenderSettings, SongOptions, apply_preset
    from .render.renderer import render_video

    out = Path(a.out)
    log = _Log(str(out) + ".log")
    try:
        render = RenderSettings()
        if a.preset:
            apply_preset(render, a.preset)
        if a.layout:
            render.layout = a.layout
        if a.max_duration is not None:
            render.max_duration = a.max_duration
        render.overlays = _load_overlays(a.overlays)
        render.outro = a.outro or ""
        song_opts = SongOptions(start_s=a.start or 0.0, end_s=a.end)
        opts = AutoOptions(max_fragment=a.max_fragment, transpose=a.transpose, drums=not a.no_drums,
                           voice=not a.full_range, workers=a.workers, work_dir=a.work_dir or "",
                           melody_only=a.melody_only)
        project, song, report = auto_project(a.videos, a.song, opts, song_opts, render, log.progress())
        project.render.output_path = str(out)
        project.save(out.with_suffix(".vsproj"))
        result = {"project": str(out.with_suffix(".vsproj")), "video": None, "report": report.to_dict()}
        out.with_suffix(".report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        log.say(f"Coverage {report.coverage:.0%} ({report.exact} exact, {report.octave} octave, "
                f"{report.borrowed} pitch-shifted, {report.missing} missing), transpose {report.transpose:+d}")
        if report.coverage < a.min_coverage:
            log.say(f"Coverage is below {a.min_coverage:.0%}: not rendering.")
            _emit(result)
            return EXIT_LOW_COVERAGE
        if not a.no_render:
            render_video(project, song, str(out), log.progress())
            result["video"] = str(out)
            log.say(f"OK {out}")
        _emit(result)
        return EXIT_OK
    except Exception:  # noqa: BLE001
        log.say("FAILED\n" + traceback.format_exc())
        return EXIT_FAILED
    finally:
        log.close()


def cmd_render(a: argparse.Namespace) -> int:
    from .importers import load_song
    from .models import Project
    from .render.renderer import render_video

    log = _Log(a.out + ".log")
    try:
        project = Project.load(a.project)
        log.say(f"Loading song {project.song_path}")
        song = load_song(project.song_path, project.song, project.audiveris_path or None)
        log.say(f"{len(song.events)} notes, {len(song.tracks)} tracks")
        render_video(project, song, a.out, log.progress())
        log.say(f"OK {a.out}")
        _emit({"video": a.out})
        return EXIT_OK
    except Exception:  # noqa: BLE001
        log.say("FAILED\n" + traceback.format_exc())
        return EXIT_FAILED
    finally:
        log.close()


def cmd_text(a: argparse.Namespace) -> int:
    from .render.burn import burn_overlays

    log = _Log(a.out + ".log")
    try:
        burn_overlays(a.video, _load_overlays(a.overlays), a.out, progress=log.progress())
        log.say(f"OK {a.out}")
        _emit({"video": a.out})
        return EXIT_OK
    except Exception:  # noqa: BLE001
        log.say("FAILED\n" + traceback.format_exc())
        return EXIT_FAILED
    finally:
        log.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vsampler", description="Video Sampler without its window.")
    sub = p.add_subparsers(dest="command", required=True)

    an = sub.add_parser("analyse", aliases=["analyze"], help="list every note and hit the clip finder hears")
    an.add_argument("video")
    an.set_defaults(func=cmd_analyse)

    au = sub.add_parser("auto", help="build (and render) a song from long videos, with no one at the controls")
    au.add_argument("--videos", nargs="+", required=True, help="videos to cut clips from")
    au.add_argument("--song", required=True, help="MIDI, MusicXML, audio or sheet-music file")
    au.add_argument("--out", required=True, help="the .mp4 to write (the project and report go next to it)")
    au.add_argument("--preset", choices=["shorts", "landscape"], help="ready-made size and length")
    au.add_argument("--layout", choices=["grid", "dynamic"])
    au.add_argument("--start", type=float, help="play the song from this many seconds in")
    au.add_argument("--end", type=float, help="…until this many seconds in")
    au.add_argument("--max-duration", type=float, help="cut the song part of the video to this many seconds")
    au.add_argument("--transpose", type=int, help="key change in semitones (default: chosen automatically)")
    au.add_argument("--overlays", help="JSON file with the text to show (a list of TextOverlay fields)")
    au.add_argument("--outro", help="video to play after the song")
    au.add_argument("--max-fragment", type=float, help="cut every clip to at most this many seconds")
    au.add_argument("--min-coverage", type=float, default=0.0,
                    help="don't render if the clips play less than this share of the song (0..1)")
    au.add_argument("--no-drums", action="store_true", help="don't put percussive sounds on drum pads")
    au.add_argument("--melody-only", action="store_true",
                    help="play just the tune (by default every part plays, chords too)")
    au.add_argument("--full-range", action="store_true",
                    help="listen for singing / instruments too (slower); the default suits speech")
    au.add_argument("--workers", type=int, default=0, help="processes for pitch tracking (default: CPU cores - 1)")
    au.add_argument("--work-dir", help="where long videos' pieces and saved analyses go (default: the app cache)")
    au.add_argument("--no-render", action="store_true", help="only build the project and the report")
    au.set_defaults(func=cmd_auto)

    tx = sub.add_parser("text", help="put text on a finished video without rendering it again")
    tx.add_argument("--video", required=True, help="the video without text")
    tx.add_argument("--overlays", required=True, help="JSON file with the text to show")
    tx.add_argument("--out", required=True)
    tx.set_defaults(func=cmd_text)

    r = sub.add_parser("render", help="render a saved project")
    r.add_argument("project")
    r.add_argument("out")
    r.set_defaults(func=cmd_render)
    return p


COMMANDS = {"analyse", "analyze", "auto", "text", "render", "--render", "-h", "--help"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--render":   # the older form
        argv[0] = "render"
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    return args.func(args)
