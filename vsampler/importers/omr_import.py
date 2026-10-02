"""PDF / image sheet music -> Song via Audiveris optical music recognition."""
from __future__ import annotations

import glob
import os
import subprocess
import tempfile

from ..models import Song
from ..paths import find_audiveris
from .musicxml_import import load_musicxml

AUDIVERIS_URL = "https://github.com/Audiveris/audiveris/releases"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class AudiverisMissing(Exception):
    pass


def load_sheet(path: str, audiveris: str | None = None) -> Song:
    exe = find_audiveris(audiveris)
    if not exe:
        raise AudiverisMissing(
            "Reading PDF or image sheet music needs Audiveris (free).\n"
            f"Install it from {AUDIVERIS_URL}, then try again "
            "(or set its location in Settings)."
        )
    out = tempfile.mkdtemp(prefix="vs_omr_")
    cmd = [exe, "-batch", "-export", "-output", out, "--", path]
    r = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=1800)
    found = glob.glob(os.path.join(out, "**", "*.mxl"), recursive=True) + \
        glob.glob(os.path.join(out, "**", "*.xml"), recursive=True)
    if not found:
        msg = (r.stderr or r.stdout).decode(errors="ignore")[-600:]
        raise RuntimeError("Audiveris could not read this sheet music.\n" + msg)
    # multi-page / multi-movement exports produce several files; use the largest
    found.sort(key=os.path.getsize, reverse=True)
    song = load_musicxml(found[0], kind="sheet")
    song.source_path = path
    return song
