"""Main window: three-step workflow + project files."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QTabWidget

from .. import APP_NAME, __version__, audio_dsp
from ..models import Project
from ..paths import asset, find_audiveris
from .clips_tab import ClipsTab
from .screen import preferred_size, scrollable
from .output_tab import OutputTab
from .song_tab import SongTab
from .state import AppState

PROJECT_FILTER = "Video Sampler project (*.vsproj)"

HELP = """<h3>How it works</h3>
<ol>
<li><b>Your sounds</b> – record short videos of someone (or something!) making single notes.
Add them and the app works out which note each one is and puts it on the keyboard.
Auto-tune nudges each one to be perfectly in tune.
Switch to <b>🥁 Drums</b> for percussive clips (slaps, claps, smacks…): each one goes on a drum pad and plays
whenever the song's drum part hits that sound.
Got one long video instead? Click <b>✂ Cut clips from a long video…</b>: the app suggests every note and hit in it,
you adjust them on a timeline and add them all at once. Several takes of a note are used in turn.</li>
<li><b>The song</b> – open a MIDI file, MusicXML sheet music, an mp3/wav recording (the notes are worked out
automatically), or a PDF/picture of sheet music (needs the free Audiveris reader).
Choose which parts to play, change key or speed, and check every note has a clip.</li>
<li><b>Make the video</b> – choose a collage style and click <i>Make my video</i>.</li>
</ol>
<p>When a note is longer than its clip, the clip is <b>stretched without changing its pitch</b>:
the start of the sound plays normally and the rest is slowed down, with the video slowed to match.</p>
<p><b>Play your clips like an instrument:</b> click a key or pad in Step 1 to hear it exactly as it will sound
in the video. Or use your computer keyboard: <b>A W S E D F T G Y H U J K O L P ;</b> play the notes
(<b>Z</b> / <b>X</b> change octave), and on the drum pads <b>1 2 3 4 · Q W E R · A S D F · Z X C V</b> play the pads.</p>
<p>Notes with no clip are reported in Step 2. Tick <b>Octave jump</b> to play them from the same note an octave
higher or lower (handy when a song goes above or below your clips). You can also let the app borrow the nearest
clip and pitch-shift it. Missing drum sounds can use a similar drum clip instead.</p>"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.state = AppState()
        saved = QSettings("VideoSampler", "VideoSampler").value("audiveris", "")
        if saved:
            self.state.project.audiveris_path = saved
        ico = asset("icon.ico")
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))
        self.resize(preferred_size(None, 1320, 900))

        self.tabs = QTabWidget()
        self.clips = ClipsTab(self.state)
        self.song = SongTab(self.state)
        self.output = OutputTab(self.state)
        # each step scrolls rather than pushing the window past the edge of a small screen
        self.tabs.addTab(scrollable(self.clips), "  1 · Your sounds  ")
        self.tabs.addTab(scrollable(self.song), "  2 · The song  ")
        self.tabs.addTab(scrollable(self.output), "  3 · Make the video  ")
        self.setCentralWidget(self.tabs)

        self._menus()
        rb = audio_dsp.backend_name()
        omr = "found" if find_audiveris(self.state.project.audiveris_path) else "not installed (only needed for PDF sheet music)"
        self.statusBar().showMessage(f"Stretch engine: {rb}   ·   Sheet-music reader (Audiveris): {omr}")
        self._title()
        for sig in (self.state.slots_changed, self.state.options_changed, self.state.song_changed):
            sig.connect(self._title)

    def _menus(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, key, fn in (("&New project", QKeySequence.New, self.new_project),
                              ("&Open project…", QKeySequence.Open, self.open_project),
                              ("&Save project", QKeySequence.Save, self.save_project),
                              ("Save project &as…", QKeySequence.SaveAs, self.save_project_as)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(fn)
            m.addAction(a)
        m.addSeparator()
        q = QAction("E&xit", self)
        q.triggered.connect(self.close)
        m.addAction(q)
        h = self.menuBar().addMenu("&Help")
        a = QAction("How it works", self)
        a.triggered.connect(lambda: QMessageBox.information(self, "How it works", HELP))
        h.addAction(a)
        b = QAction("About", self)
        b.triggered.connect(lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME}</b> {__version__}<br>Turn video clips of single notes into a song.<br><br>"
                            "Uses Rubber Band (GPL), FFmpeg, Qt, librosa, music21 and Spotify's basic-pitch."))
        h.addAction(b)

    def _title(self) -> None:
        name = os.path.basename(self.state.project_path) if self.state.project_path else "Untitled"
        self.setWindowTitle(f"{APP_NAME} — {name}{' •' if self.state.dirty else ''}")

    # ------------------------------------------------------------------ projects
    def _confirm_discard(self) -> bool:
        if not self.state.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save your project first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        aud = self.state.project.audiveris_path
        self.state.project = Project(audiveris_path=aud)
        self.state.song = None
        self.state.project_path = ""
        self.state.dirty = False
        self.state.project_replaced.emit()
        self.state.song_changed.emit()
        self._title()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", PROJECT_FILTER)
        if not path:
            return
        try:
            p = Project.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Couldn’t open project", str(e))
            return
        missing = [s.path for s in [*p.slots.values(), *p.drum_slots.values()] if not os.path.exists(s.path)]
        if not p.audiveris_path:
            p.audiveris_path = self.state.project.audiveris_path
        self.state.project = p
        self.state.song = None
        self.state.project_path = path
        self.state.project_replaced.emit()
        self.state.dirty = False
        self._title()
        if missing:
            QMessageBox.warning(self, "Some clips are missing",
                                "These video files couldn’t be found:\n\n" + "\n".join(missing[:15]))

    def save_project(self) -> bool:
        if not self.state.project_path:
            return self.save_project_as()
        self.state.project.save(self.state.project_path)
        self.state.dirty = False
        self._title()
        return True

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "my_song.vsproj", PROJECT_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".vsproj"):
            path += ".vsproj"
        self.state.project_path = path
        return self.save_project()

    def closeEvent(self, e) -> None:
        if self.output.task is not None and QMessageBox.question(
                self, "Still rendering", "A video is still being made. Quit anyway?") != QMessageBox.Yes:
            e.ignore()
            return
        if not self._confirm_discard():
            e.ignore()
            return
        if self.output.task is not None:
            self.output.task.cancel.set()
        e.accept()
