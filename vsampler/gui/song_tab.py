"""Step 2: load the song (MIDI, MusicXML, audio or sheet music)."""
from __future__ import annotations

import os
import tempfile
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from ..importers import FILE_FILTER, load_song, song_kind
from ..importers.omr_import import AUDIVERIS_URL, AudiverisMissing
from ..notes import midi_to_name
from ..render.renderer import Cancelled, render_audio_preview
from ..songops import default_tracks, suggest_transpose
from . import theme
from .clips_tab import hint
from .state import AppState
from .widgets.piano_roll import PianoRoll
from .widgets.player import AudioPlayer
from .worker import run_task

KIND_LABEL = {"midi": "MIDI file", "musicxml": "MusicXML sheet music", "audio": "Audio recording (auto-transcribed)",
              "sheet": "PDF / image sheet music (read with Audiveris)"}


class DropFrame(QFrame):
    def __init__(self, on_file, parent=None):
        super().__init__(parent)
        self.on_file = on_file
        self.setAcceptDrops(True)
        self.setProperty("card", True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.on_file(urls[0].toLocalFile())


class SongTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task = None
        self.player = AudioPlayer(self)
        self.player.position.connect(lambda t: self.roll.set_playhead(t))
        self.player.stopped.connect(self._preview_stopped)
        self._wav = os.path.join(tempfile.gettempdir(), "vs_song_preview.wav")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        title = QLabel("Step 2 · The song")
        title.setProperty("h1", True)
        root.addWidget(title)

        # ---- file card
        card = DropFrame(self.open_path)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        info = QVBoxLayout()
        self.file_lab = QLabel("<b>No song loaded</b>")
        self.kind_lab = hint("Drop a file here, or click “Open song…”. Works with MIDI (.mid), MusicXML sheet music "
                             "(.musicxml/.mxl), audio (.mp3/.wav…) and PDF/image sheet music.")
        info.addWidget(self.file_lab)
        info.addWidget(self.kind_lab)
        cl.addLayout(info, 1)
        self.busy = QProgressBar()
        self.busy.setVisible(False)
        self.busy.setFixedWidth(220)
        cl.addWidget(self.busy)
        self.open_btn = QPushButton("Open song…")
        self.open_btn.setProperty("primary", True)
        self.open_btn.clicked.connect(self.choose_file)
        cl.addWidget(self.open_btn)
        root.addWidget(card)

        # ---- audio import options
        self.audio_box = QGroupBox("Transcription settings (audio songs)")
        al = QHBoxLayout(self.audio_box)
        al.addWidget(QLabel("Sensitivity"))
        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(20, 80)
        self.sens.setToolTip("Higher = picks up more (and quieter) notes")
        al.addWidget(self.sens, 1)
        al.addWidget(QLabel("Shortest note"))
        self.min_ms = QSpinBox()
        self.min_ms.setRange(30, 1000)
        self.min_ms.setSuffix(" ms")
        al.addWidget(self.min_ms)
        self.a_melody = QCheckBox("Melody only")
        al.addWidget(self.a_melody)
        redo = QPushButton("Transcribe again")
        redo.clicked.connect(self.reload)
        al.addWidget(redo)
        root.addWidget(self.audio_box)

        # ---- middle row: tracks + adjustments + missing notes
        mid = QHBoxLayout()
        tb = QGroupBox("Parts / tracks to play")
        tl = QVBoxLayout(tb)
        self.tracks = QListWidget()
        self.tracks.itemChanged.connect(self._tracks_changed)
        tl.addWidget(self.tracks)
        mid.addWidget(tb, 3)

        ab = QGroupBox("Adjust")
        a2 = QVBoxLayout(ab)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Transpose"))
        self.transpose = QSpinBox()
        self.transpose.setRange(-36, 36)
        self.transpose.setSuffix(" semitones")
        r1.addWidget(self.transpose)
        best = QPushButton("Best fit for my clips")
        best.setToolTip("Find the key change that lets the most notes use your clips directly")
        best.clicked.connect(self.best_fit)
        r1.addWidget(best)
        a2.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Speed"))
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(25, 400)
        self.tempo.setSuffix(" %")
        self.tempo.setDecimals(0)
        r2.addWidget(self.tempo)
        r2.addStretch(1)
        a2.addLayout(r2)
        self.melody = QCheckBox("Melody only (play just the top note of chords)")
        a2.addWidget(self.melody)
        a2.addStretch(1)
        mid.addWidget(ab, 3)

        self.miss = QFrame()
        self.miss.setProperty("good", True)
        ml = QVBoxLayout(self.miss)
        self.miss_title = QLabel("")
        self.miss_title.setStyleSheet("font-weight:700;")
        self.miss_text = QLabel("")
        self.miss_text.setWordWrap(True)
        self.shift = QCheckBox("Use nearest clip and pitch-shift missing notes")
        self.max_shift = QSpinBox()
        self.max_shift.setRange(1, 24)
        self.max_shift.setPrefix("up to ")
        self.max_shift.setSuffix(" semitones")
        ml.addWidget(self.miss_title)
        ml.addWidget(self.miss_text, 1)
        ml.addWidget(self.shift)
        ml.addWidget(self.max_shift)
        mid.addWidget(self.miss, 4)
        root.addLayout(mid)

        # ---- piano roll
        rb = QGroupBox("Song notes   (green = has a clip · amber = borrowed and pitch-shifted · red = missing)")
        rl = QVBoxLayout(rb)
        self.roll = PianoRoll()
        rl.addWidget(self.roll, 1)
        prow = QHBoxLayout()
        self.preview_btn = QPushButton("▶  Listen to the song with my clips")
        self.preview_btn.clicked.connect(self.toggle_preview)
        prow.addWidget(self.preview_btn)
        self.prev_status = hint("")
        prow.addWidget(self.prev_status, 1)
        zi, zo = QPushButton("Zoom +"), QPushButton("Zoom −")
        zi.clicked.connect(lambda: self.roll.zoom(1.4))
        zo.clicked.connect(lambda: self.roll.zoom(1 / 1.4))
        prow.addWidget(zo)
        prow.addWidget(zi)
        rl.addLayout(prow)
        root.addWidget(rb, 1)

        for w, sig in ((self.transpose, "valueChanged"), (self.tempo, "valueChanged"), (self.melody, "toggled"),
                       (self.shift, "toggled"), (self.max_shift, "valueChanged"), (self.sens, "valueChanged"),
                       (self.min_ms, "valueChanged"), (self.a_melody, "toggled")):
            getattr(w, sig).connect(self._opts_changed)
        state.slots_changed.connect(self.refresh)
        state.options_changed.connect(self.refresh)
        state.song_changed.connect(self._song_changed)
        state.project_replaced.connect(self._project_replaced)
        self._load_opts()
        self._song_changed()

    # ------------------------------------------------------------------ options <-> widgets
    def _load_opts(self) -> None:
        o = self.state.project.song
        self._loading = True
        self.transpose.setValue(o.transpose)
        self.tempo.setValue(o.tempo_pct)
        self.melody.setChecked(o.melody_only)
        self.shift.setChecked(o.allow_pitch_shift)
        self.max_shift.setValue(o.max_shift)
        self.sens.setValue(int(round((1 - o.audio_onset_threshold) * 100)))
        self.min_ms.setValue(int(o.audio_min_note_ms))
        self.a_melody.setChecked(o.audio_melody_only)
        self._loading = False

    def _opts_changed(self, *_a) -> None:
        if self._loading:
            return
        o = self.state.project.song
        o.transpose = self.transpose.value()
        o.tempo_pct = self.tempo.value()
        o.melody_only = self.melody.isChecked()
        o.allow_pitch_shift = self.shift.isChecked()
        o.max_shift = self.max_shift.value()
        o.audio_onset_threshold = 1 - self.sens.value() / 100
        o.audio_min_note_ms = float(self.min_ms.value())
        o.audio_melody_only = self.a_melody.isChecked()
        self.state.touch_options()

    def _tracks_changed(self, _item) -> None:
        if self._loading:
            return
        self.state.project.song.enabled_tracks = [
            self.tracks.item(i).data(Qt.UserRole) for i in range(self.tracks.count())
            if self.tracks.item(i).checkState() == Qt.Checked]
        self.state.touch_options()

    def _project_replaced(self) -> None:
        self._load_opts()
        if self.state.project.song_path and os.path.exists(self.state.project.song_path):
            self.open_path(self.state.project.song_path, keep_tracks=True)
        else:
            self.state.song = None
            self.state.song_changed.emit()

    # ------------------------------------------------------------------ loading
    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open song", "", FILE_FILTER)
        if path:
            self.open_path(path)

    def reload(self) -> None:
        if self.state.project.song_path:
            self.open_path(self.state.project.song_path, keep_tracks=True)

    def open_path(self, path: str, keep_tracks: bool = False) -> None:
        try:
            kind = song_kind(path)
        except ValueError as e:
            QMessageBox.warning(self, "Can’t open this file", str(e))
            return
        self.player.stop()
        self.open_btn.setEnabled(False)
        self.busy.setVisible(True)
        self.busy.setRange(0, 0)
        msg = {"audio": "Listening to the song and writing down the notes… (can take a minute)",
               "sheet": "Reading the sheet music… (can take a few minutes)"}.get(kind, "Loading…")
        self.file_lab.setText(f"<b>{os.path.basename(path)}</b>")
        self.kind_lab.setText(msg)

        def done(song):
            self._finish_busy()
            self.state.song = song
            self.state.project.song_path = path
            if not keep_tracks:
                self.state.project.song.enabled_tracks = None
            self.state.dirty = True
            self.state.song_changed.emit()

        def fail(e, _tb):
            self._finish_busy()
            self.kind_lab.setText(KIND_LABEL.get(kind, ""))
            if isinstance(e, AudiverisMissing):
                self._audiveris_missing(path)
            else:
                QMessageBox.warning(self, "Couldn’t read the song", f"{os.path.basename(path)}\n\n{e}")
            self._song_changed()

        run_task(load_song, path, self.state.project.song, self.state.project.audiveris_path or None,
                 on_done=done, on_error=fail)

    def _finish_busy(self) -> None:
        self.busy.setVisible(False)
        self.open_btn.setEnabled(True)

    def _audiveris_missing(self, path: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Sheet music reader needed")
        box.setText("To read PDF or picture sheet music, Video Sampler uses <b>Audiveris</b> "
                    "(free and open source).<br><br>Install it, then open the file again. If it’s installed "
                    "somewhere unusual, click “Locate Audiveris…”.<br><br>"
                    "Tip: MuseScore can also export sheet music as MusicXML, which loads instantly.")
        dl = box.addButton("Download Audiveris", QMessageBox.ActionRole)
        loc = box.addButton("Locate Audiveris…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is dl:
            webbrowser.open(AUDIVERIS_URL)
        elif box.clickedButton() is loc:
            exe, _ = QFileDialog.getOpenFileName(self, "Find Audiveris", "", "Audiveris (Audiveris*.exe Audiveris*.bat);;All (*)")
            if exe:
                self.state.project.audiveris_path = exe
                from PySide6.QtCore import QSettings
                QSettings("VideoSampler", "VideoSampler").setValue("audiveris", exe)
                self.open_path(path)

    # ------------------------------------------------------------------ refresh
    def _song_changed(self) -> None:
        song = self.state.song
        self.audio_box.setVisible(song is not None and song.kind == "audio")
        self._loading = True
        self.tracks.clear()
        if song:
            enabled = self.state.project.song.enabled_tracks
            if enabled is None:
                enabled = default_tracks(song)
            for t in song.tracks:
                label = f"{t.name}   ·  {t.note_count} notes  ·  {midi_to_name(t.low)}–{midi_to_name(t.high)}"
                if t.is_drum:
                    label += "  (drums)"
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, t.index)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if t.index in enabled else Qt.Unchecked)
                self.tracks.addItem(it)
            self.file_lab.setText(f"<b>{os.path.basename(song.source_path)}</b>")
            m, s = divmod(int(song.duration), 60)
            self.kind_lab.setText(f"{KIND_LABEL.get(song.kind, song.kind)} · {len(song.events)} notes · {m}:{s:02d}")
        self._loading = False
        self.refresh()

    def refresh(self) -> None:
        evs = self.state.events()
        plan = self.state.plan()
        exact = plan.exact if plan else set()
        borrowed = set(plan.borrowed) if plan else set()
        self.roll.set_events(evs, exact, borrowed)
        self.preview_btn.setEnabled(bool(plan and plan.instances))
        o = self.state.project.song
        self.max_shift.setEnabled(o.allow_pitch_shift)
        if not self.state.song:
            self.miss.setProperty("good", True)
            self.miss.setProperty("warn", False)
            self.miss_title.setText("Notes check")
            self.miss_text.setText("Load a song to see which notes it needs.")
        else:
            needed = sorted({e.pitch for e in evs})
            no_clip = [p for p in needed if p not in exact]
            unresolved = sorted(plan.missing)
            warn = bool(unresolved)
            self.miss.setProperty("warn", warn)
            self.miss.setProperty("good", not warn)
            if not no_clip:
                self.miss_title.setText("✔  Every note has a clip!")
                self.miss_text.setText(f"The song uses {len(needed)} different notes and you have all of them.")
            elif unresolved:
                self.miss_title.setText(f"⚠  {len(unresolved)} note{'s' if len(unresolved) > 1 else ''} "
                                        f"{'have' if len(unresolved) > 1 else 'has'} no clip "
                                        f"({plan.total_missing} note{'s' if plan.total_missing != 1 else ''} will be silent)")
                names = ", ".join(midi_to_name(p) for p in unresolved)
                txt = f"Missing: <b>{names}</b>.<br>Add clips for these in Step 1"
                txt += " or tick the box below to borrow the nearest clip." if not o.allow_pitch_shift else \
                    ", or allow a bigger pitch-shift range."
                self.miss_text.setText(txt)
            else:
                self.miss_title.setText("✔  All notes covered (some borrowed)")
                pairs = ", ".join(f"{midi_to_name(p)}←{midi_to_name(s)}" for p, s in sorted(plan.borrowed.items()))
                self.miss_text.setText(f"Pitch-shifted from the nearest clip: {pairs}")
            for w in (self.miss,):
                w.style().unpolish(w)
                w.style().polish(w)

    def best_fit(self) -> None:
        if not self.state.song:
            return
        o = self.state.project.song
        saved = o.transpose
        o.transpose = 0
        t = suggest_transpose(self.state.song, o, set(self.state.project.slots))
        o.transpose = saved
        self.transpose.setValue(t)

    # ------------------------------------------------------------------ audio preview
    def toggle_preview(self) -> None:
        if self.player.is_playing():
            self.player.stop()
            return
        if self.task:
            self.task.cancel.set()
            return
        self.preview_btn.setText("■  Stop (preparing…)")

        def prog(f, m):
            self.prev_status.setText(m)

        def done(path):
            self.task = None
            self.prev_status.setText("")
            self.preview_btn.setText("■  Stop")
            self.player.play(path)

        def fail(e, tb):
            self.task = None
            self.preview_btn.setText("▶  Listen to the song with my clips")
            self.prev_status.setText("")
            if not isinstance(e, Cancelled):
                QMessageBox.warning(self, "Preview failed", str(e))

        import copy
        self.task = run_task(render_audio_preview, copy.deepcopy(self.state.project), self.state.song, self._wav,
                             on_done=done, on_error=fail, on_progress=prog)

    def _preview_stopped(self) -> None:
        self.preview_btn.setText("▶  Listen to the song with my clips")
        self.roll.set_playhead(None)
