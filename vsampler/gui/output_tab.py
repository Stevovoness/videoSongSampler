"""Step 3: choose the collage style and render the video."""
from __future__ import annotations

import copy
import os
import subprocess

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ..render.renderer import Cancelled, preview_frame, render_video
from .clips_tab import hint
from .state import AppState, to_pixmap
from .worker import run_task

RESOLUTIONS = [("720p  (1280×720)", 1280, 720), ("1080p (1920×1080)", 1920, 1080), ("Square (1080×1080)", 1080, 1080),
               ("Vertical / phone (1080×1920)", 1080, 1920), ("Small (854×480)", 854, 480)]


class ColorButton(QPushButton):
    def __init__(self, color: str, on_change):
        super().__init__()
        self.on_change = on_change
        self.set_color(color)
        self.clicked.connect(self._pick)
        self.setFixedWidth(90)

    def set_color(self, c: str) -> None:
        self.color = c
        self.setStyleSheet(f"background:{c}; border:1px solid #555; border-radius:6px; min-height:22px;")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.set_color(c.name())
            self.on_change()


class LayoutCard(QFrame):
    def __init__(self, radio: QRadioButton, text: str):
        super().__init__()
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        radio.setStyleSheet("font-weight:700; font-size:11pt;")
        lay.addWidget(radio)
        lay.addWidget(hint(text))


class OutputTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task = None
        self.last_output = ""
        self._loading = False

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        left = QVBoxLayout()
        title = QLabel("Step 3 · Make the video")
        title.setProperty("h1", True)
        left.addWidget(title)

        # layout choice
        self.r_grid = QRadioButton("Collage grid")
        self.r_dyn = QRadioButton("Only who’s singing")
        grp = QButtonGroup(self)
        grp.addButton(self.r_grid)
        grp.addButton(self.r_dyn)
        cards = QHBoxLayout()
        cards.addWidget(LayoutCard(self.r_grid, "Every clip gets a tile. A tile springs to life and lights up "
                                                "when its note plays; the others wait, dimmed."))
        cards.addWidget(LayoutCard(self.r_dyn, "The screen shows only the clips that are sounding right now: "
                                               "one note = full screen, chords split the screen."))
        left.addLayout(cards)

        box = QGroupBox("Look")
        form = QFormLayout(box)
        self.res = QComboBox()
        for label, w, h in RESOLUTIONS:
            self.res.addItem(label, (w, h))
        form.addRow("Size", self.res)
        self.fps = QComboBox()
        for f in (24, 25, 30, 60):
            self.fps.addItem(f"{f} fps", f)
        form.addRow("Frame rate", self.fps)
        crow = QHBoxLayout()
        self.bg = ColorButton("#101014", self._changed)
        self.hl = ColorButton("#ffcc33", self._changed)
        crow.addWidget(QLabel("Background"))
        crow.addWidget(self.bg)
        crow.addSpacing(12)
        crow.addWidget(QLabel("Highlight"))
        crow.addWidget(self.hl)
        crow.addStretch(1)
        form.addRow("Colours", crow)
        self.labels = QCheckBox("Show note / drum names on the tiles")
        form.addRow("", self.labels)
        self.idle = QComboBox()
        self.idle.addItem("Dimmed still picture", "dim")
        self.idle.addItem("Hidden (empty tile)", "hide")
        form.addRow("Waiting tiles (grid)", self.idle)
        self.only_used = QCheckBox("Only include clips the song uses (grid)")
        form.addRow("", self.only_used)
        self.smooth = QCheckBox("Smooth slow-motion when a clip is stretched")
        form.addRow("", self.smooth)
        self.vel = QCheckBox("Softer notes play quieter (uses note velocity)")
        form.addRow("", self.vel)
        left.addWidget(box)

        obox = QGroupBox("Save to")
        ol = QHBoxLayout(obox)
        self.out = QLineEdit()
        self.out.setPlaceholderText("Choose where to save the video…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        ol.addWidget(self.out, 1)
        ol.addWidget(browse)
        left.addWidget(obox)

        rrow = QHBoxLayout()
        self.render_btn = QPushButton("🎬  Make my video")
        self.render_btn.setProperty("primary", True)
        self.render_btn.clicked.connect(self.render)
        rrow.addWidget(self.render_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel)
        rrow.addWidget(self.cancel_btn)
        rrow.addStretch(1)
        left.addLayout(rrow)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setVisible(False)
        left.addWidget(self.prog)
        self.prog_lab = hint("")
        left.addWidget(self.prog_lab)
        done_row = QHBoxLayout()
        self.open_btn = QPushButton("▶  Open video")
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output)))
        self.folder_btn = QPushButton("📁  Show in folder")
        self.folder_btn.clicked.connect(self.show_in_folder)
        for b in (self.open_btn, self.folder_btn):
            b.setVisible(False)
            done_row.addWidget(b)
        done_row.addStretch(1)
        left.addLayout(done_row)
        left.addStretch(1)
        root.addLayout(left, 5)

        # preview
        pbox = QGroupBox("Preview")
        pl = QVBoxLayout(pbox)
        self.preview = QLabel("Load clips and a song, then click “Update preview”.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(300, 180)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet("background:#0b0b0d; border-radius:8px; color:#888;")
        pl.addWidget(self.preview, 1)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Time"))
        self.t_slider = QSlider(Qt.Horizontal)
        self.t_slider.setRange(0, 1000)
        self.t_slider.sliderReleased.connect(self.update_preview)
        trow.addWidget(self.t_slider, 1)
        self.t_lab = QLabel("0:00")
        trow.addWidget(self.t_lab)
        upd = QPushButton("Update preview")
        upd.clicked.connect(self.update_preview)
        trow.addWidget(upd)
        pl.addLayout(trow)
        self.summary = hint("")
        pl.addWidget(self.summary)
        root.addWidget(pbox, 6)
        self._pix: QPixmap | None = None

        self.r_grid.toggled.connect(self._changed)
        for w in (self.res, self.fps, self.idle):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.labels, self.only_used, self.smooth, self.vel):
            w.toggled.connect(self._changed)
        self.out.textChanged.connect(self._changed)
        self.t_slider.valueChanged.connect(self._t_label)
        for sig in (state.slots_changed, state.song_changed, state.options_changed):
            sig.connect(self.refresh)
        state.project_replaced.connect(self._load)
        self._load()

    # ------------------------------------------------------------------ settings
    def _load(self) -> None:
        r = self.state.project.render
        self._loading = True
        (self.r_grid if r.layout == "grid" else self.r_dyn).setChecked(True)
        i = next((i for i, (_, w, h) in enumerate(RESOLUTIONS) if (w, h) == (r.width, r.height)), 0)
        self.res.setCurrentIndex(i)
        self.fps.setCurrentIndex(max(0, self.fps.findData(r.fps)))
        self.bg.set_color(r.background)
        self.hl.set_color(r.highlight)
        self.labels.setChecked(r.show_labels)
        self.idle.setCurrentIndex(max(0, self.idle.findData(r.idle_mode)))
        self.only_used.setChecked(r.only_used_clips)
        self.smooth.setChecked(r.smooth_slowmo)
        self.vel.setChecked(r.velocity_volume)
        self.out.setText(r.output_path)
        self._loading = False
        self.refresh()

    def _changed(self, *_a) -> None:
        if self._loading:
            return
        r = self.state.project.render
        r.layout = "grid" if self.r_grid.isChecked() else "dynamic"
        r.width, r.height = self.res.currentData()
        r.fps = self.fps.currentData()
        r.background, r.highlight = self.bg.color, self.hl.color
        r.show_labels = self.labels.isChecked()
        r.idle_mode = self.idle.currentData()
        r.only_used_clips = self.only_used.isChecked()
        r.smooth_slowmo = self.smooth.isChecked()
        r.velocity_volume = self.vel.isChecked()
        r.output_path = self.out.text().strip()
        self.state.dirty = True
        grid = r.layout == "grid"
        self.idle.setEnabled(grid)
        self.only_used.setEnabled(grid)

    def _t_label(self, v: int) -> None:
        song = self.state.song
        t = (song.duration * 100 / max(1, self.state.project.song.tempo_pct)) * v / 1000 if song else 0
        self.t_lab.setText(f"{int(t) // 60}:{int(t) % 60:02d}")

    def refresh(self) -> None:
        plan = self.state.plan()
        ok = bool(plan and plan.instances)
        self.render_btn.setEnabled(ok and self.task is None)
        if not self.state.song:
            self.summary.setText("No song loaded yet (Step 2).")
        elif not ok:
            self.summary.setText("None of the song’s notes have clips yet (Step 1).")
        else:
            used = len({i.slot for i in plan.instances})
            dur = max(i.start + i.duration for i in plan.instances)
            n_notes, n_drums = len(plan.note_instances), len(plan.drum_instances)
            txt = f"{n_notes} notes"
            if n_drums:
                txt += f" · {n_drums} drum hit{'s' if n_drums != 1 else ''}"
            txt += f" · {used} clips · {int(dur) // 60}:{int(dur) % 60:02d} long"
            if plan.total_missing:
                n = plan.total_missing
                txt += f" · ⚠ {n} note{'s' if n != 1 else ''} {'have' if n != 1 else 'has'} no clip and will be silent"
            self.summary.setText(txt)

    def browse(self) -> None:
        start = self.out.text() or os.path.join(os.path.expanduser("~"), "Videos", "my_song.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "Save video as", start, "MP4 video (*.mp4)")
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.out.setText(path)

    # ------------------------------------------------------------------ preview
    def update_preview(self) -> None:
        if not self.state.song or not (self.state.project.slots or self.state.project.drum_slots):
            return
        song = self.state.song
        t = (song.duration * 100 / max(1, self.state.project.song.tempo_pct)) * self.t_slider.value() / 1000
        self.preview.setText("Making preview…")

        def done(img):
            rgb = img[:, :, ::-1].copy()
            self._pix = to_pixmap(rgb)
            self._show_pix()

        def fail(e, _tb):
            self.preview.setText(str(e))

        run_task(preview_frame, copy.deepcopy(self.state.project), song, t, on_done=done, on_error=fail)

    def _show_pix(self) -> None:
        if self._pix:
            self.preview.setPixmap(self._pix.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._show_pix()

    # ------------------------------------------------------------------ render
    def render(self) -> None:
        out = self.out.text().strip()
        if not out:
            self.browse()
            out = self.out.text().strip()
            if not out:
                return
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
            self.out.setText(out)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        plan = self.state.plan()
        if plan and plan.total_missing:
            if QMessageBox.question(self, "Some notes have no clip",
                                    f"{plan.total_missing} notes in the song have no clip and will be silent.\n\n"
                                    "Make the video anyway?") != QMessageBox.Yes:
                return
        self.render_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.prog.setVisible(True)
        self.prog.setValue(0)
        for b in (self.open_btn, self.folder_btn):
            b.setVisible(False)

        def prog(f, m):
            self.prog.setValue(int(f * 1000))
            self.prog_lab.setText(m)

        def done(path):
            self._end()
            self.last_output = path
            self.prog.setValue(1000)
            self.prog_lab.setText(f"Saved: {path}")
            for b in (self.open_btn, self.folder_btn):
                b.setVisible(True)

        def fail(e, tb):
            self._end()
            if isinstance(e, Cancelled):
                self.prog_lab.setText("Cancelled.")
                try:
                    os.remove(out)
                except OSError:
                    pass
            else:
                self.prog_lab.setText("Something went wrong.")
                QMessageBox.critical(self, "Render failed", f"{e}\n\n{tb[-1500:]}")

        self.task = run_task(render_video, copy.deepcopy(self.state.project), self.state.song, out,
                             on_done=done, on_error=fail, on_progress=prog)

    def _end(self) -> None:
        self.task = None
        self.cancel_btn.setVisible(False)
        self.refresh()

    def cancel(self) -> None:
        if self.task:
            self.task.cancel.set()
            self.prog_lab.setText("Cancelling…")

    def show_in_folder(self) -> None:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_output)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.last_output)))
