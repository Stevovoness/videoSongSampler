"""What happens in this exact video, for the writer: phrases, the words each note came from, frames, sources.

Transcripts come from faster-whisper, but only for the 30-second blocks of footage the clips were cut from, and
each block is saved, so even hours of footage cost a few minutes the first time and nothing after that.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import av
import cv2
import numpy as np

from vsampler.importers import load_song
from vsampler.models import Project
from vsampler.notes import midi_to_name
from vsampler.planner import plan_for_project
from vsampler.render.renderer import project_take
from vsampler.songops import apply_options

BLOCK = 30.0             # seconds of footage transcribed at a time
MAX_BLOCKS = 24
MAX_FRAMES = 8
PHRASE_GAP = 0.35        # a pause this long starts a new phrase
PHRASE_MAX = 3.0


def video_duration(path: str) -> float:
    with av.open(path) as c:
        return c.duration / 1e6 if c.duration else 0.0


def grab_frame(path: str, t: float, width: int = 360) -> np.ndarray | None:
    """The frame at t seconds (BGR), scaled to `width`."""
    with av.open(path) as c:
        v = c.streams.video[0]
        c.seek(max(0, int((t - 0.5) / v.time_base)), stream=v)
        img = None
        for fr in c.decode(v):
            img = fr.to_ndarray(format="bgr24")
            if fr.time is not None and fr.time >= t:
                break
    if img is None:
        return None
    h, w = img.shape[:2]
    return cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)


class Transcriber:
    """Word-level transcripts of 30-second blocks of footage, saved in `cache_dir`."""

    def __init__(self, cache_dir: Path, model: str = "base"):
        self.cache_dir, self.model_name, self._model = cache_dir, model, None
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _model_(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model

    def words(self, path: str, block: int) -> list[dict]:
        key = hashlib.sha1(f"{Path(path).resolve()}|{block}|{self.model_name}".encode()).hexdigest()[:20]
        f = self.cache_dir / f"{key}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        from scipy.signal import resample_poly
        from vsampler.render.sources import get_audio
        audio = get_audio(path)
        sr = 44100
        a, b = int(max(0.0, block * BLOCK - 1) * sr), int((block + 1) * BLOCK * sr + sr)
        mono = audio[:, a:b].mean(axis=0)
        y = resample_poly(mono, 160, 441).astype(np.float32)        # 44.1 kHz -> 16 kHz
        segs, _info = self._model_().transcribe(y, word_timestamps=True, vad_filter=True)
        t0 = a / sr
        out = [{"start": round(t0 + w.start, 2), "end": round(t0 + w.end, 2), "word": w.word.strip()}
               for s in segs for w in (s.words or [])]
        f.write_text(json.dumps(out), encoding="utf-8")
        return out


def _source_title(clip_path: str, titles: dict[str, str]) -> str:
    """Title of the original video a clip (maybe from a 10-minute piece of it) came from."""
    if clip_path in titles:
        return titles[clip_path]
    folder = Path(clip_path).parent.name
    for original, title in titles.items():
        if folder.startswith(Path(original).stem[:40]):
            return title
    return Path(clip_path).stem


def build_context(run_dir: Path, plan: dict, idea: dict, themes: list[dict], transcriber: Transcriber | None,
                  progress=lambda f, m: None) -> dict:
    """The context the writer gets (also saved as run_dir/context.json), plus frames in run_dir/frames/."""
    project = Project.load(run_dir / "project.vsproj")
    song = load_song(project.song_path, project.song)
    events = apply_options(song, project.song, include_drums=bool(project.drum_slots))
    notes = sorted(plan_for_project(events, project).note_instances, key=lambda i: i.start)
    base = str(run_dir / "base.mp4")
    duration = video_duration(base)
    song_end = min(duration, (project.render.max_duration or duration))

    # what each note is: the take behind it and where it came from
    sung = []
    for inst in notes:
        clip = project_take(project, inst.slot, inst.take)
        end = clip.trim_end if clip.trim_end is not None else clip.trim_start + 0.3
        sung.append({"t": round(inst.start, 2), "note": midi_to_name(inst.target), "pitch": inst.target,
                     "path": clip.path, "from": round(clip.trim_start, 2), "to": round(end, 2)})

    # transcripts for the blocks of footage the clips come from (the most used blocks first)
    words_at: dict[tuple[str, int], list[dict]] = {}
    if transcriber is not None and sung:
        blocks: dict[tuple[str, int], int] = {}
        for s in sung:
            k = (s["path"], int(s["from"] // BLOCK))
            blocks[k] = blocks.get(k, 0) + 1
        chosen = sorted(blocks, key=lambda k: -blocks[k])[:MAX_BLOCKS]
        for n, k in enumerate(chosen):
            progress(n / len(chosen), f"Listening to what was said… {n + 1} of {len(chosen)}")
            try:
                words_at[k] = transcriber.words(*k)
            except Exception as e:  # noqa: BLE001 - context is best effort
                print(f"transcript failed for {k}: {e}")

    def said(s: dict, around: float = 0.0) -> str:
        ws = words_at.get((s["path"], int(s["from"] // BLOCK)), [])
        return " ".join(w["word"] for w in ws if w["end"] > s["from"] - around and w["start"] < s["to"] + around)

    # phrases: runs of notes without a pause
    phrases: list[list[dict]] = []
    for s, inst in zip(sung, notes):
        if phrases and s["t"] - (phrases[-1][-1]["t"] + phrases[-1][-1].get("dur", 0)) < PHRASE_GAP \
                and s["t"] - phrases[-1][0]["t"] < PHRASE_MAX:
            phrases[-1].append({**s, "dur": inst.duration})
        else:
            phrases.append([{**s, "dur": inst.duration}])
    top = max((s["pitch"] for s in sung), default=0)
    out_phrases = []
    for ph in phrases:
        words = []
        for s in ph:
            w = said(s)
            if w and (not words or words[-1] != w):
                words.append(w)
        out_phrases.append({
            "start": ph[0]["t"], "end": round(ph[-1]["t"] + ph[-1]["dur"], 2),
            "notes": " ".join(s["note"] for s in ph),
            "has_highest_note": any(s["pitch"] == top for s in ph),
            "sung_words": " / ".join(words),
            "said_around": said(ph[0], around=6.0)[:300],
            "sources": sorted({_source_title(s["path"], plan.get("titles", {})) for s in ph}),
        })

    # frames of the finished picture, spread over the song
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    frames = []
    if out_phrases:
        pick = np.linspace(0, len(out_phrases) - 1, min(MAX_FRAMES, len(out_phrases))).round().astype(int)
        for n, i in enumerate(dict.fromkeys(pick.tolist())):
            ph = out_phrases[i]
            t = round((ph["start"] + ph["end"]) / 2, 2)
            img = grab_frame(base, t)
            if img is not None:
                f = frames_dir / f"frame_{n:02d}.jpg"
                cv2.imwrite(str(f), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frames.append({"t": t, "file": str(f)})

    ctx = {
        "idea": idea,
        "person": plan["who"],
        "person_kind": plan.get("kind", ""),
        "song": {"title": plan["song"], "artist": plan.get("artist", ""), "section": [plan["start"], plan["end"]]},
        "themes": themes,
        "duration": round(duration, 2),
        "song_end": round(song_end, 2),
        "has_outro": bool(project.render.outro),
        "sources": sorted(set(plan.get("titles", {}).values())),
        "highest_note_at": next((s["t"] for s in sung if s["pitch"] == top), None),
        "phrases": out_phrases,
        "frames": frames,
    }
    (run_dir / "context.json").write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")
    return ctx
