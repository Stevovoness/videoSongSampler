"""Fetch the external helper tools that get bundled with the app.

- Rubber Band (pitch-preserving time-stretch)        -> tools/rubberband/
- Audiveris + its own Java runtime (sheet music OMR) -> tools/audiveris/Audiveris/
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RB_URL = "https://breakfastquay.com/files/releases/rubberband-4.0.0-gpl-executable-windows.zip"
RB_KEEP = {"rubberband.exe", "sndfile.dll", "COPYING.txt", "README.txt"}
AUDIVERIS_VERSION = "5.11.0"
AUDIVERIS_URL = (f"https://github.com/Audiveris/audiveris/releases/download/{AUDIVERIS_VERSION}/"
                 f"Audiveris-{AUDIVERIS_VERSION}-windowsConsole-x86_64.msi")


def _download(url: str) -> bytes:
    print(f"Downloading {url} …")
    return urllib.request.urlopen(url, timeout=600).read()


def fetch_rubberband() -> None:
    dest = ROOT / "tools" / "rubberband"
    if (dest / "rubberband.exe").exists():
        print(f"Rubber Band already present in {dest}")
        return
    data = _download(RB_URL)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if name in RB_KEEP:
                with z.open(info) as src, open(dest / name, "wb") as out:
                    shutil.copyfileobj(src, out)
    print(f"Rubber Band installed to {dest}")


def fetch_audiveris() -> None:
    """Unpack the Audiveris MSI (it contains its own Java runtime) without installing it."""
    dest = ROOT / "tools" / "audiveris"
    if (dest / "Audiveris" / "Audiveris.exe").exists():
        print(f"Audiveris already present in {dest}")
        return
    with tempfile.TemporaryDirectory() as td:
        msi = Path(td) / "audiveris.msi"
        msi.write_bytes(_download(AUDIVERIS_URL))
        out = Path(td) / "x"
        r = subprocess.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={out}"])
        exe = next(out.rglob("Audiveris.exe"), None)
        if r.returncode != 0 or exe is None:
            raise SystemExit(f"Could not unpack Audiveris (msiexec exit {r.returncode})")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        shutil.copytree(exe.parent, dest / "Audiveris")
        (dest / "VERSION.txt").write_text(AUDIVERIS_VERSION)
    print(f"Audiveris {AUDIVERIS_VERSION} unpacked to {dest}")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("These downloads are Windows builds. On other systems install rubberband and "
                         "Audiveris with your package manager.")
    fetch_rubberband()
    fetch_audiveris()
