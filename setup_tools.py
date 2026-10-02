"""Fetch external helper tools.

- Rubber Band (pitch-preserving time-stretch) -> tools/rubberband/
- Checks for Audiveris (PDF / image sheet music reader), which must be installed separately.
"""
from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RB_URL = "https://breakfastquay.com/files/releases/rubberband-4.0.0-gpl-executable-windows.zip"
RB_KEEP = {"rubberband.exe", "sndfile.dll", "COPYING.txt", "README.txt"}


def fetch_rubberband() -> None:
    dest = ROOT / "tools" / "rubberband"
    if (dest / "rubberband.exe").exists():
        print(f"Rubber Band already present in {dest}")
        return
    if sys.platform != "win32":
        print("Not on Windows: install rubberband with your package manager (e.g. brew/apt install rubberband).")
        return
    print(f"Downloading Rubber Band from {RB_URL} …")
    data = urllib.request.urlopen(RB_URL, timeout=120).read()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if name in RB_KEEP:
                with z.open(info) as src, open(dest / name, "wb") as out:
                    shutil.copyfileobj(src, out)
    print(f"Rubber Band installed to {dest}")


def check_audiveris() -> None:
    sys.path.insert(0, str(ROOT))
    from vsampler.paths import find_audiveris
    exe = find_audiveris()
    if exe:
        print(f"Audiveris found: {exe}")
    else:
        print("Audiveris not found. It is only needed for PDF / image sheet music.\n"
              "Get it from https://github.com/Audiveris/audiveris/releases")


if __name__ == "__main__":
    fetch_rubberband()
    check_audiveris()
