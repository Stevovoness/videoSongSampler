"""Collage geometry."""
from __future__ import annotations

import math

import cv2
import numpy as np

Rect = tuple[int, int, int, int]  # x, y, w, h


def fit_grid(n: int, width: int, height: int, aspect: float, gap: int = 6) -> list[Rect]:
    """Place n tiles of the given aspect (w/h) as large as possible, centred."""
    if n <= 0:
        return []
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        tw = (width - gap * (cols + 1)) / cols
        th = (height - gap * (rows + 1)) / rows
        if tw <= 0 or th <= 0:
            continue
        w = min(tw, th * aspect)
        h = w / aspect
        if best is None or w * h > best[0]:
            best = (w * h, cols, rows, int(w), int(h))
    if best is None:
        return [(0, 0, width, height)] * n
    _, cols, rows, w, h = best
    rects = []
    total_h = rows * h + (rows - 1) * gap
    y0 = (height - total_h) // 2
    for r in range(rows):
        in_row = min(cols, n - r * cols)
        total_w = in_row * w + (in_row - 1) * gap
        x0 = (width - total_w) // 2
        for c in range(in_row):
            rects.append((x0 + c * (w + gap), y0 + r * (h + gap), w, h))
    return rects


def cover(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Scale and centre-crop img to exactly w x h."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    nw, nh = max(w, int(round(iw * s))), max(h, int(round(ih * s)))
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
    r = cv2.resize(img, (nw, nh), interpolation=interp)
    x, y = (nw - w) // 2, (nh - h) // 2
    return r[y:y + h, x:x + w]


def hex_to_bgr(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) != 6:
        return (0, 0, 0)
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (b, g, r)


def draw_label(img: np.ndarray, text: str, x: int, y: int, h: int, highlight: tuple[int, int, int] | None) -> None:
    scale = max(0.4, min(1.4, h / 220))
    th = max(1, int(round(scale * 2)))
    (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, th)
    pad = int(6 * scale) + 2
    bx, by = x + pad, y + h - pad
    x1, y1, x2, y2 = bx - pad // 2, by - tht - pad // 2 - base, bx + tw + pad // 2, by + pad // 2
    roi = img[max(0, y1):y2, max(0, x1):x2]
    if roi.size:
        roi[:] = (roi * 0.35).astype(np.uint8)
    color = highlight if highlight else (235, 235, 235)
    cv2.putText(img, text, (bx, by - base // 2), cv2.FONT_HERSHEY_DUPLEX, scale, color, th, cv2.LINE_AA)
