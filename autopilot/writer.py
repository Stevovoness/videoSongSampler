"""The on-screen text: Claude writes it from the video's context (see style.md); a template stands in without a key."""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

STYLE = (Path(__file__).parent / "style.md").read_text(encoding="utf-8")
MAX_HOOK_WORDS = 10
MAX_CAPTION_WORDS = 8
BRAGGING = ("not one note", "not a single note", "every note is a real", "real words", "no ai", "100% real")

SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string", "description": "Big text at the top for the first seconds"},
        "captions": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"}},
            "required": ["text", "start", "end"], "additionalProperties": False}},
        "cta": {"type": "string", "description": "The call to action on the end card"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "captions", "cta", "title", "description", "hashtags"],
    "additionalProperties": False,
}

INSTRUCTIONS = """Write the on-screen text and the post's title, description and hashtags for this video.
The user message has the video's context as JSON (what's sung when, the words each note was cut from, what was
said around it, the source videos, today's themes) and frames from the finished video with their times.
Caption times are seconds into the video: each caption must sit inside one sung phrase, before `song_end`.
The description must say it's a parody, made from public footage. Hashtags without the # sign."""


class WriterError(Exception):
    pass


@dataclass
class Draft:
    hook: str
    captions: list[dict]
    cta: str
    title: str
    description: str
    hashtags: list[str]
    source: str = "template"                 # claude | template | edited
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def has_claude(client=None) -> bool:
    return client is not None or bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def check(d: Draft, ctx: dict) -> list[str]:
    """What's wrong with a draft (empty if nothing)."""
    out = []
    end = ctx.get("song_end") or ctx.get("duration") or 60
    if len(d.hook.split()) > MAX_HOOK_WORDS:
        out.append(f"The hook has {len(d.hook.split())} words; keep it to {MAX_HOOK_WORDS}.")
    last = -1.0
    for c in sorted(d.captions, key=lambda c: c["start"]):
        if len(c["text"].split()) > MAX_CAPTION_WORDS:
            out.append(f"Caption “{c['text']}” is too long ({MAX_CAPTION_WORDS} words at most).")
        if not 0 <= c["start"] < c["end"] <= end + 0.01:
            out.append(f"Caption “{c['text']}” must be between 0 and {end:.1f} s with start before end.")
        if c["start"] < last:
            out.append(f"Caption “{c['text']}” overlaps the one before it.")
        last = c["end"]
    text = " ".join([d.hook, d.cta] + [c["text"] for c in d.captions]).lower()
    out += [f"Don't brag about the edit (“{b}”)." for b in BRAGGING if b in text]
    return out


def _frame_blocks(ctx: dict) -> list[dict]:
    blocks = []
    for f in ctx.get("frames", []):
        data = base64.standard_b64encode(Path(f["file"]).read_bytes()).decode("ascii")
        blocks.append({"type": "text", "text": f"Frame at {f['t']} s:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    return blocks


def _context_text(ctx: dict) -> str:
    slim = {k: v for k, v in ctx.items() if k != "frames"}
    return json.dumps(slim, ensure_ascii=False, indent=1)


def ask_claude(cfg, system: str, content: list[dict], schema: dict, client=None, retry_note=None) -> dict:
    """One structured-output request; returns the parsed JSON. Retries once with `retry_note(data)` feedback."""
    import anthropic

    client = client or anthropic.Anthropic()
    messages: list = [{"role": "user", "content": content}]
    data: dict = {}
    for attempt in range(2):
        try:
            resp = client.beta.messages.create(
                model=cfg.claude["model"], max_tokens=16000, system=system, messages=messages,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": cfg.claude.get("effort", "medium"),
                               "format": {"type": "json_schema", "schema": schema}})
        except anthropic.APIStatusError as e:
            raise WriterError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise WriterError("Couldn't reach the Claude API.") from e
        if resp.stop_reason == "refusal":
            raise WriterError("Claude declined to write this one.")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        problems = retry_note(data) if retry_note else []
        if not problems or attempt:
            return data
        messages += [{"role": "assistant", "content": resp.content},
                     {"role": "user", "content": "Please fix these and answer again:\n- " + "\n- ".join(problems)}]
    return data


def write(ctx: dict, cfg, note: str = "", client=None) -> Draft:
    """A draft of the text for the video described by `ctx` (Claude if available, else the template)."""
    if not has_claude(client):
        return template(ctx, cfg)
    content = [{"type": "text", "text": "Video context:\n" + _context_text(ctx)}] + _frame_blocks(ctx)
    if note:
        content.append({"type": "text", "text": f"Note from the reviewer for this version: {note}"})
    system = STYLE + "\n\n" + INSTRUCTIONS

    def problems(data: dict) -> list[str]:
        return check(Draft(**data), ctx)

    data = ask_claude(cfg, system, content, SCHEMA, client, problems)
    d = Draft(**data, source="claude")
    d.problems = check(d, ctx)
    return d


def _short(text: str, n: int) -> str:
    words = re.sub(r"\s+", " ", text).strip().split()
    return " ".join(words[:n])


def template(ctx: dict, cfg) -> Draft:
    """Plain text from the context, used when there's no Claude API key. Meant to be edited in the review."""
    who, song = ctx["person"], ctx["song"]["title"]
    phrases = ctx.get("phrases", [])
    themes = ctx.get("themes", [])
    caps = []
    if len(phrases) > 2:
        p = phrases[1]
        caps.append({"text": "nobody asked for this cover", "start": p["start"], "end": p["end"] + 1.5})
    hi = next((p for p in phrases if p.get("has_highest_note")), None)
    if hi is not None and (not caps or hi["start"] >= caps[-1]["end"]):
        caps.append({"text": "the high note though 😭", "start": hi["start"], "end": min(hi["end"] + 2.0,
                                                                                         ctx["song_end"])})
    political = any(t.get("kind") == "political" for t in themes) or ctx.get("person_kind") == "politician"
    late = [p for p in phrases if caps and p["start"] >= caps[-1]["end"] + 0.5]
    if late and political:
        p = late[len(late) // 2]
        caps.append({"text": "freedom got so loud it went in tune", "start": p["start"],
                     "end": min(p["end"] + 2.0, ctx["song_end"])})
    soft = ctx.get("person_kind") == "celebrity"
    cta = (themes[0].get("cta") if themes and themes[0].get("cta") else None) or \
        (cfg.cta["soft"] if soft else cfg.cta["default"])
    artist = f" by {ctx['song']['artist']}" if ctx["song"].get("artist") else ""
    return Draft(
        hook=_short(f"POV: {who} drops a {song} cover 🎤", MAX_HOOK_WORDS),
        captions=caps, cta=cta,
        title=f"{who} sings {song} (parody)",
        description=f"Parody: {who} “sings” {song}{artist}, made from public speeches. Not real, obviously.",
        hashtags=["shorts", "parody", re.sub(r"\W+", "", who.lower()), re.sub(r"\W+", "", song.lower())],
        source="template")


def to_overlays(d: Draft, ctx: dict, handle: str = "") -> list[dict]:
    """The draft as the engine's text overlays (TextOverlay fields)."""
    end = ctx.get("song_end") or None
    out = [{"text": d.hook, "style": "hook", "start": 0.0, "end": end}]
    for c in sorted(d.captions, key=lambda c: c["start"]):
        out.append({"text": c["text"], "style": "caption", "start": round(float(c["start"]), 2),
                    "end": round(float(c["end"]), 2)})
    if d.cta:
        out.append({"text": d.cta, "style": "cta", "start": -4.0, "end": None})
    if handle:
        out.append({"text": handle, "style": "watermark", "start": 0.0, "end": None})
    return out


def from_overlays(overlays: list[dict], d: Draft) -> Draft:
    """A draft updated from overlays edited in the review page (titles etc. kept)."""
    hook = next((o["text"] for o in overlays if o.get("style") == "hook"), "")
    cta = next((o["text"] for o in overlays if o.get("style") == "cta"), "")
    caps = [{"text": o["text"], "start": float(o["start"]), "end": float(o["end"] if o["end"] is not None else 0)}
            for o in overlays if o.get("style", "caption") == "caption"]
    return Draft(hook, caps, cta, d.title, d.description, d.hashtags, "edited")
