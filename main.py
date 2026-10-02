"""Video Sampler - entry point."""
import multiprocessing
import os
import sys

# The windowed exe has no console: sys.stdout / sys.stderr are None and any library that prints
# (music21 warns on import) would crash. Send that output to a log file instead.
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VideoSampler")
    os.makedirs(_log_dir, exist_ok=True)
    _log = open(os.path.join(_log_dir, "output.log"), "w", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log


def cli_render(project_path: str, out_path: str) -> int:
    """Render a saved project without the GUI:  VideoSampler --render my.vsproj out.mp4

    Progress and errors go to stdout and to out_path + '.log' (the windowed exe has no console).
    """
    import traceback

    from vsampler.importers import load_song
    from vsampler.models import Project
    from vsampler.render.renderer import render_video

    log = open(out_path + ".log", "w", encoding="utf-8")

    def say(msg: str) -> None:
        print(msg, flush=True)
        log.write(msg + "\n")
        log.flush()

    try:
        project = Project.load(project_path)
        say(f"Loading song {project.song_path}")
        song = load_song(project.song_path, project.song, project.audiveris_path or None)
        say(f"{len(song.events)} notes, {len(song.tracks)} tracks")
        last = [-1]

        def progress(f: float, m: str) -> None:
            if int(f * 10) != last[0]:
                last[0] = int(f * 10)
                say(f"{int(f * 100):3d}%  {m}")

        render_video(project, song, out_path, progress)
        say(f"OK {out_path}")
        return 0
    except Exception:  # noqa: BLE001
        say("FAILED\n" + traceback.format_exc())
        return 1
    finally:
        log.close()


def main() -> int:
    multiprocessing.freeze_support()
    if len(sys.argv) == 4 and sys.argv[1] == "--render":
        return cli_render(sys.argv[2], sys.argv[3])
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
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
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
