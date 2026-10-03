"""The review page: watch the video without text, edit the text, check the final video, approve.

    /run/<id>?token=…            the page (phone-friendly)
    /api/run/<id>?token=…        the run's state as JSON (polled by the page)
    /api/run/<id>/action         POST {"action": …, "overlays": […], "note": "…"}
    /api/run/<id>/frame?t=…      a still with the current text drawn on it (instant text preview)
    /media/<id>/<base|final>.mp4 the videos (with seeking)

Every run has its own random token; nothing is served without it. Slow actions run in a background thread and
the page polls until they finish.
"""
from __future__ import annotations

import secrets
import threading
from pathlib import Path

import cv2
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from vsampler.models import Project, TextOverlay
from vsampler.render.overlays import OverlayPainter

from ..context import grab_frame
from ..pipeline import InvalidAction, Pipeline, Run

PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")
VIDEOS = {"base.mp4", "final.mp4"}


def create_app(pipe: Pipeline, background: bool = True) -> FastAPI:
    """The review app. background=False runs actions inline (for tests)."""
    app = FastAPI(title="Video review", docs_url=None, redoc_url=None)
    locks: dict[str, threading.Lock] = {}
    for stale in pipe.runs():       # a job that was running when the last server stopped won't finish now
        if stale.busy:
            stale.busy, stale.error = "", "The last step was interrupted (the server restarted). Try it again."
            pipe.save(stale)

    def run_for(run_id: str, token: str) -> Run:
        try:
            run = pipe.load(run_id)
        except (FileNotFoundError, OSError):
            raise HTTPException(404, "No such run") from None
        if not token or not secrets.compare_digest(token, run.token):
            raise HTTPException(403, "Wrong or missing token")
        return pipe.ensure_parts(pipe.expire_if_due(run))

    def view(run: Run) -> dict:
        d = pipe.dir(run)
        return {
            "id": run.id, "state": run.state, "busy": run.busy, "progress": run.progress, "error": run.error,
            "idea": run.idea, "plan": {k: run.plan.get(k) for k in ("who", "song", "artist", "start", "end", "tier")},
            "themes": [t["name"] for t in run.themes], "coverage": run.report.get("coverage"),
            "parts": [{**pt, "label": part_label(i, pt)} for i, pt in enumerate(run.parts)],
            "song_length": run.song_length, "section": [run.plan.get("start"), run.plan.get("end")],
            "overlays": run.overlays, "draft": run.draft, "judge": run.judge, "actions": run.actions,
            "videos": {name.split(".")[0]: f"/media/{run.id}/{name}?token={run.token}&v={run.version}"
                       for name in VIDEOS if (d / name).exists()},
            "log": run.log[-12:],
        }

    @app.get("/run/{run_id}", response_class=HTMLResponse)
    def page(run_id: str, token: str = Query("")):
        run_for(run_id, token)
        return HTMLResponse(PAGE.replace("__RUN__", run_id).replace("__TOKEN__", token))

    @app.get("/api/run/{run_id}")
    def state(run_id: str, token: str = Query("")):
        return JSONResponse(view(run_for(run_id, token)))

    @app.post("/api/run/{run_id}/action")
    def action(run_id: str, token: str = Query(""), body: dict = Body(...)):
        run = run_for(run_id, token)
        name = body.get("action", "")
        kw = {k: body[k] for k in ("overlays", "note", "start", "end", "remember") if k in body}
        if name == "change_section":
            try:
                problem = pipe.check_section(run, float(kw.get("start")), float(kw.get("end")))
            except (TypeError, ValueError):
                problem = "Give the start and end of the part in seconds."
            if problem:
                raise HTTPException(400, problem)
        lock = locks.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Still working on the last request")
        try:
            if name not in run.actions:
                raise InvalidAction(f"Can't {name.replace('_', ' ')} right now.")
            quick = name in ("save_text", "back_to_text", "approve", "skip")
        except InvalidAction as e:
            lock.release()
            raise HTTPException(409, str(e)) from None

        def work() -> None:
            try:
                pipe.act(pipe.load(run_id), name, **kw)
            except Exception:  # noqa: BLE001 - saved on the run as its error
                pass
            finally:
                lock.release()

        if background and not quick:
            run.busy = "Starting…"
            pipe.save(run)
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return JSONResponse(view(pipe.load(run_id)))

    @app.get("/api/run/{run_id}/frame")
    def frame(run_id: str, t: float = Query(0.0), token: str = Query(""), text: bool = Query(True)):
        run = run_for(run_id, token)
        base = pipe.dir(run) / "base.mp4"
        img = grab_frame(str(base), t, width=1080) if base.exists() else None
        if img is None:
            raise HTTPException(404, "No video yet")
        if text and run.overlays:
            rs = Project.load(pipe.dir(run) / "project.vsproj").render
            rs.width, rs.height = img.shape[1], img.shape[0]
            rs.overlays = [TextOverlay.from_dict(o) for o in run.overlays]
            from ..context import video_duration
            img = OverlayPainter(rs, video_duration(str(base))).apply(img, t)
        ok, jpg = cv2.imencode(".jpg", cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2)),
                               [cv2.IMWRITE_JPEG_QUALITY, 82])
        return Response(jpg.tobytes(), media_type="image/jpeg")

    @app.get("/media/{run_id}/{name}")
    def media(run_id: str, name: str, token: str = Query("")):
        run = run_for(run_id, token)
        f = pipe.dir(run) / name
        if name not in VIDEOS or not f.exists():
            raise HTTPException(404, "No such video")
        return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    return app


def part_label(i: int, part: dict) -> str:
    """How a suggested song part is described on the page."""
    if i == 0 and part.get("repeats", 0) >= 2:
        return "The chorus (the most repeated part)"
    if part.get("start", 0) < 1:
        return "The start of the song"
    n = part.get("repeats", 0)
    return f"A part that comes back {n} times" if n >= 2 else "A part that plays once"


def link(pipe: Pipeline, run: Run) -> str:
    r = pipe.cfg.review
    base = r.get("public_url") or f"http://{r['host']}:{r['port']}"
    return f"{base.rstrip('/')}/run/{run.id}?token={run.token}"


def serve(pipe: Pipeline) -> None:
    import uvicorn

    r = pipe.cfg.review
    uvicorn.run(create_app(pipe), host=r["host"], port=int(r["port"]), log_level="warning")
