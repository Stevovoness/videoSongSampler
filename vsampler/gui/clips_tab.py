"""Step 1: assign a video clip to each note."""
from __future__ import annotations

import os
import tempfile

import soundfile as sf
from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSlider, QSplitter, QVBoxLayout, QWidget)

from .. import audio_dsp
from ..clips import SR, analyze_clip
from ..models import ClipSlot
from ..notes import describe_detected, midi_to_name, pretty_name
from ..render import sources
from . import theme
from .state import AppState, to_pixmap
from .widgets.keyboard import VIDEO_EXT, PianoKeyboard
from .widgets.player import AudioPlayer
from .worker import run_task

VIDEO_FILTER = "Videos (" + " ".join("*" + e for e in VIDEO_EXT) + ");;All files (*)"


def note_combo(lo: int = 24, hi: int = 108) -> QComboBox:
    c = QComboBox()
    for m in range(lo, hi + 1):
        c.addItem(pretty_name(m), m)
    return c


def set_combo_note(c: QComboBox, midi: int) -> None:
    i = c.findData(midi)
    if i >= 0:
        c.setCurrentIndex(i)


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("hint", True)
    lab.setWordWrap(True)
    return lab


class ClipsTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.selected: int | None = None
        self.pending = 0
        self.audio = AudioPlayer(self)
        self._tmp_wav = os.path.join(tempfile.gettempdir(), "vs_note_preview.wav")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)

        # ---- header
        head = QHBoxLayout()
        title = QLabel("Step 1 · Your sounds")
        title.setProperty("h1", True)
        head.addWidget(title)
        head.addStretch(1)
        self.status = QLabel("")
        self.status.setProperty("hint", True)
        head.addWidget(self.status)
        add = QPushButton("＋ Add clips (auto-detect notes)…")
        add.setProperty("primary", True)
        add.clicked.connect(self.bulk_add)
        head.addWidget(add)
        root.addLayout(head)
        root.addWidget(hint("Click a key to give it a video, or drag a video file straight onto a key. "
                            "“Add clips” listens to each video, works out which note it is, and puts it on the right key. "
                            "Red dots = notes your song needs that have no clip yet; amber dots = borrowed (pitch-shifted) from a nearby clip."))

        # ---- range + keyboard
        rng = QHBoxLayout()
        rng.addWidget(QLabel("Keyboard from"))
        self.lo = note_combo(21, 96)
        self.hi = note_combo(33, 108)
        set_combo_note(self.lo, 48)
        set_combo_note(self.hi, 84)
        self.lo.currentIndexChanged.connect(self._range_changed)
        self.hi.currentIndexChanged.connect(self._range_changed)
        rng.addWidget(self.lo)
        rng.addWidget(QLabel("to"))
        rng.addWidget(self.hi)
        fit = QPushButton("Fit to song")
        fit.setToolTip("Show exactly the range of notes the loaded song uses")
        fit.clicked.connect(self.fit_to_song)
        rng.addWidget(fit)
        rng.addStretch(1)
        clear = QPushButton("Remove all clips")
        clear.clicked.connect(self.clear_all)
        rng.addWidget(clear)
        root.addLayout(rng)

        self.keys = PianoKeyboard(48, 84)
        self.keys.setMinimumHeight(170)
        self.keys.key_clicked.connect(self.select_key)
        self.keys.files_dropped.connect(lambda m, paths: self.assign_file(m, paths[0]))
        root.addWidget(self.keys)

        # ---- bottom: list + detail
        split = QSplitter(Qt.Horizontal)
        left = QGroupBox("Loaded clips")
        ll = QVBoxLayout(left)
        self.list = QListWidget()
        self.list.setIconSize(QSize(96, 54))
        self.list.currentItemChanged.connect(self._list_pick)
        ll.addWidget(self.list)
        split.addWidget(left)

        self.detail = QGroupBox("Selected note")
        self._build_detail()
        split.addWidget(self.detail)
        split.setSizes([360, 640])
        root.addWidget(split, 1)

        state.slots_changed.connect(self.refresh)
        state.song_changed.connect(self.refresh)
        state.options_changed.connect(self.refresh)
        state.project_replaced.connect(self._project_replaced)
        self.refresh()
        self.select_key(None)

    # ------------------------------------------------------------------ detail panel
    def _build_detail(self) -> None:
        lay = QHBoxLayout(self.detail)
        # video preview
        vcol = QVBoxLayout()
        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 200)
        self.video.setStyleSheet("background:#000; border-radius:8px;")
        self.vplayer = QMediaPlayer(self)
        self.vaudio = QAudioOutput(self)
        self.vplayer.setAudioOutput(self.vaudio)
        self.vplayer.setVideoOutput(self.video)
        vcol.addWidget(self.video, 1)
        prow = QHBoxLayout()
        self.play_btn = QPushButton("▶  Play clip")
        self.play_btn.clicked.connect(self.play_clip)
        self.tuned_btn = QPushButton("♪  Hear tuned note")
        self.tuned_btn.setToolTip("Plays just the trimmed sound, with auto-tune applied")
        self.tuned_btn.clicked.connect(self.play_tuned)
        prow.addWidget(self.play_btn)
        prow.addWidget(self.tuned_btn)
        vcol.addLayout(prow)
        lay.addLayout(vcol, 3)

        # settings
        form_box = QWidget()
        col = QVBoxLayout(form_box)
        col.setContentsMargins(6, 0, 0, 0)
        self.d_title = QLabel("—")
        self.d_title.setProperty("h1", True)
        col.addWidget(self.d_title)
        self.d_file = hint("")
        col.addWidget(self.d_file)
        brow = QHBoxLayout()
        self.choose_btn = QPushButton("Choose video…")
        self.choose_btn.clicked.connect(self.choose_for_selected)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        brow.addWidget(self.choose_btn)
        brow.addWidget(self.remove_btn)
        col.addLayout(brow)

        self.d_detect = QLabel("")
        self.d_detect.setWordWrap(True)
        col.addWidget(self.d_detect)

        form = QFormLayout()
        self.trim_a = QDoubleSpinBox()
        self.trim_b = QDoubleSpinBox()
        for sp in (self.trim_a, self.trim_b):
            sp.setDecimals(2)
            sp.setSingleStep(0.02)
            sp.setSuffix(" s")
            sp.setRange(0, 3600)
            sp.valueChanged.connect(self._trim_changed)
        trow = QHBoxLayout()
        trow.addWidget(self.trim_a)
        trow.addWidget(QLabel("to"))
        trow.addWidget(self.trim_b)
        auto = QPushButton("Auto")
        auto.setToolTip("Cut away the silence before and after the sound")
        auto.clicked.connect(self.auto_trim_selected)
        trow.addWidget(auto)
        form.addRow("Sound starts / ends", trow)
        self.autotune = QCheckBox("Auto-tune to exactly this note")
        self.autotune.toggled.connect(self._autotune_changed)
        form.addRow("", self.autotune)
        self.gain = QSlider(Qt.Horizontal)
        self.gain.setRange(-24, 12)
        self.gain.valueChanged.connect(self._gain_changed)
        self.gain_lab = QLabel("0 dB")
        grow = QHBoxLayout()
        grow.addWidget(self.gain, 1)
        grow.addWidget(self.gain_lab)
        form.addRow("Volume", grow)
        self.move_to = note_combo()
        self.move_to.activated.connect(self._move_selected)
        form.addRow("Move to note", self.move_to)
        col.addLayout(form)
        col.addStretch(1)
        lay.addWidget(form_box, 2)
        self._detail_widgets = [self.play_btn, self.tuned_btn, self.remove_btn, self.trim_a, self.trim_b,
                                self.autotune, self.gain, self.move_to, auto]

    # ------------------------------------------------------------------ refresh
    def _range_changed(self) -> None:
        lo, hi = self.lo.currentData(), self.hi.currentData()
        if hi - lo < 11:
            hi = lo + 11
        self.keys.set_range(lo, hi)

    def fit_to_song(self) -> None:
        notes = self.state.needed_pitches() | set(self.state.project.slots)
        if not notes:
            return
        set_combo_note(self.lo, max(21, min(notes) - 2))
        set_combo_note(self.hi, min(108, max(notes) + 2))

    def refresh(self) -> None:
        slots = self.state.project.slots
        plan = self.state.plan()
        needed = self.state.needed_pitches()
        borrowed = set(plan.borrowed) if plan else set()
        self.keys.set_state(set(slots), needed, borrowed)
        # list
        self.list.blockSignals(True)
        self.list.clear()
        for m in sorted(slots):
            s = slots[m]
            text = f"{pretty_name(m)}\n{os.path.basename(s.path)}"
            if s.detected_midi is not None:
                text += f"\nheard: {describe_detected(s.detected_midi)}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, m)
            pm = self.state.thumbs.get(s.path)
            if pm:
                item.setIcon(QIcon(pm))
            self.list.addItem(item)
            if m == self.selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        missing = needed - set(slots) - borrowed
        msg = f"{len(slots)} clip{'s' if len(slots) != 1 else ''} loaded"
        if needed:
            msg += f" · song uses {len(needed)} notes"
            if missing:
                msg += f" · {len(missing)} missing"
        if self.pending:
            msg += f" · analysing {self.pending}…"
        self.status.setText(msg)
        self._fill_detail()

    def _project_replaced(self) -> None:
        self.selected = None
        slots = self.state.project.slots
        if slots:
            set_combo_note(self.lo, max(21, min(min(slots) - 2, 48)))
            set_combo_note(self.hi, min(108, max(max(slots) + 2, 72)))
        for s in slots.values():
            if s.path not in self.state.thumbs and os.path.exists(s.path):
                self._load_thumb(s.path, s.trim_start)
        self.refresh()

    def _load_thumb(self, path: str, t: float) -> None:
        from ..clips import grab_frame

        def done(img):
            pm = to_pixmap(img)
            if pm:
                self.state.thumbs[path] = pm
                self.refresh()

        run_task(grab_frame, path, t + 0.15, on_done=done, on_error=lambda *_: None)

    # ------------------------------------------------------------------ selection
    def select_key(self, m: int | None) -> None:
        self.selected = m
        self.keys.select(m)
        self.vplayer.stop()
        slot = self.state.project.slots.get(m) if m is not None else None
        if slot and os.path.exists(slot.path):
            self.vplayer.setSource(QUrl.fromLocalFile(slot.path))
            self.vplayer.setPosition(int(slot.trim_start * 1000))
            self.vplayer.pause()
        else:
            self.vplayer.setSource(QUrl())
        self.refresh()

    def _list_pick(self, cur, _prev) -> None:
        if cur is not None:
            self.select_key(cur.data(Qt.UserRole))

    def _fill_detail(self) -> None:
        m = self.selected
        slot = self.state.project.slots.get(m) if m is not None else None
        self.choose_btn.setEnabled(m is not None)
        for w in self._detail_widgets:
            w.setEnabled(slot is not None)
        if m is None:
            self.d_title.setText("Pick a key")
            self.d_file.setText("Click a key on the keyboard above to choose the video for that note.")
            self.d_detect.setText("")
            return
        self.d_title.setText(pretty_name(m))
        if slot is None:
            self.d_file.setText("No clip for this note yet. Click “Choose video…” or drop a video on the key.")
            self.d_detect.setText("")
            self.choose_btn.setText("Choose video…")
            return
        self.choose_btn.setText("Replace video…")
        self.d_file.setText(slot.path)
        if slot.detected_midi is None:
            self.d_detect.setText(f"<span style='color:{theme.WARN}'>Couldn’t hear a clear note in this clip — "
                                  "it will be used as-is.</span>")
        else:
            diff = m - slot.detected_midi
            txt = f"Heard: <b>{describe_detected(slot.detected_midi)}</b>"
            if abs(diff) > 1.0:
                txt += (f"<br><span style='color:{theme.WARN}'>That’s {abs(diff):.1f} semitones away from "
                        f"{midi_to_name(m)}. Auto-tune only fixes small differences — is this the right key?</span>")
            elif slot.autotune and abs(diff) > 0.005:
                txt += f" → tuned {'up' if diff > 0 else 'down'} {abs(diff) * 100:.0f}¢"
            self.d_detect.setText(txt)
        for w, v in ((self.trim_a, slot.trim_start), (self.trim_b, slot.trim_end or 0.0)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.autotune.blockSignals(True)
        self.autotune.setChecked(slot.autotune)
        self.autotune.blockSignals(False)
        self.gain.blockSignals(True)
        self.gain.setValue(int(slot.gain_db))
        self.gain.blockSignals(False)
        self.gain_lab.setText(f"{slot.gain_db:+.0f} dB")
        set_combo_note(self.move_to, m)

    # ------------------------------------------------------------------ editing
    def _slot(self) -> ClipSlot | None:
        return self.state.project.slots.get(self.selected) if self.selected is not None else None

    def _trim_changed(self) -> None:
        s = self._slot()
        if s:
            a, b = self.trim_a.value(), self.trim_b.value()
            if b <= a + 0.02:
                return
            s.trim_start, s.trim_end = a, b
            self.state.dirty = True

    def auto_trim_selected(self) -> None:
        s = self._slot()
        if not s:
            return
        from ..clips import auto_trim
        a, b = auto_trim(sources.get_audio(s.path))
        s.trim_start, s.trim_end = a, b
        self.state.touch_slots()

    def _autotune_changed(self, on: bool) -> None:
        s = self._slot()
        if s:
            s.autotune = on
            self.state.touch_slots()

    def _gain_changed(self, v: int) -> None:
        s = self._slot()
        if s:
            s.gain_db = float(v)
            self.gain_lab.setText(f"{v:+d} dB")
            self.state.dirty = True

    def _move_selected(self) -> None:
        s, m, new = self._slot(), self.selected, self.move_to.currentData()
        if not s or new == m:
            return
        slots = self.state.project.slots
        if new in slots and QMessageBox.question(
                self, "Replace?", f"{pretty_name(new)} already has a clip. Swap them?") != QMessageBox.Yes:
            set_combo_note(self.move_to, m)
            return
        other = slots.get(new)
        slots[new] = s
        if other:
            slots[m] = other
        else:
            del slots[m]
        self.selected = new
        self.keys.select(new)
        self.state.touch_slots()

    def remove_selected(self) -> None:
        if self.selected in self.state.project.slots:
            del self.state.project.slots[self.selected]
            self.vplayer.setSource(QUrl())
            self.state.touch_slots()

    def clear_all(self) -> None:
        if self.state.project.slots and QMessageBox.question(
                self, "Remove all clips", "Remove every clip from the keyboard?") == QMessageBox.Yes:
            self.state.project.slots.clear()
            self.select_key(None)
            self.state.touch_slots()

    # ------------------------------------------------------------------ playback
    def play_clip(self) -> None:
        s = self._slot()
        if not s:
            return
        if self.vplayer.playbackState() == QMediaPlayer.PlayingState:
            self.vplayer.pause()
            return
        self.vplayer.setPosition(int(s.trim_start * 1000))
        self.vplayer.play()

    def play_tuned(self) -> None:
        s, m = self._slot(), self.selected
        if not s:
            return
        try:
            src = sources.get_source(s)
            y = audio_dsp.render_note(src.key, src.audio, SR, src.length, s.autotune_shift(m))
            sf.write(self._tmp_wav, y.T, SR)
            self.audio.play(self._tmp_wav)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Preview failed", str(e))

    # ------------------------------------------------------------------ adding clips
    def choose_for_selected(self) -> None:
        if self.selected is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, f"Video for {pretty_name(self.selected)}", "", VIDEO_FILTER)
        if path:
            self.assign_file(self.selected, path)

    def assign_file(self, midi: int | None, path: str) -> None:
        """Analyse a clip; put it on `midi`, or on its detected note if midi is None."""
        self.pending += 1
        self.refresh()

        def job(p):
            analysis, audio = analyze_clip(p)
            sources.store_audio(p, audio)
            return analysis

        def done(a):
            self.pending -= 1
            target = midi
            if target is None:
                if a.detected_midi is None:
                    target = self._ask_note(path, "I couldn’t hear a clear note in this clip.")
                    if target is None:
                        self.refresh()
                        return
                else:
                    target = int(round(a.detected_midi))
                    if target in self.state.project.slots and self.state.project.slots[target].path != path:
                        ans = QMessageBox.question(
                            self, "Note already has a clip",
                            f"“{os.path.basename(path)}” sounds like {pretty_name(target)}, which already has a clip.\n\n"
                            "Yes = replace it, No = choose a different note for this clip.",
                            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                        if ans == QMessageBox.Cancel:
                            self.refresh()
                            return
                        if ans == QMessageBox.No:
                            target = self._ask_note(path, "Which note should this clip play?", target)
                            if target is None:
                                self.refresh()
                                return
            sources.forget_frames(path)
            self.state.project.slots[target] = ClipSlot(path, a.trim_start, a.trim_end, a.detected_midi)
            pm = to_pixmap(a.thumbnail)
            if pm:
                self.state.thumbs[path] = pm
            self.state.confidence[path] = a.confidence
            lo, hi = self.lo.currentData(), self.hi.currentData()
            if target < lo:
                set_combo_note(self.lo, target)
            if target > hi:
                set_combo_note(self.hi, target)
            self.selected = target
            self.select_key(target)
            self.state.touch_slots()

        def fail(e, _tb):
            self.pending -= 1
            self.refresh()
            QMessageBox.warning(self, "Couldn’t use this clip", f"{os.path.basename(path)}:\n{e}")

        run_task(job, path, on_done=done, on_error=fail)

    def _ask_note(self, path: str, why: str, default: int = 60) -> int | None:
        items = [pretty_name(m) for m in range(24, 109)]
        item, ok = QInputDialog.getItem(self, "Which note?", f"{os.path.basename(path)}\n\n{why}\nWhich note is it?",
                                        items, default - 24, False)
        return (items.index(item) + 24) if ok else None

    def bulk_add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add note clips", "", VIDEO_FILTER)
        for p in paths:
            self.assign_file(None, p)
