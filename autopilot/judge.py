"""The safety check on a draft: fixed rules always, plus a second Claude opinion when a key is set."""
from __future__ import annotations

import re

from . import writer

RULES = """You check the on-screen text of a parody video before it's posted. Fail it if any line:
- invents a quote or a factual claim about a real person (commenting on what's visible is fine);
- contains election misinformation (dates, how to vote, results);
- contains slurs, sexual content about a real person, or mocks a tragedy, a group's identity or anyone's looks;
- brags about how the video was edited;
- doesn't match what the frames show at that time.
Gentle mockery of over-the-top patriotism and powerful people is fine. Be brief: give one reason per problem."""

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "reasons": {"type": "array", "items": {"type": "string"}}},
    "required": ["ok", "reasons"], "additionalProperties": False,
}
CLAIM_WORDS = re.compile(r"\b(said|says|admits?|admitted|confirm(s|ed)?|announced?|leaked|caught)\b", re.I)


def rule_check(d: writer.Draft, ctx: dict) -> list[str]:
    out = writer.check(d, ctx)
    for line in [d.hook] + [c["text"] for c in d.captions]:
        if CLAIM_WORDS.search(line):
            out.append(f"“{line}” may read as a factual claim about a real person.")
    return out


def judge(d: writer.Draft, ctx: dict, cfg, client=None) -> tuple[bool, list[str]]:
    """(ok, reasons). The fixed rules always run; Claude also reviews when available."""
    reasons = rule_check(d, ctx)
    if writer.has_claude(client):
        text = writer._context_text({"person": ctx.get("person"), "song": ctx.get("song"), "draft": d.to_dict()})
        content = [{"type": "text", "text": "Text to check:\n" + text}] + writer._frame_blocks(ctx)
        try:
            data = writer.ask_claude(cfg, RULES, content, SCHEMA, client)
            if not data.get("ok", False):
                reasons += [f"Judge: {r}" for r in data.get("reasons", [])] or ["Judge: failed without a reason."]
        except writer.WriterError as e:
            reasons.append(f"Judge unavailable: {e}")
    return not reasons, reasons
