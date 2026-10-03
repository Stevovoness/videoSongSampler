"""Working with long source videos: cutting them into pieces the clip finder and the renderer can handle.

A 2-hour video's sound alone is ~3 GB once decoded, and the clip finder and the clip cache load a file's whole
sound track. So long videos are cut into ~10-minute pieces first. The cut copies the compressed data (no
re-encoding), so it takes seconds, and each piece is an ordinary video file.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import av

PIECE_SECONDS = 600


def duration(path: str) -> float:
    with av.open(path) as c:
        if c.duration:
            return c.duration / 1e6
        s = (c.streams.video or c.streams.audio)[0]
        return float(s.duration * s.time_base) if s.duration else 0.0


def _piece_dir(path: str, root: str | Path) -> Path:
    st = os.stat(path)
    tag = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:10]
    return Path(root) / f"{Path(path).stem[:40]}_{tag}"


def split_video(path: str, root: str | Path, piece_seconds: float = PIECE_SECONDS) -> list[str]:
    """Cut `path` into pieces of about piece_seconds (at keyframes), in a folder under `root`.

    Returns the piece files in order. A video no longer than one piece is returned as it is. The pieces are
    reused if they're already there (the folder name depends on the file's size and date).
    """
    if duration(path) <= piece_seconds * 1.2:
        return [path]
    out = _piece_dir(path, root)
    done = out / "done.txt"
    if done.exists():
        return [str(out / n) for n in done.read_text(encoding="utf-8").split()]
    out.mkdir(parents=True, exist_ok=True)
    pieces: list[str] = []
    inp = av.open(path)
    try:
        streams = [s for s in (inp.streams.video[:1] + inp.streams.audio[:1])]
        video = inp.streams.video[0] if inp.streams.video else None
        cur = None
        mapping: dict[int, av.stream.Stream] = {}
        offsets: dict[int, int] = {}
        start = 0.0
        for pkt in inp.demux(*streams):
            if pkt.dts is None or pkt.pts is None:
                continue
            t = float(pkt.pts * pkt.time_base)
            cut_here = (pkt.stream is video and pkt.is_keyframe) if video is not None else True
            if cut_here and (cur is None or t - start >= piece_seconds):
                if cur is not None:
                    cur.close()
                name = f"part{len(pieces):03d}.mp4"
                cur = av.open(str(out / name), "w")
                mapping = {s.index: cur.add_stream_from_template(s) for s in streams}
                offsets = {}
                pieces.append(name)
                start = t
            if cur is None:
                continue
            idx = pkt.stream.index
            offsets.setdefault(idx, pkt.dts)
            pkt.pts -= offsets[idx]
            pkt.dts -= offsets[idx]
            if pkt.pts < 0 or pkt.dts < 0:
                continue
            pkt.stream = mapping[idx]
            cur.mux(pkt)
        if cur is not None:
            cur.close()
    finally:
        inp.close()
    done.write_text("\n".join(pieces), encoding="utf-8")
    return [str(out / n) for n in pieces]
