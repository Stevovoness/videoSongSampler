"""Build the self-contained Windows app.

    python build_exe.py            -> dist/VideoSampler.exe          (single file)
    python build_exe.py --onedir   -> dist/VideoSampler/VideoSampler.exe (folder, faster start-up)
    python build_exe.py --both
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(onefile: bool) -> None:
    env = dict(os.environ, VS_ONEFILE="1" if onefile else "0")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "VideoSampler.spec"]
    print(">", " ".join(cmd), "(onefile)" if onefile else "(onedir)")
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def main() -> None:
    if not (ROOT / "tools" / "rubberband" / "rubberband.exe").exists():
        subprocess.run([sys.executable, str(ROOT / "setup_tools.py")], check=True)
    if not (ROOT / "assets" / "icon.ico").exists():
        subprocess.run([sys.executable, str(ROOT / "make_icon.py")], check=True)
    args = set(sys.argv[1:])
    if "--both" in args or "--onedir" in args:
        run(onefile=False)
    if "--onedir" not in args:
        run(onefile=True)
    print("\nDone. Output is in", ROOT / "dist")


if __name__ == "__main__":
    main()
