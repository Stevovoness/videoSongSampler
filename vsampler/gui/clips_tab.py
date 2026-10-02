"""Step 1: assign a video clip to each note (piano keys) and each drum sound (drum pads).

Clicking a key or pad - or pressing its computer key - plays the clip exactly as it will sound in the video.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSlider, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..clipfinder import LONG_VIDEO_SECONDS
from ..clips import SR, analyze_clip, level_gain_db, measure_loudness, trim_thumb_key, video_duration
from ..drums import GM_DRUMS, drum_name, drum_note, drum_short
from ..models import ClipSlot
from ..notes import describe_detected, midi_to_name, pretty_name
from ..render import sources
from . import theme
from .state import AppState, to_pixmap
from .widgets.drum_pads import DrumPads
from .widgets.keyboard import VIDEO_EXT, PianoKeyboard
from .widgets.sampler import SamplePad
from .worker import run_task

VIDEO_FILTER = "Videos (" + " ".join("*" + e for e in VIDEO_EXT) + ");;All files (*)"

# computer keyboard -> semitones above the base C (like most music software)
NOTE_KEYS = [(Qt.Key_A, "A"), (Qt.Key_W, "W"), (Qt.Key_S, "S"), (Qt.Key_E, "E"), (Qt.Key_D, "D"), (Qt.Key_F, "F"),
             (Qt.Key_T, "T"), (Qt.Key_G, "G"), (Qt.Key_Y, "Y"), (Qt.Key_H, "H"), (Qt.Key_U, "U"), (Qt.Key_J, "J"),
             (Qt.Key_K, "K"), (Qt.Key_O, "O"), (Qt.Key_L, "L"), (Qt.Key_P, "P"), (Qt.Key_Semicolon, ";")]
# computer keyboard -> the 16 core drum pads, row by row
PAD_KEYS = [(Qt.Key_1, "1"), (Qt.Key_2, "2"), (Qt.Key_3, "3"), (Qt.Key_4, "4"),
            (Qt.Key_Q, "Q"), (Qt.Key_W, "W"), (Qt.Key_E, "E"), (Qt.Key_R, "R"),
            (Qt.Key_A, "A"), (Qt.Key_S, "S"), (Qt.Key_D, "D"), (Qt.Key_F, "F"),
            (Qt.Key_Z, "Z"), (Qt.Key_X, "X"), (Qt.Key_C, "C"), (Qt.Key_V, "V")]
# empty pads are filled in this order when no song tells us which drum sounds are needed
PAD_FILL_ORDER = [36, 38, 42, 39, 46, 49, 45, 50, 47, 51, 37, 44, 40, 35, 54, 56]


def note_combo(lo: int = 24, hi: int = 108) -> QComboBox:
    c = QComboBox()
    for m in range(lo, hi + 1):
        c.addItem(pretty_name(m), m)
    return c


def drum_combo() -> QComboBox:
    c = QComboBox()
    for n in sorted(GM_DRUMS):
        c.addItem(f"{drum_short(n)}  ({drum_name(n)})", n)
    return c


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{t % 60:05.2f}"


def set_combo_note(c: QComboBox, midi: int) -> None:
    i = c.findData(midi)
    if i >= 0:
        c.setCurrentIndex(i)


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("hint", True)
    lab.setWordWrap(True)
    return lab


HINTS = {
    "notes": "Click a key to hear it exactly as it will sound in the video, and to give it a video. Drop a video "
             "straight onto a key, or use “Add clips” to have each one placed on the note it sings. "
             "Red dots = notes your song needs with no clip · blue = played from a clip an octave away · "
             "amber = borrowed and pitch-shifted.",
    "drums": "Slaps, claps, smacks, knocks… give each drum sound a video. Click a pad to hear it, drop a video "
             "onto a pad, or use “Add clips” to fill the empty pads the song needs. Drum clips play exactly as "
             "recorded (no auto-tune, no stretching). Red dots = the song uses this sound and nothing plays it · "
             "amber = a similar drum clip stands in.",
}


class ClipsTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.mode = "notes"
        self.sel: dict[str, int | None] = {"notes": None, "drums": None}
        self.pending = 0
        self.kb_base = 60                       # computer keyboard 'A' plays this note
        self.take_sel = 0                       # which take of the selected slot the detail panel shows
        self.finders: list = []                 # open clip finder windows
        self.pad = SamplePad(state, self)
        self.pad.failed.connect(lambda msg: self.status.setText(f"Couldn’t play: {msg}"))

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)

        # ---- header
        head = QHBoxLayout()
        title = QLabel("Step 1 · Your sounds")
        title.setProperty("h1", True)
        head.addWidget(title)
        head.addSpacing(16)
        self.b_notes = QPushButton("🎹  Notes")
        self.b_drums = QPushButton("🥁  Drums")
        grp = QButtonGroup(self)
        for i, b in enumerate((self.b_notes, self.b_drums)):
            b.setCheckable(True)
            b.setMinimumWidth(110)
            grp.addButton(b, i)
        self.b_notes.setToolTip("Pitched sounds on a piano keyboard: one clip per note")
        self.b_drums.setToolTip("Percussion (slaps, claps, smacks…) on drum pads: one clip per drum sound")
        self.b_notes.setChecked(True)
        grp.idClicked.connect(lambda i: self.set_mode("drums" if i else "notes"))
        head.addWidget(self.b_notes)
        head.addWidget(self.b_drums)
        head.addSpacing(10)
        self.click_play = QCheckBox("🔊 Click to play")
        self.click_play.setChecked(True)
        self.click_play.setToolTip("Clicking a key or pad plays it exactly as it will sound in the video")
        head.addWidget(self.click_play)
        head.addStretch(1)
        self.status = QLabel("")
        self.status.setProperty("hint", True)
        head.addWidget(self.status)
        self.add_btn = QPushButton()
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self.bulk_add)
        head.addWidget(self.add_btn)
        self.cut_btn = QPushButton("✂  Cut clips from a long video…")
        self.cut_btn.setToolTip("Give the app a longer video: it suggests every note (and hit) in it, "
                                "you adjust them and add them all at once")
        self.cut_btn.clicked.connect(self.cut_long)
        head.addWidget(self.cut_btn)
        root.addLayout(head)
        self.hint = hint("")
        root.addWidget(self.hint)

        # ---- options row
        rng = QHBoxLayout()
        self.note_opts = QWidget()
        nl = QHBoxLayout(self.note_opts)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(QLabel("Keyboard from"))
        self.lo = note_combo(21, 96)
        self.hi = note_combo(33, 108)
        set_combo_note(self.lo, 48)
        set_combo_note(self.hi, 84)
        self.lo.currentIndexChanged.connect(self._range_changed)
        self.hi.currentIndexChanged.connect(self._range_changed)
        nl.addWidget(self.lo)
        nl.addWidget(QLabel("to"))
        nl.addWidget(self.hi)
        fit = QPushButton("Fit to song")
        fit.setToolTip("Show exactly the range of notes the loaded song uses")
        fit.clicked.connect(self.fit_to_song)
        nl.addWidget(fit)
        rng.addWidget(self.note_opts)
        self.drum_opts = QWidget()
        dl = QHBoxLayout(self.drum_opts)
        dl.setContentsMargins(0, 0, 0, 0)
        self.show_all = QCheckBox("Show all drum sounds")
        self.show_all.setToolTip("Show every General MIDI drum sound, not just the main kit and the ones the song uses")
        self.show_all.toggled.connect(lambda on: (self.pads.set_show_all(on), self._update_letters()))
        dl.addWidget(self.show_all)
        rng.addWidget(self.drum_opts)
        rng.addStretch(1)
        self.even = QCheckBox("Even out clip volumes automatically")
        self.even.setToolTip("Measures how loud each clip is and turns quiet ones up / loud ones down "
                             "so every note comes out at about the same volume")
        self.even.setChecked(state.project.render.even_volumes)
        self.even.toggled.connect(self._even_changed)
        rng.addWidget(self.even)
        rng.addSpacing(12)
        self.clear_btn = QPushButton("Remove all clips")
        self.clear_btn.clicked.connect(self.clear_all)
        rng.addWidget(self.clear_btn)
        root.addLayout(rng)

        # ---- keyboard / drum pads
        self.stack = QStackedWidget()
        self.keys = PianoKeyboard(48, 84)
        self.keys.setMinimumHeight(170)
        self.keys.describe = self._describe_key
        self.keys.key_clicked.connect(self._clicked)
        self.keys.files_dropped.connect(lambda m, paths: self.add_video(paths[0], m, drum=False))
        self.pads = DrumPads()
        self.pads.key_clicked.connect(self._clicked)
        self.pads.files_dropped.connect(lambda n, paths: self.add_video(paths[0], n, drum=True))
        self.stack.addWidget(self.keys)
        self.stack.addWidget(self.pads)
        self.stack.setMinimumHeight(200)
        root.addWidget(self.stack)
        self.kb_hint = hint("")
        root.addWidget(self.kb_hint)

        # ---- bottom: list + detail
        split = QSplitter(Qt.Horizontal)
        self.list_box = QGroupBox("Loaded clips")
        ll = QVBoxLayout(self.list_box)
        self.list = QListWidget()
        self.list.setIconSize(QSize(96, 54))
        self.list.currentItemChanged.connect(self._list_pick)
        ll.addWidget(self.list)
        split.addWidget(self.list_box)

        self.detail = QGroupBox("Selected sound")
        self._build_detail()
        split.addWidget(self.detail)
        split.setSizes([360, 640])
        root.addWidget(split, 1)

        state.slots_changed.connect(self.refresh)
        state.slots_changed.connect(self.pad.warm)
        state.song_changed.connect(self.refresh)
        state.options_changed.connect(self.refresh)
        state.project_replaced.connect(self._project_replaced)
        QApplication.instance().installEventFilter(self)
        self.set_mode("notes")

    # ------------------------------------------------------------------ mode
    @property
    def selected(self) -> int | None:
        return self.sel[self.mode]

    @property
    def drum(self) -> bool:
        return self.mode == "drums"

    def _slots(self, drum: bool | None = None) -> dict[int, ClipSlot]:
        drum = self.drum if drum is None else drum
        return self.state.project.drum_slots if drum else self.state.project.slots

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        (self.b_drums if self.drum else self.b_notes).setChecked(True)
        self.stack.setCurrentWidget(self.pads if self.drum else self.keys)
        self.note_opts.setVisible(not self.drum)
        self.drum_opts.setVisible(self.drum)
        self.add_btn.setText("＋ Add drum clips…" if self.drum else "＋ Add clips (auto-detect notes)…")
        self.add_btn.setToolTip("Puts each clip on an empty pad the song needs (you can move it afterwards)"
                                if self.drum else "Listens to each video, works out its note and puts it on that key")
        self.hint.setText(HINTS[mode])
        self.list_box.setTitle("Loaded drum clips" if self.drum else "Loaded note clips")
        self.form.setRowVisible(self.autotune, not self.drum)
        self.form.setRowVisible(self.move_to, not self.drum)
        self.form.setRowVisible(self.move_pad, self.drum)
        self.tuned_btn.setText("♪  Hear it as in the video")
        self._update_letters()
        self.select_key(self.selected, play=False)

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
        self._vstop = QTimer(self)
        self._vstop.setSingleShot(True)
        self._vstop.timeout.connect(self.vplayer.pause)
        vcol.addWidget(self.video, 1)
        prow = QHBoxLayout()
        self.play_btn = QPushButton("▶  Play clip")
        self.play_btn.setToolTip("Plays the original video with its own sound")
        self.play_btn.clicked.connect(self.play_clip)
        self.tuned_btn = QPushButton("♪  Hear it as in the video")
        self.tuned_btn.setToolTip("Plays just the trimmed sound, exactly as the finished video will use it")
        self.tuned_btn.clicked.connect(lambda: self.play_key(self.selected, show_video=True))
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
        self.add_take_btn = QPushButton("＋ Add take…")
        self.add_take_btn.setToolTip("Add another recording of this sound. Takes are used in turn, so the video "
                                     "doesn’t show the same moment every time")
        self.add_take_btn.clicked.connect(self.add_take)
        brow.addWidget(self.choose_btn)
        brow.addWidget(self.add_take_btn)
        brow.addWidget(self.remove_btn)
        col.addLayout(brow)
        krow = QHBoxLayout()
        self.take_prev = QPushButton("◀")
        self.take_next = QPushButton("▶")
        for b in (self.take_prev, self.take_next):
            b.setFixedWidth(34)
        self.take_prev.clicked.connect(lambda: self._step_take(-1))
        self.take_next.clicked.connect(lambda: self._step_take(1))
        self.take_lab = QLabel("")
        self.take_main = QPushButton("Make main take")
        self.take_main.clicked.connect(self.make_main_take)
        self.take_del = QPushButton("Remove take")
        self.take_del.clicked.connect(self.remove_take)
        self.adjust_btn = QPushButton("✂  Adjust in the video…")
        self.adjust_btn.setToolTip("Open this clip in the clip finder to move or resize it within its video")
        self.adjust_btn.clicked.connect(self.adjust_in_finder)
        for w in (self.take_prev, self.take_lab, self.take_next, self.take_main, self.take_del):
            krow.addWidget(w)
        krow.addStretch(1)
        krow.addWidget(self.adjust_btn)
        col.addLayout(krow)
        self._take_widgets = [self.take_prev, self.take_lab, self.take_next, self.take_main, self.take_del]

        self.d_detect = QLabel("")
        self.d_detect.setWordWrap(True)
        col.addWidget(self.d_detect)
        self.d_level = QLabel("")
        self.d_level.setWordWrap(True)
        col.addWidget(self.d_level)

        form = self.form = QFormLayout()
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
        self.move_pad = drum_combo()
        self.move_pad.activated.connect(self._move_selected)
        form.addRow("Move to pad", self.move_pad)
        col.addLayout(form)
        col.addStretch(1)
        lay.addWidget(form_box, 2)
        self._detail_widgets = [self.play_btn, self.remove_btn, self.trim_a, self.trim_b,
                                self.autotune, self.gain, self.move_to, self.move_pad, auto, self.add_take_btn,
                                self.adjust_btn]

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
        proj = self.state.project
        plan = self.state.plan()
        needed = self.state.needed_pitches()
        needed_d = self.state.needed_drums()
        borrowed = set(plan.borrowed) if plan else set()
        octave = set(plan.octave) if plan else set()
        self.keys.set_state(set(proj.slots), needed, borrowed, octave)
        thumbs = {n: self.state.thumbs[trim_thumb_key(s)] for n, s in proj.drum_slots.items()
                  if trim_thumb_key(s) in self.state.thumbs}
        self.keys.takes = {m: s.take_count for m, s in proj.slots.items() if s.take_count > 1}
        self.pads.takes = {n: s.take_count for n, s in proj.drum_slots.items() if s.take_count > 1}
        standin = dict(plan.drum_standin) if plan else {}
        self.pads.set_state(set(proj.drum_slots), needed_d, standin, thumbs)
        self._update_letters()
        # list
        slots = self._slots()
        self.list.blockSignals(True)
        self.list.clear()
        for m in sorted(slots):
            s = slots[m]
            takes = f"  ·  {s.take_count} takes" if s.take_count > 1 else ""
            if self.drum:
                text = f"{drum_short(m)}   (drum {m}){takes}\n{os.path.basename(s.path)}"
            else:
                text = f"{pretty_name(m)}{takes}\n{os.path.basename(s.path)}"
                if s.detected_midi is not None:
                    text += f"\nheard: {describe_detected(s.detected_midi)}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, m)
            pm = self.state.thumbs.get(trim_thumb_key(s))
            if pm:
                item.setIcon(QIcon(pm))
            self.list.addItem(item)
            if m == self.selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        # status
        n, d = len(proj.slots), len(proj.drum_slots)
        msg = f"{n} note clip{'s' if n != 1 else ''} · {d} drum clip{'s' if d != 1 else ''}"
        if self.drum and needed_d:
            missing = len(plan.drum_missing) if plan else 0
            msg += f" · song uses {len(needed_d)} drum sounds"
            if missing:
                msg += f" · {missing} silent"
        elif not self.drum and needed:
            missing = needed - set(proj.slots) - borrowed - octave
            msg += f" · song uses {len(needed)} notes"
            if missing:
                msg += f" · {len(missing)} missing"
        if self.pending:
            msg += f" · analysing {self.pending}…"
        self.status.setText(msg)
        self._fill_detail()

    def _project_replaced(self) -> None:
        self.sel = {"notes": None, "drums": None}
        self.even.blockSignals(True)
        self.even.setChecked(self.state.project.render.even_volumes)
        self.even.blockSignals(False)
        proj = self.state.project
        slots = proj.slots
        if slots:
            set_combo_note(self.lo, max(21, min(min(slots) - 2, 48)))
            set_combo_note(self.hi, min(108, max(max(slots) + 2, 72)))
        for s in list(slots.values()) + list(proj.drum_slots.values()):
            self._load_thumbs(s)
        self.set_mode("drums" if proj.drum_slots and not proj.slots else "notes")
        self.pad.warm()

    def _load_thumbs(self, slot: ClipSlot) -> None:
        """Fetch thumbnails for every take of a slot that doesn't have one yet (in the background)."""
        from ..clips import grab_frame

        for i in range(slot.take_count):
            clip = slot.take(i)
            key = trim_thumb_key(clip)
            if key in self.state.thumbs or not os.path.exists(clip.path):
                continue

            def done(img, key=key):
                pm = to_pixmap(img)
                if pm:
                    self.state.thumbs[key] = pm
                    self.refresh()

            run_task(grab_frame, clip.path, clip.trim_start + 0.15, on_done=done, on_error=lambda *_: None)

    # ------------------------------------------------------------------ computer keyboard
    def _update_letters(self) -> None:
        if self.drum:
            pads = self.pads.pads()[:len(PAD_KEYS)]
            self.pads.set_letters({n: PAD_KEYS[i][1] for i, n in enumerate(pads)})
            self.kb_hint.setText("⌨  Play the pads with your computer keyboard:  1 2 3 4 · Q W E R · A S D F · Z X C V")
        else:
            self.keys.set_letters({self.kb_base + i: lab for i, (_, lab) in enumerate(NOTE_KEYS)})
            top = self.kb_base + len(NOTE_KEYS) - 1
            self.kb_hint.setText(f"⌨  Play with your computer keyboard:  A W S E D F T G Y H U J K O L P ;  "
                                 f"= {midi_to_name(self.kb_base)} to {midi_to_name(top)}   ·   "
                                 "Z / X = octave down / up")

    def _shift_kb_octave(self, d: int) -> None:
        self.kb_base = max(12, min(108 - 12, self.kb_base + 12 * d))
        # make sure the keys being played are on screen
        lo, hi = self.lo.currentData(), self.hi.currentData()
        top = self.kb_base + len(NOTE_KEYS) - 1
        if self.kb_base < lo:
            set_combo_note(self.lo, max(21, self.kb_base))
        if top > hi:
            set_combo_note(self.hi, min(108, top))
        self._update_letters()

    def eventFilter(self, obj, e) -> bool:
        if e.type() != QEvent.KeyPress or not self.isVisible() or not self.window().isActiveWindow():
            return False
        if e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox)) or (isinstance(focus, QComboBox) and focus.isEditable()):
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        key = e.key()
        if self.drum:
            idx = next((i for i, (k, _) in enumerate(PAD_KEYS) if k == key), None)
            if idx is None:
                return False
            if not e.isAutoRepeat():
                pads = self.pads.pads()
                if idx < len(pads):
                    self.play_key(pads[idx])
            return True
        if key in (Qt.Key_Z, Qt.Key_X):
            if not e.isAutoRepeat():
                self._shift_kb_octave(-1 if key == Qt.Key_Z else 1)
            return True
        idx = next((i for i, (k, _) in enumerate(NOTE_KEYS) if k == key), None)
        if idx is None:
            return False
        if not e.isAutoRepeat():
            self.play_key(self.kb_base + idx)
        return True

    # ------------------------------------------------------------------ playing
    def _describe_key(self, m: int) -> str:
        r = self.state.resolve_key(m)
        if r.how == "exact":
            return "clip loaded — click to play"
        if r.how == "octave":
            n = abs(r.slot - m) // 12
            return (f"no clip — plays {midi_to_name(r.slot)} ({n} octave{'s' if n > 1 else ''} "
                    f"{'up' if r.slot > m else 'down'})")
        if r.how == "borrowed":
            return f"no clip — plays {midi_to_name(r.slot)} pitch-shifted {m - r.slot:+d}"
        return "no clip yet — click or drop a video"

    def play_key(self, n: int | None, show_video: bool = False) -> None:
        """Play key / pad n exactly as the video will (including octave jumps and stand-ins)."""
        if n is None:
            return
        if self.drum:
            r = self.state.resolve_pad(n)
            self.pads.flash(n)
        else:
            r = self.state.resolve_key(n)
            self.keys.flash(n)
        if r.slot is None:
            return
        take = self.pad.trigger(r.slot, r.shift)
        if show_video:
            if take is not None and r.how == "exact" and n == self.selected:
                self.take_sel = take
                self._fill_detail()
            self._show_video_hit()

    def _show_video_hit(self) -> None:
        """Play the selected clip's picture (muted - the sound comes from the sample pad) over its trimmed sound."""
        s = self._rec()
        if not s:
            return
        self._load_video(s)
        self.vaudio.setMuted(True)
        self.vplayer.setPosition(int(s.trim_start * 1000))
        self.vplayer.play()
        end = s.trim_end if s.trim_end is not None else s.trim_start + 2.0
        self._vstop.start(int(max(0.05, end - s.trim_start) * 1000))

    # ------------------------------------------------------------------ selection
    def _clicked(self, n: int) -> None:
        self.select_key(n, play=self.click_play.isChecked())

    def select_key(self, m: int | None, play: bool = False) -> None:
        changed = m != self.sel[self.mode]
        self.sel[self.mode] = m
        if changed:
            self.take_sel = 0
        (self.pads if self.drum else self.keys).select(m)
        slot = self._slots().get(m) if m is not None else None
        if changed or not play:
            self.vplayer.stop()
            self._vstop.stop()
            rec = self._rec()
            if rec and os.path.exists(rec.path):
                self.vplayer.setSource(QUrl.fromLocalFile(rec.path))
                self.vplayer.setPosition(int(rec.trim_start * 1000))
                self.vplayer.pause()
            else:
                self.vplayer.setSource(QUrl())
        self.refresh()
        if play:
            self.play_key(m, show_video=slot is not None)

    def _load_video(self, rec) -> None:
        """Point the preview at a take's video (only reloads when the file changes)."""
        url = QUrl.fromLocalFile(rec.path) if os.path.exists(rec.path) else QUrl()
        if self.vplayer.source() != url:
            self.vplayer.setSource(url)

    def _list_pick(self, cur, _prev) -> None:
        if cur is not None:
            self.select_key(cur.data(Qt.UserRole))

    def _fill_detail(self) -> None:
        m = self.selected
        slot = self._slot()
        self.choose_btn.setEnabled(m is not None)
        self.tuned_btn.setEnabled(m is not None)
        for w in self._detail_widgets:
            w.setEnabled(slot is not None)
        if slot is None:
            for w in self._take_widgets:
                w.setVisible(False)
        if m is None:
            self.d_title.setText("Pick a pad" if self.drum else "Pick a key")
            self.d_file.setText("Click a pad above to hear it and choose its video." if self.drum else
                                "Click a key on the keyboard above to hear it and choose the video for that note.")
            self.d_detect.setText("")
            self.d_level.setText("")
            return
        self.d_title.setText(f"{drum_short(m)}  ·  {drum_name(m)}" if self.drum else pretty_name(m))
        if slot is None:
            r = self.state.resolve_pad(m) if self.drum else self.state.resolve_key(m)
            extra = ""
            if r.slot is not None:
                extra = (f"<br>For now {drum_short(drum_note(r.slot))} stands in for it." if self.drum
                         else f"<br>For now: {self._describe_key(m).split('— ')[-1]}.")
            self.d_file.setText(f"No clip for this {'drum sound' if self.drum else 'note'} yet. "
                                f"Click “Choose video…” or drop a video on the {'pad' if self.drum else 'key'}.{extra}")
            self.d_detect.setText("")
            self.d_level.setText("")
            self.choose_btn.setText("Choose video…")
            return
        self.choose_btn.setText("Replace video…")
        self.take_sel %= slot.take_count
        rec = self._rec()
        span = f"{_mmss(rec.trim_start)}–{_mmss(rec.trim_end)}" if rec.trim_end is not None else \
            f"from {_mmss(rec.trim_start)}"
        self.d_file.setText(f"{os.path.basename(rec.path)}   ·   {span}")
        self.d_file.setToolTip(rec.path)
        many = slot.take_count > 1
        for w in self._take_widgets:
            w.setVisible(many)
        self.take_lab.setText(f"Take {self.take_sel + 1} of {slot.take_count}"
                              + ("  (main)" if self.take_sel == 0 else ""))
        self.take_main.setEnabled(self.take_sel > 0)
        if self.drum:
            self.d_detect.setText("Percussion: played exactly as recorded, never auto-tuned or stretched.")
        elif rec.detected_midi is None:
            self.d_detect.setText(f"<span style='color:{theme.WARN}'>Couldn’t hear a clear note in this clip — "
                                  "it will be used as-is.</span>")
        else:
            diff = m - rec.detected_midi
            txt = f"Heard: <b>{describe_detected(rec.detected_midi)}</b>"
            if abs(diff) > 1.0:
                txt += (f"<br><span style='color:{theme.WARN}'>That’s {abs(diff):.1f} semitones away from "
                        f"{midi_to_name(m)}. Auto-tune only fixes small differences — is this the right key?</span>")
            elif slot.autotune and abs(diff) > 0.005:
                txt += f" → tuned {'up' if diff > 0 else 'down'} {abs(diff) * 100:.0f}¢"
            self.d_detect.setText(txt)
        self.d_level.setText(self._level_text(rec))
        for w, v in ((self.trim_a, rec.trim_start), (self.trim_b, rec.trim_end or 0.0)):
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
        set_combo_note(self.move_pad if self.drum else self.move_to, m)

    def _level_text(self, slot) -> str:
        if slot.loudness_db is None:
            return ""
        txt = f"Loudness: <b>{slot.loudness_db:.0f} dB</b>"
        if self.state.project.render.even_volumes:
            g = level_gain_db(slot.loudness_db)
            if abs(g) >= 0.5:
                txt += f" → evened out {'louder' if g > 0 else 'quieter'} by {abs(g):.0f} dB"
            else:
                txt += " → already at the standard level"
        return txt

    def _even_changed(self, on: bool) -> None:
        self.state.project.render.even_volumes = on
        self.state.touch_slots()

    def _update_loudness(self, s) -> None:
        audio = sources.get_audio(s.path)
        end = s.trim_end if s.trim_end is not None else audio.shape[1] / SR
        s.loudness_db = measure_loudness(audio[:, int(s.trim_start * SR): int(end * SR)])

    # ------------------------------------------------------------------ editing
    def _slot(self) -> ClipSlot | None:
        return self._slots().get(self.selected) if self.selected is not None else None

    def _rec(self):
        """The recording the detail panel shows: the slot itself (take 0) or one of its extra takes."""
        s = self._slot()
        if s is None:
            return None
        i = self.take_sel % s.take_count
        return s if i == 0 else s.extra_takes[i - 1]

    def _trim_changed(self) -> None:
        s = self._rec()
        if s:
            a, b = self.trim_a.value(), self.trim_b.value()
            if b <= a + 0.02:
                return
            s.trim_start, s.trim_end = a, b
            self._update_loudness(s)
            self.d_level.setText(self._level_text(s))
            self.state.dirty = True

    def auto_trim_selected(self) -> None:
        s = self._rec()
        if not s:
            return
        from ..clips import auto_trim
        a, b = auto_trim(sources.get_audio(s.path))
        s.trim_start, s.trim_end = a, b
        self._update_loudness(s)
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
        combo = self.move_pad if self.drum else self.move_to
        s, m, new = self._slot(), self.selected, combo.currentData()
        if not s or new == m:
            return
        slots = self._slots()
        name = drum_short(new) if self.drum else pretty_name(new)
        if new in slots and QMessageBox.question(
                self, "Replace?", f"{name} already has a clip. Swap them?") != QMessageBox.Yes:
            set_combo_note(combo, m)
            return
        other = slots.get(new)
        slots[new] = s
        if other:
            slots[m] = other
        else:
            del slots[m]
        self.sel[self.mode] = new
        (self.pads if self.drum else self.keys).select(new)
        self.state.touch_slots()

    def remove_selected(self) -> None:
        slots = self._slots()
        if self.selected in slots:
            del slots[self.selected]
            self.vplayer.setSource(QUrl())
            self.state.touch_slots()

    def clear_all(self) -> None:
        slots = self._slots()
        what = "drum pads" if self.drum else "keyboard"
        if slots and QMessageBox.question(
                self, "Remove all clips", f"Remove every clip from the {what}?") == QMessageBox.Yes:
            slots.clear()
            self.select_key(None)
            self.state.touch_slots()

    # ------------------------------------------------------------------ playback
    def play_clip(self) -> None:
        s = self._rec()
        if not s:
            return
        self._load_video(s)
        self._vstop.stop()
        if self.vplayer.playbackState() == QMediaPlayer.PlayingState:
            self.vplayer.pause()
            return
        self.vaudio.setMuted(False)
        self.vplayer.setPosition(int(s.trim_start * 1000))
        self.vplayer.play()

    # ------------------------------------------------------------------ adding clips
    def choose_for_selected(self) -> None:
        if self.selected is None:
            return
        name = drum_name(self.selected) if self.drum else pretty_name(self.selected)
        path, _ = QFileDialog.getOpenFileName(self, f"Video for {name}", "", VIDEO_FILTER)
        if path:
            self.add_video(path, self.selected)

    def add_take(self) -> None:
        if self.selected is None or self._slot() is None:
            return
        name = drum_name(self.selected) if self.drum else pretty_name(self.selected)
        path, _ = QFileDialog.getOpenFileName(self, f"Another take for {name}", "", VIDEO_FILTER)
        if path:
            self.add_video(path, self.selected, add_take=True)

    def add_video(self, path: str, n: int | None, drum: bool | None = None, add_take: bool = False) -> None:
        """Use a video for key / pad n (None = work it out). Long videos open the clip finder instead."""
        drum = self.drum if drum is None else drum
        try:
            long = video_duration(path) > LONG_VIDEO_SECONDS
        except Exception:  # noqa: BLE001 - let the normal analysis report unreadable files
            long = False
        if long:
            self.open_finder(path, target=n, drum=drum)
        else:
            self.assign_file(n, path, drum=drum, add_take=add_take)

    def cut_long(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Cut clips from a long video", "", VIDEO_FILTER)
        if path:
            target = self.selected if self.selected is not None and self._slot() is None else None
            self.open_finder(path, target=target)

    def open_finder(self, path: str, target: int | None = None, drum: bool | None = None,
                    edit: tuple[bool, int, int] | None = None) -> None:
        from .clip_finder import ClipFinderDialog
        drum = self.drum if drum is None else drum
        dlg = ClipFinderDialog(self.state, self.pad, path, "drums" if drum else "notes", target, self._free_pad,
                               edit, on_added=self._finder_added, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        self.finders.append(dlg)
        dlg.destroyed.connect(lambda *_: self.finders.remove(dlg) if dlg in self.finders else None)
        dlg.show()

    def adjust_in_finder(self) -> None:
        rec = self._rec()
        if rec is not None:
            self.open_finder(rec.path, drum=self.drum, edit=(self.drum, self.selected, self.take_sel))

    def _finder_added(self, slots: list[ClipSlot]) -> None:
        for s in slots:
            self._load_thumbs(s)
        self.refresh()

    def _step_take(self, d: int) -> None:
        s = self._slot()
        if s:
            self.take_sel = (self.take_sel + d) % s.take_count
            rec = self._rec()
            self._load_video(rec)
            self.vplayer.setPosition(int(rec.trim_start * 1000))
            self.vplayer.pause()
            self._fill_detail()

    def make_main_take(self) -> None:
        s, k = self._slot(), self.take_sel
        if not s or k == 0:
            return
        main = s.as_take()
        s.set_take(0, s.extra_takes[k - 1])
        s.extra_takes[k - 1] = main
        self.take_sel = 0
        self.state.touch_slots()

    def remove_take(self) -> None:
        s, k = self._slot(), self.take_sel
        if not s or s.take_count < 2:
            return
        if k == 0:
            s.set_take(0, s.extra_takes.pop(0))
        else:
            s.extra_takes.pop(k - 1)
        self.take_sel = 0
        self.state.touch_slots()

    def _free_pad(self) -> int | None:
        """The next empty pad: drum sounds the song uses (most used first), then the main kit."""
        drums = self.state.project.drum_slots
        counts: dict[int, int] = {}
        for e in self.state.events():
            if e.drum:
                counts[e.pitch] = counts.get(e.pitch, 0) + 1
        for n in sorted(counts, key=lambda n: -counts[n]) + PAD_FILL_ORDER:
            if n not in drums:
                return n
        return next((n for n in sorted(GM_DRUMS) if n not in drums), None)

    def assign_file(self, n: int | None, path: str, drum: bool | None = None, add_take: bool = False) -> None:
        """Analyse a clip and put it on note / pad `n`, or (n=None) on its detected note / the next free pad.

        add_take: add it as another take of n's clip instead of replacing it."""
        drum = self.drum if drum is None else drum
        self.pending += 1
        self.refresh()

        def job(p):
            analysis, audio = analyze_clip(p, detect=not drum)
            sources.store_audio(p, audio)
            return analysis

        def done(a):
            self.pending -= 1
            as_drum, target = drum, n
            if target is None and drum:
                target = self._free_pad()
            elif target is None:
                if a.detected_midi is None:
                    as_drum, target = self._ask_note(path, "I couldn’t hear a clear note in this clip.")
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
                            as_drum, target = self._ask_note(path, "Which note should this clip play?", target)
                            if target is None:
                                self.refresh()
                                return
            if target is None:
                self.refresh()
                return
            sources.forget_frames(path)
            slot = ClipSlot(path, a.trim_start, a.trim_end, None if as_drum else a.detected_midi,
                            autotune=not as_drum, loudness_db=a.loudness_db)
            existing = self._slots(as_drum).get(target)
            if add_take and existing is not None:
                existing.extra_takes.append(slot.as_take())
            else:
                self._slots(as_drum)[target] = slot
            pm = to_pixmap(a.thumbnail)
            if pm:
                self.state.thumbs[trim_thumb_key(slot)] = pm
            self.state.confidence[path] = a.confidence
            if not as_drum:
                lo, hi = self.lo.currentData(), self.hi.currentData()
                if target < lo:
                    set_combo_note(self.lo, target)
                if target > hi:
                    set_combo_note(self.hi, target)
            if as_drum != self.drum:
                self.set_mode("drums" if as_drum else "notes")
            self.select_key(target)
            if add_take and existing is not None:
                self.take_sel = existing.take_count - 1
            self.state.touch_slots()

        def fail(e, _tb):
            self.pending -= 1
            self.refresh()
            QMessageBox.warning(self, "Couldn’t use this clip", f"{os.path.basename(path)}:\n{e}")

        run_task(job, path, on_done=done, on_error=fail)

    def _ask_note(self, path: str, why: str, default: int = 60) -> tuple[bool, int | None]:
        """Ask which note a clip is - or whether it's a drum sound. Returns (is_drum, note)."""
        box = QMessageBox(self)
        box.setWindowTitle("Which note?")
        box.setText(f"<b>{os.path.basename(path)}</b><br><br>{why}<br><br>"
                    "Is it a note for the keyboard, or a percussive sound (slap, clap, knock…) for the drum pads?")
        note_b = box.addButton("Choose a note…", QMessageBox.AcceptRole)
        drum_b = box.addButton("🥁  Use as a drum sound", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is drum_b:
            return True, self._free_pad()
        if box.clickedButton() is not note_b:
            return False, None
        items = [pretty_name(m) for m in range(24, 109)]
        item, ok = QInputDialog.getItem(self, "Which note?", f"{os.path.basename(path)}\n\nWhich note is it?",
                                        items, default - 24, False)
        return False, (items.index(item) + 24) if ok else None

    def bulk_add(self) -> None:
        title = "Add drum clips (slaps, claps, smacks…)" if self.drum else "Add note clips"
        paths, _ = QFileDialog.getOpenFileNames(self, title, "", VIDEO_FILTER)
        for p in paths:
            self.add_video(p, None)
