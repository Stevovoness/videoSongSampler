"""Build the self-contained Windows release.

    python build_exe.py

Produces in release/:
    VideoSampler-Setup-<version>.exe   one-click installer (no admin rights needed)
    VideoSampler-<version>-Windows-portable.zip   unzip anywhere and run VideoSampler.exe

Nothing needs to be installed on the target PC: Python, FFmpeg, Rubber Band, the basic-pitch model and
Audiveris (with its own Java runtime) are all inside. Building the installer needs Inno Setup 6
(winget install JRSoftware.InnoSetup); without it only the zip is made.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from vsampler import __version__  # noqa: E402


def find_iscc() -> str | None:
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        for sub in (r"Programs\Inno Setup 6", "Inno Setup 6"):
            p = Path(base) / sub / "ISCC.exe"
            if p.is_file():
                return str(p)
    return shutil.which("ISCC")


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "setup_tools.py")], check=True)
    if not (ROOT / "assets" / "icon.ico").exists():
        subprocess.run([sys.executable, str(ROOT / "make_icon.py")], check=True)

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "VideoSampler.spec"],
                   cwd=ROOT, check=True)

    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    zip_base = out / f"VideoSampler-{__version__}-Windows-portable"
    print(f"Zipping {zip_base}.zip")
    shutil.make_archive(str(zip_base), "zip", ROOT / "dist", "VideoSampler")

    iscc = find_iscc()
    if iscc:
        subprocess.run([iscc, f"/DAppVersion={__version__}", "/Q", str(ROOT / "installer" / "VideoSampler.iss")],
                       check=True)
        for f in (ROOT / "installer" / "Output").glob("*.exe"):
            shutil.move(str(f), out / f.name)
    else:
        print("Inno Setup not found - skipped the installer (winget install JRSoftware.InnoSetup)")
    print("\nDone:")
    for f in sorted(out.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
