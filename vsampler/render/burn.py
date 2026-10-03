"""Put text on an already-rendered video, without rendering it again.

The picture is decoded, the overlays are drawn on each frame (`overlays.OverlayPainter`) and it's encoded again;
the sound is copied across untouched. Decoding and drawing run in a second thread while the main thread
encodes, so a 30-second Short takes well under a minute instead of a full render.
"""
from __future__ import annotations

import dataclasses
import queue
import threading
from typing import Callable

import av
import cv2
import numpy as np

from ..models import RenderSettings, TextOverlay
from .overlays import OverlayPainter

ProgressFn = Callable[[float, str], None]
_END = object()


def _noop(_f: float, _m: str) -> None:
    pass


def burn_overlays(base: str, overlays: list[TextOverlay], out_path: str, settings: RenderSettings | None = None,
                  progress: ProgressFn = _noop) -> str:
    """Write `out_path`: the video `base` with `overlays` drawn on it (times are seconds into the video).

    `settings` supplies the highlight colour; its size is taken from the video itself.
    """
    with av.open(base) as probe:
        vin = probe.streams.video[0]
        fps = vin.average_rate or 30
        W, H = vin.codec_context.width, vin.codec_context.height
        duration = probe.duration / 1e6 if probe.duration else float(vin.duration * vin.time_base)
        total = vin.frames or int(duration * float(fps))
    rs = dataclasses.replace(settings or RenderSettings(), width=W, height=H, overlays=list(overlays))
    painter = OverlayPainter(rs, duration)

    frames: queue.Queue = queue.Queue(maxsize=16)
    audio: list = []
    failed: list[BaseException] = []

    def produce() -> None:
        """Decode, draw the text and convert to the encoder's format (in this thread, while encoding runs)."""
        try:
            with av.open(base) as inp:
                v = inp.streams.video[0]
                v.thread_type = "AUTO"
                a = inp.streams.audio[0] if inp.streams.audio else None
                n = 0
                for pkt in inp.demux(*([v] + ([a] if a is not None else []))):
                    if pkt.stream is a:
                        if pkt.dts is not None:
                            audio.append(pkt)
                        continue
                    for frame in pkt.decode():
                        t = float(frame.time) if frame.time is not None else n / float(fps)
                        img = cv2.cvtColor(frame.to_ndarray(format="yuv420p"), cv2.COLOR_YUV2BGR_I420) \
                            if frame.format.name == "yuv420p" else frame.to_ndarray(format="bgr24")
                        img = painter.apply(img, t, copy=False)   # a fresh frame: draw on it directly
                        frames.put(cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_BGR2YUV_I420))
                        n += 1
        except BaseException as e:  # noqa: BLE001 - reported by the main thread
            failed.append(e)
        finally:
            frames.put(_END)

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    with av.open(base) as tmpl:
        ain = tmpl.streams.audio[0] if tmpl.streams.audio else None
        out = av.open(out_path, "w")
        try:
            vs = out.add_stream("libx264", rate=fps)
            vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
            vs.options = {"crf": "18", "preset": "superfast"}
            vs.codec_context.thread_type = "AUTO"
            aout = out.add_stream_from_template(ain) if ain is not None else None
            n = 0
            while True:
                yuv = frames.get()
                if yuv is _END:
                    break
                vf = av.VideoFrame.from_ndarray(yuv, format="yuv420p")
                vf.pts = n
                for p in vs.encode(vf):
                    out.mux(p)
                n += 1
                if n % 15 == 0:
                    progress(min(0.99, n / max(1, total)), f"Adding text… frame {n} of {total}")
            worker.join()
            if failed:
                raise failed[0]
            for p in vs.encode():
                out.mux(p)
            for pkt in audio:    # the sound, copied as it is
                pkt.stream = aout
                out.mux(pkt)
        finally:
            out.close()
    progress(1.0, "Done!")
    return out_path
