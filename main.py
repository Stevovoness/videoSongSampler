"""Video Sampler - entry point."""
import multiprocessing
import os
import sys

# The windowed exe has no console: sys.stdout / sys.stderr are None and any library that prints
# (music21 warns on import) would crash. Send that output to a log file instead.
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VideoSampler")
    os.makedirs(_log_dir, exist_ok=True)
    # worker processes (the clip finder's pitch tracking) start this file again: they add to the log, not wipe it
    _child = any(a.startswith("--multiprocessing") for a in sys.argv)
    _log = open(os.path.join(_log_dir, "output.log"), "a" if _child else "w", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log


def main() -> int:
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] in ("analyse", "analyze", "auto", "render", "--render", "-h", "--help"):
        from vsampler.cli import main as cli_main   # command line, no window (see vsampler/cli.py)
        return cli_main(sys.argv[1:])
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    try:
        import PySide6  # noqa: F401
    except ImportError:
        here = os.path.dirname(os.path.abspath(__file__))
        venv = os.path.join(here, ".venv", "Scripts", "python.exe")
        print(f"This Python ({sys.version.split()[0]}, {sys.executable}) doesn't have Video Sampler's packages.\n"
              + (f"Run it with the project's environment instead:\n  \"{venv}\" main.py\n" if os.path.exists(venv) else
                 "Set up the environment first (see 'From source' in README.md):\n"
                 "  py -3.11 -m venv .venv\n  .venv\\Scripts\\pip install -r requirements.txt\n"
                 "  .venv\\Scripts\\pip install --no-deps basic-pitch==0.4.0\n"),
              file=sys.stderr)
        return 1
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from vsampler import APP_NAME
    from vsampler.gui import theme
    from vsampler.gui.main_window import MainWindow
    from vsampler.paths import asset

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VideoSampler.App")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("VideoSampler")
    ico = asset("icon.ico")
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    theme.apply(app)
    from vsampler.gui import screen
    screen.install(app)          # no window or dialog may extend past the edges of the screen
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
