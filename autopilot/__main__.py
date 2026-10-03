"""python -m autopilot …

    run [--idea "Donald Trump — Fireflies"] [--date 2026-12-20] [--no-serve]
        Make today's video. In approval mode it builds the video without text, sends you the review link and
        serves the review page until you've approved or skipped it (or the deadline passes). In auto mode it
        goes all the way to "approved" on its own.
    review          serve the review page for every waiting run
    ideas           list the ideas and whether each can be made right now
    status          the latest runs
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import date

from . import config as config_mod
from . import notify
from .pipeline import FINISHED, Pipeline


def _serve_until_done(pipe: Pipeline, run_id: str) -> None:
    from .review.app import serve

    threading.Thread(target=serve, args=(pipe,), daemon=True).start()
    while True:
        time.sleep(5)
        run = pipe.expire_if_due(pipe.load(run_id))
        if run.state in FINISHED and not run.busy:
            print(f"Run {run.id}: {run.state}")
            return


def cmd_run(a: argparse.Namespace, pipe: Pipeline) -> int:
    from .review.app import link

    day = date.fromisoformat(a.date) if a.date else date.today()
    run = pipe.new_run(day, a.idea)
    if run.state == "failed":
        print(run.error)
        return 1
    print(f"Run {run.id}: {run.idea.get('who')} — {run.idea.get('song')}")
    if pipe.cfg.publish_mode == "auto":
        run = pipe.run_auto(run)
        print(f"Run {run.id}: {run.state}" + (f" ({run.error})" if run.error else ""))
        return 0 if run.state == "approved" else 1
    run = pipe.build(run)
    if run.state == "failed":
        print(run.error)
        return 1
    url = link(pipe, run)
    notify.send(f"Today's video is ready to review: {run.plan.get('who')} sings {run.plan.get('song')}\n{url}")
    if not a.no_serve:
        _serve_until_done(pipe, run.id)
    return 0


def cmd_review(a: argparse.Namespace, pipe: Pipeline) -> int:
    from .review.app import link, serve

    for run in pipe.runs():
        if run.state not in FINISHED:
            print(f"{run.id} ({run.state}): {link(pipe, run)}")
    serve(pipe)
    return 0


def cmd_ideas(a: argparse.Namespace, pipe: Pipeline) -> int:
    from .build import CannotBuild, plan_for

    for idea in pipe.all_ideas():
        try:
            plan_for(pipe.cfg, idea)
            status = "ready"
        except CannotBuild as e:
            status = str(e)
        star = "★" if idea.favourite else " "
        print(f"{star} {idea.label:55s} {status}")
    return 0


def cmd_status(a: argparse.Namespace, pipe: Pipeline) -> int:
    for run in pipe.runs()[:15]:
        print(f"{run.id:14s} {run.state:13s} {run.idea.get('who', '')} — {run.idea.get('song', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autopilot", description="Daily '<person> sings <song>' Shorts.")
    p.add_argument("--config", help="settings file (default autopilot/config.yaml)")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="make today's video")
    r.add_argument("--idea", help="use this idea, e.g. 'Donald Trump — Fireflies'")
    r.add_argument("--date", help="pretend it's this date (YYYY-MM-DD), e.g. to test themes")
    r.add_argument("--no-serve", action="store_true", help="don't start the review page")
    r.set_defaults(func=cmd_run)
    sub.add_parser("review", help="serve the review page").set_defaults(func=cmd_review)
    sub.add_parser("ideas", help="list the ideas").set_defaults(func=cmd_ideas)
    sub.add_parser("status", help="the latest runs").set_defaults(func=cmd_status)
    a = p.parse_args(argv)
    pipe = Pipeline(config_mod.load(a.config))
    return a.func(a, pipe)


if __name__ == "__main__":
    sys.exit(main())
