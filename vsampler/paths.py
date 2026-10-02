"""Filesystem locations that work both from source and from a PyInstaller build."""
from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """Folder holding bundled read-only resources (tools/, assets/)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "VideoSampler"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = user_data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def asset(name: str) -> Path:
    return resource_dir() / "assets" / name


def find_rubberband() -> str | None:
    exe = "rubberband.exe" if os.name == "nt" else "rubberband"
    for cand in (resource_dir() / "tools" / "rubberband" / exe, user_data_dir() / "tools" / "rubberband" / exe):
        if cand.is_file():
            return str(cand)
    return shutil.which("rubberband")


def find_audiveris(configured: str | None = None) -> str | None:
    """Locate the Audiveris launcher (used for PDF / image sheet music)."""
    if configured and Path(configured).is_file():
        return configured
    names = ["Audiveris.exe", "Audiveris.bat", "audiveris.bat", "audiveris"]
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", ""),
             str(user_data_dir() / "tools")]
    for root in filter(None, roots):
        for pattern in ("Audiveris*", "Audiveris*/bin", "Audiveris*/app"):
            for d in glob.glob(os.path.join(root, pattern)):
                for n in names:
                    p = os.path.join(d, n)
                    if os.path.isfile(p):
                        return p
    for n in ("Audiveris", "audiveris"):
        p = shutil.which(n)
        if p:
            return p
    return None
