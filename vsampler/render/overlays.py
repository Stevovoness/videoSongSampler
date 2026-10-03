"""Text over the video: a hook line, captions, a call to action and a watermark (drawn with Pillow).

Text stays inside the *safe area*: on a vertical video, Shorts / Reels / TikTok put their buttons down the
right-hand side and the title and caption along the bottom, so nothing is drawn there.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..models import RenderSettings, TextOverlay
from ..paths import asset

FONT = "fonts/Anton-Regular.ttf"
EMOJI_FONTS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "seguiemj.ttf"),
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]
HOOK_BIG_SECONDS = 3.0   # the hook is big for its first seconds, then shrinks and stays at the top
FADE = 0.15              # seconds each text takes to fade in

RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class Style:
    size: float               # font size as a fraction of the video width
    fill: RGBA
    stroke: RGBA | None       # outline colour
    box: RGBA | None          # background box behind each line (None: no box)
    align: str                # center | left
    position: str             # default place: top | middle | lower | bottom


WHITE, BLACK = (255, 255, 255, 255), (0, 0, 0, 255)
STYLES = {
    "hook": Style(0.085, BLACK, None, (255, 255, 255, 245), "center", "top"),
    "hook_small": Style(0.058, BLACK, None, (255, 255, 255, 235), "center", "top"),
    "caption": Style(0.068, WHITE, BLACK, None, "center", "lower"),
    "cta": Style(0.07, BLACK, None, None, "center", "middle"),        # box: the highlight colour
    "watermark": Style(0.032, (255, 255, 255, 170), (0, 0, 0, 150), None, "left", "bottom"),
}


def safe_area(w: int, h: int) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the part of the frame that no app button or caption covers."""
    if h > w:   # vertical: buttons down the right, title and caption along the bottom
        return int(w * 0.05), int(h * 0.09), int(w * 0.86), int(h * 0.80)
    m = int(min(w, h) * 0.05)
    return m, m, w - m, h - m


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF
            or cp in (0x200D, 0xFE0F, 0x20E3) or 0xE0020 <= cp <= 0xE007F)


def _runs(text: str) -> list[tuple[bool, str]]:
    """Split text into (is_emoji, run) pieces."""
    out: list[tuple[bool, str]] = []
    for ch in text:
        e = _is_emoji(ch)
        if out and out[-1][0] == e:
            out[-1] = (e, out[-1][1] + ch)
        else:
            out.append((e, ch))
    return out


@lru_cache(maxsize=16)
def _font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(asset(FONT)), px)


@lru_cache(maxsize=1)
def _emoji_font_path() -> str | None:
    return next((p for p in EMOJI_FONTS if os.path.exists(p)), None)


@lru_cache(maxsize=256)
def _emoji(run: str, px: int) -> Image.Image | None:
    """A colour emoji run scaled to px high, or None if there's no emoji font."""
    path = _emoji_font_path()
    if path is None:
        return None
    try:
        font = ImageFont.truetype(path, px)
    except OSError:          # bitmap emoji fonts (Noto Color Emoji) only come in one size
        font = ImageFont.truetype(path, 109)
    l, t, r, b = font.getbbox(run)
    if r <= l or b <= t:
        return None
    img = Image.new("RGBA", (r - l, b - t))
    ImageDraw.Draw(img).text((-l, -t), run, font=font, embedded_color=True)
    return img.resize((max(1, round(img.width * px / img.height)), px), Image.LANCZOS)


def _width(text: str, px: int) -> float:
    w = 0.0
    for emoji, run in _runs(text):
        if emoji:
            img = _emoji(run, px)
            w += img.width if img is not None else 0
        else:
            w += _font(px).getlength(run)
    return w


def _wrap(text: str, px: int, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        line = ""
        for word in para.split():
            trial = f"{line} {word}" if line else word
            if line and _width(trial, px) > max_w:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return [ln for ln in lines if ln.strip()]


def render_block(text: str, style: Style, max_w: int, px: int, box: RGBA | None = None) -> np.ndarray:
    """The text as an RGBA image no wider than max_w (lines wrapped, each with its box or outline)."""
    box = box or style.box
    stroke = max(2, px // 9) if style.stroke else 0
    pad = int(px * 0.3) if box else stroke
    while px > 8:   # shrink text whose longest word still doesn't fit
        lines = _wrap(text, px, max_w - 2 * pad)
        if all(_width(ln, px) <= max_w - 2 * pad for ln in lines):
            break
        px = int(px * 0.9)
        stroke = max(2, px // 9) if style.stroke else 0
        pad = int(px * 0.3) if box else stroke
    font = _font(px)
    line_h = int(px * 1.18)
    images = []
    for ln in lines:
        lw = int(np.ceil(_width(ln, px)))
        img = Image.new("RGBA", (lw + 2 * pad, line_h + (2 * pad if box else 2 * stroke)))
        d = ImageDraw.Draw(img)
        if box:
            d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=int(px * 0.28), fill=box)
        x, cy = pad, img.height / 2
        for emoji, run in _runs(ln):
            if emoji:
                e = _emoji(run, px)
                if e is not None:
                    img.alpha_composite(e, (int(x), int(cy - e.height / 2)))
                    x += e.width
            else:
                d.text((x, cy), run, font=font, fill=style.fill, anchor="lm",
                       stroke_width=stroke, stroke_fill=style.stroke)
                x += font.getlength(run)
        images.append(img)
    if not images:
        return np.zeros((1, 1, 4), np.uint8)
    gap = int(px * 0.12) if box else 0
    W = max(i.width for i in images)
    out = Image.new("RGBA", (W, sum(i.height for i in images) + gap * (len(images) - 1)))
    y = 0
    for i in images:
        out.alpha_composite(i, ((W - i.width) // 2 if style.align == "center" else 0, y))
        y += i.height + gap
    return np.asarray(out)


def _hex_rgba(color: str) -> RGBA:
    c = color.lstrip("#")
    if len(c) != 6:
        return (255, 204, 51, 255)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255


def blend(img: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    """Draw an RGBA image onto a BGR frame in place, clipped to the frame."""
    H, W = img.shape[:2]
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    part = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = part[..., 3:4].astype(np.float32) * (alpha / 255.0)
    roi = img[y0:y1, x0:x1]
    roi[:] = (roi * (1 - a) + part[..., 2::-1] * a).astype(np.uint8)


@dataclass
class _Ready:
    """A text block ready to blend quickly: BGR picture plus per-pixel weights (computed once per block)."""
    bgr: np.ndarray
    w: np.ndarray        # float32 0..1, the block's opacity
    inv: np.ndarray      # 1 - w

    @classmethod
    def of(cls, rgba: np.ndarray) -> "_Ready":
        w = np.ascontiguousarray(rgba[..., 3], dtype=np.float32) / 255.0
        return cls(np.ascontiguousarray(rgba[..., 2::-1]), w, 1.0 - w)


def _blend_ready(img: np.ndarray, r: _Ready, x: int, y: int, alpha: float = 1.0) -> None:
    """Like `blend`, using OpenCV's per-pixel blend (several times faster than numpy for every frame)."""
    H, W = img.shape[:2]
    h, w = r.bgr.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sl = (slice(y0 - y, y1 - y), slice(x0 - x, x1 - x))
    fg = np.ascontiguousarray(r.bgr[sl])
    wa, wi = r.w[sl], r.inv[sl]
    if alpha < 1.0:
        wa = wa * alpha
        wi = 1.0 - wa
    roi = np.ascontiguousarray(img[y0:y1, x0:x1])
    img[y0:y1, x0:x1] = cv2.blendLinear(roi, fg, np.ascontiguousarray(wi), np.ascontiguousarray(wa))


class OverlayPainter:
    """Draws a project's text overlays onto finished frames."""

    def __init__(self, settings: RenderSettings, duration: float):
        self.s = settings
        self.items: list[tuple[float, float, TextOverlay]] = []
        for o in settings.overlays:
            if not o.text.strip():
                continue
            start = o.start if o.start >= 0 else max(0.0, duration + o.start)
            end = duration if o.end is None else (o.end if o.end >= 0 else duration + o.end)
            if end > start:
                self.items.append((start, end, o))
        self._blocks: dict[tuple[str, str, int, int], np.ndarray] = {}
        self._ready: dict[tuple[str, str, int, int], _Ready] = {}

    def _block(self, text: str, style_name: str, w: int) -> np.ndarray:
        key = (text, style_name, w, self.s.width)
        if key not in self._blocks:
            style = STYLES.get(style_name, STYLES["caption"])
            box = _hex_rgba(self.s.highlight) if style_name == "cta" else None
            self._blocks[key] = render_block(text, style, w, max(10, int(style.size * self.s.width)), box)
        return self._blocks[key]

    def apply(self, img: np.ndarray, t: float, copy: bool = True) -> np.ndarray:
        """The frame with every overlay showing at time t (a new array if anything was drawn)."""
        showing = [(s, o) for s, e, o in self.items if s <= t < e]
        if not showing:
            return img
        if copy:   # frames can be cached and reused by the compositor
            img = img.copy()
        H, W = img.shape[:2]
        x0, y0, x1, y1 = safe_area(W, H)
        for start, o in showing:
            name = o.style
            if name == "hook" and t - start >= HOOK_BIG_SECONDS:
                name = "hook_small"
            style = STYLES.get(name, STYLES["caption"])
            block = self._block(o.text, name, x1 - x0)
            rkey = (o.text, name, x1 - x0, self.s.width)
            if rkey not in self._ready:
                self._ready[rkey] = _Ready.of(block)
            h, w = block.shape[:2]
            where = o.position or style.position
            if where == "top":
                y = y0
            elif where == "middle":
                y = (y0 + y1 - h) // 2
            elif where == "bottom":
                y = y1 - h
            else:   # lower
                y = int(y0 + 0.72 * (y1 - y0) - h / 2)
            x = x0 if style.align == "left" else (x0 + x1 - w) // 2
            _blend_ready(img, self._ready[rkey], x, y, min(1.0, (t - start) / FADE) if FADE else 1.0)
        return img
