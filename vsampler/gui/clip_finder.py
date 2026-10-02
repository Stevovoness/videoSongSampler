"""Clip finder window: suggest every clip point in a long video, let the user adjust them, add them as clips.

All suggested notes and hits appear at once, on a timeline and in a list. Several takes of the same note are
added as takes of one slot, which the video then uses in turn.
"""
from __future__ import annotations

import dataclasses
import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider,
                               QSizePolicy, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import clipfinder
from ..clipfinder import Candidate, FinderCancelled, FinderResult
from ..clips import SR
from ..drums import drum_short
from ..models import ClipSlot, Take
from ..notes import describe_detected, midi_to_name
from ..render import sources
from .screen import preferred_size, scrollable
from .widgets.timeline import Timeline, item_label
from .worker import run_task

_RESULTS: dict[str, FinderResult] = {}     # analysis cache for this session, by video path
NOTE_LO, NOTE_HI = 12, 120


class ClipFinderDialog(QDialog):
    def __init__(self, state, pad, path: str, mode: str = "notes", target: int | None = None,
                 free_pad=None, edit: tuple[bool, int, int] | None = None, on_added=None, parent=None):
        """mode: 'notes' | 'drums'. target: key / pad the finder was opened for.
        edit: (is_drum, key, take) to adjust an existing clip. on_added(list[ClipSlot]) after clips are added."""
        super().__init__(parent)
        from .clips_tab import drum_combo, hint, note_combo, set_combo_note
        self._set_combo = set_combo_note
        self.state, self.pad, self.path, self.mode, self.target = state, pad, path, mode, target
        self.free_pad = free_pad or (lambda: 38)
        self.edit, self.on_added = edit, on_added
        self.result: FinderResult | None = None
        self.audio = None
        self.items: list[Candidate] = []
        self.assign: dict[int, tuple[bool, int | None]] = {}
        self.user_tick: dict[int, bool] = {}
        self.manual: set[int] = set()          # assignment chosen by the user (don't follow the heard pitch)
        self.user_made: set[int] = set()
        self.removed: set[int] = set()
        self.added: set[int] = set()
        self.best: set[int] = set()
        self.filter: object = "all"
        self.sel = -1
        self.edit_idx = -1
        self.hit_pad: int | None = None
        self.task = None
        self._refine_idx = -1

        self.setWindowTitle(f"Clip finder — {os.path.basename(path)}")
        self.setModal(False)
        self.resize(preferred_size(parent, 1240, 860))
        outer = QVBoxLayout(self)
        body = QWidget()
        root = QVBoxLayout(body)          # everything but the buttons; scrolls if the screen is small
        root.setContentsMargins(0, 0, 0, 0)

        # ---- header
        head = QHBoxLayout()
        title = QLabel(f"✂  Cut clips from  <b>{os.path.basename(path)}</b>")
        title.setProperty("h1", True)
        head.addWidget(title)
        head.addStretch(1)
        self.busy = QProgressBar()
        self.busy.setRange(0, 1000)
        self.busy.setFixedWidth(260)
        head.addWidget(self.busy)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        head.addWidget(self.cancel_btn)
        root.addLayout(head)
        root.addWidget(hint(
            "Every clear note and every percussive hit in the video is suggested below. Untick the ones you don’t "
            "want, drag a block’s edges to trim it, drag its middle to move it, or drag on empty space to cut your "
            "own clip. Several takes of the same note are all kept and used in turn, so the video doesn’t repeat "
            "the same moment. Double-click a block (or press Space) to play it."))

        # ---- sensitivity + summary
        srow = QHBoxLayout()
        srow.addWidget(QLabel("More suggestions"))
        self.sens = QSlider(Qt.Horizontal)
        self.sens.setRange(0, 100)
        self.sens.setValue(25)
        self.sens.setFixedWidth(220)
        self.sens.setToolTip("Hide suggestions the app is less sure about")
        self.sens.valueChanged.connect(lambda _v: self.refresh())
        srow.addWidget(self.sens)
        srow.addWidget(QLabel("Only the clearest"))
        srow.addSpacing(20)
        self.summary = QLabel("")
        self.summary.setStyleSheet("font-weight:600;")
        srow.addWidget(self.summary, 1)
        root.addLayout(srow)

        # ---- timeline
        self.timeline = Timeline()
        self.timeline.setMinimumHeight(150)
        self.timeline.selected.connect(lambda i: self.select(i, from_timeline=True))
        self.timeline.changed.connect(self._timeline_changed)
        self.timeline.edited.connect(self._schedule_refine)
        self.timeline.created.connect(self._create)
        self.timeline.deleted.connect(self._delete)
        self.timeline.play_requested.connect(self.play_clip)
        vsplit = QSplitter(Qt.Vertical)   # drag the divider to give the timeline or the list more room
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self.timeline)

        # ---- chips (filter by note)
        self.chips_box = QWidget()
        self.chips = QHBoxLayout(self.chips_box)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        chips_scroll = QScrollArea()
        chips_scroll.setWidget(self.chips_box)
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setFixedHeight(44)
        chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # ---- bottom: list | video + selected clip
        split = QSplitter(Qt.Horizontal)
        lbox = QGroupBox("Suggested clips")
        ll = QVBoxLayout(lbox)
        ll.addWidget(chips_scroll)
        qrow = QHBoxLayout()
        for text, fn in (("Tick all", self.tick_all), ("Best take per note only", self.tick_best),
                         ("Untick all", self.untick_all)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            qrow.addWidget(b)
        qrow.addStretch(1)
        ll.addLayout(qrow)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Use", "Sound", "Becomes", "Time", "Length", "Clarity"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 44)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 110)
        self.tree.itemChanged.connect(self._tree_ticked)
        self.tree.currentItemChanged.connect(self._tree_pick)
        ll.addWidget(self.tree)
        split.addWidget(lbox)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.video = QVideoWidget()
        self.video.setMinimumSize(240, 140)
        self.video.setStyleSheet("background:#000; border-radius:8px;")
        self.player = QMediaPlayer(self)
        self.vaudio = QAudioOutput(self)
        self.player.setAudioOutput(self.vaudio)
        self.player.setVideoOutput(self.video)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.positionChanged.connect(self._position)
        self._play_end: float | None = None   # stop when the video reaches this time (s)
        self._stop = QTimer(self)               # backstop in case the position never gets there
        self._stop.setSingleShot(True)
        self._stop.timeout.connect(self._stop_playing)
        rl.addWidget(self.video, 1)

        pbox = QGroupBox("Selected clip")
        form = QFormLayout(pbox)
        self.d_start, self.d_end = QDoubleSpinBox(), QDoubleSpinBox()
        for sp in (self.d_start, self.d_end):
            sp.setDecimals(2)
            sp.setSingleStep(0.01)
            sp.setSuffix(" s")
            sp.valueChanged.connect(self._spin_changed)
        trow = QHBoxLayout()
        trow.addWidget(self.d_start)
        trow.addWidget(QLabel("to"))
        trow.addWidget(self.d_end)
        form.addRow("Clip", trow)
        self.d_heard = QLabel("—")
        form.addRow("Heard", self.d_heard)
        arow = QHBoxLayout()
        self.d_drum = QCheckBox("🥁 Drum sound")
        self.d_drum.toggled.connect(self._drum_toggled)
        self.d_note = note_combo(NOTE_LO, NOTE_HI)
        self.d_note.activated.connect(self._note_chosen)
        self.d_pad = drum_combo()
        self.d_pad.activated.connect(self._pad_chosen)
        arow.addWidget(self.d_note, 1)
        arow.addWidget(self.d_pad, 1)
        arow.addWidget(self.d_drum)
        form.addRow("Use it for", arow)
        brow = QHBoxLayout()
        self.play_btn = QPushButton("▶  Play clip")
        self.play_btn.setToolTip("Plays this part of the video with its own sound")
        self.play_btn.clicked.connect(lambda: self.play_clip(self.sel))
        self.hear_btn = QPushButton("♪  Hear it as in the video")
        self.hear_btn.setToolTip("Plays just this sound, tuned and levelled like the finished video")
        self.hear_btn.clicked.connect(self.hear)
        self.del_btn = QPushButton("✕  Remove")
        self.del_btn.clicked.connect(lambda: self._delete(self.sel))
        brow.addWidget(self.play_btn)
        brow.addWidget(self.hear_btn)
        brow.addWidget(self.del_btn)
        form.addRow("", brow)
        rl.addWidget(pbox)
        split.addWidget(right)
        split.setSizes([620, 600])
        vsplit.addWidget(split)
        vsplit.setStretchFactor(0, 2)
        vsplit.setStretchFactor(1, 3)
        root.addWidget(vsplit, 1)
        self._panel_widgets = [self.d_start, self.d_end, self.d_drum, self.d_note, self.d_pad, self.play_btn,
                               self.hear_btn, self.del_btn]

        # ---- buttons
        bot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setProperty("hint", True)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long messages never widen the window
        bot.addWidget(self.status, 1)
        self.save_btn = QPushButton("💾  Save changes to this clip")
        self.save_btn.setProperty("primary", True)
        self.save_btn.clicked.connect(self.save_edit)
        self.save_btn.setVisible(edit is not None)
        bot.addWidget(self.save_btn)
        self.add_sel_btn = QPushButton("Add selected only")
        self.add_sel_btn.clicked.connect(lambda: self.add(only_selected=True))
        bot.addWidget(self.add_sel_btn)
        self.add_btn = QPushButton("Add ticked clips")
        self.add_btn.setProperty("primary", edit is None)
        self.add_btn.clicked.connect(lambda: self.add(only_selected=False))
        bot.addWidget(self.add_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bot.addWidget(close)
        outer.addWidget(scrollable(body), 1)
        outer.addLayout(bot)              # the buttons always stay visible

        self._refine_timer = QTimer(self)
        self._refine_timer.setSingleShot(True)
        self._refine_timer.timeout.connect(self._run_refine)
        self.select(-1)
        self._start()

    # ------------------------------------------------------------------ analysis
    def _start(self) -> None:
        if self.path in _RESULTS:
            self.audio = sources.get_audio(self.path)
            self._analysed(_RESULTS[self.path])
            return
        self.summary.setText("Finding clip points…")

        def job(p, progress=None, cancel=None):
            progress(0.0, "Reading the video’s sound…")
            audio = sources.get_audio(p)
            return audio, clipfinder.analyse(audio, SR, progress, cancel)

        def prog(f, m):
            self.busy.setValue(int(f * 1000))
            self.status.setText(m)

        def done(out):
            self.task = None
            self.audio, res = out
            _RESULTS[self.path] = res
            self._analysed(res)

        def fail(e, _tb):
            self.task = None
            self.busy.setVisible(False)
            self.cancel_btn.setVisible(False)
            if isinstance(e, FinderCancelled):
                self.status.setText("Cancelled.")
                self.summary.setText("")
            else:
                self.summary.setText("")
                self.status.setText("")
                QMessageBox.warning(self, "Couldn’t read this video", f"{os.path.basename(self.path)}:\n{e}")

        self.task = run_task(job, self.path, on_done=done, on_error=fail, on_progress=prog)

    def _cancel(self) -> None:
        if self.task:
            self.task.cancel.set()

    def _analysed(self, res: FinderResult) -> None:
        self.result = res
        self.busy.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.status.setText("")
        self.items = [dataclasses.replace(c) for c in res.candidates]
        self.hit_pad = self.target if (self.mode == "drums" and self.target is not None) else self.free_pad()
        for i in range(len(self.items)):
            self.assign[i] = self._default_assign(i)
        self.best = clipfinder.best_takes(self.items)
        self.timeline.set_data(res.duration, res.peaks, res.times, res.midi)
        for sp in (self.d_start, self.d_end):
            sp.setRange(0.0, res.duration)
        if self.edit is not None:
            self._add_edit_item()
        elif self.target is not None and self.mode == "notes":
            # opened for one key: pick the clearest take nearest that note for it
            notes = [i for i, c in enumerate(self.items) if c.kind == "note"]
            if notes:
                i = min(notes, key=lambda i: (abs(self.items[i].midi - self.target), -self.items[i].confidence))
                self.assign[i] = (False, self.target)
                self.manual.add(i)
                self.select(i)
        if not self.items and self.edit is None:
            self.summary.setText("No clear notes or hits found. Drag on the timeline to cut a clip yourself.")
        self.refresh()
        if self.sel < 0:
            first = next((i for i in self._shown()), -1)
            self.select(first)

    def _default_assign(self, i: int) -> tuple[bool, int | None]:
        c = self.items[i]
        if c.kind == "hit" or c.midi is None:
            return True, self.hit_pad
        return False, min(NOTE_HI, max(NOTE_LO, c.note))

    def _add_edit_item(self) -> None:
        drum, key, take = self.edit
        slots = self.state.project.drum_slots if drum else self.state.project.slots
        clip = slots[key].take(take)
        end = clip.trim_end if clip.trim_end is not None else min(self.result.duration, clip.trim_start + 1.0)
        c = Candidate(clip.trim_start, end, clip.detected_midi, "hit" if drum else "note", 1.0, clip.loudness_db)
        self.items.append(c)
        i = len(self.items) - 1
        self.edit_idx = i
        self.user_made.add(i)
        self.assign[i] = (drum, key)
        self.manual.add(i)
        self.select(i)

    # ------------------------------------------------------------------ state helpers
    def _threshold(self) -> float:
        return self.sens.value() / 100 * 0.7

    def _visible(self, i: int) -> bool:
        if i in self.removed:
            return False
        return i in self.user_made or self.items[i].confidence >= self._threshold()

    def _default_tick(self, i: int) -> bool:
        if self.edit is not None or i in self.added:
            return False
        if i in self.user_made:
            return True
        wanted = "hit" if self.mode == "drums" else "note"
        return self.items[i].kind == wanted

    def _is_ticked(self, i: int) -> bool:
        return self._visible(i) and i not in self.added and self.user_tick.get(i, self._default_tick(i))

    def _matches(self, i: int) -> bool:
        f = self.filter
        if f == "all":
            return True
        if f == "notes":
            return not self.assign[i][0]
        if f == "hits":
            return self.assign[i][0]
        return not self.assign[i][0] and self.assign[i][1] == f

    def _shown(self) -> list[int]:
        return [i for i in range(len(self.items)) if self._visible(i) and self._matches(i)]

    def _label(self, i: int) -> str:
        lab = item_label(self.items[i], self.assign[i])
        if i in self.added:
            lab = "✔ " + lab
        elif i in self.best and not self.assign[i][0]:
            lab += " ★"
        return lab

    # ------------------------------------------------------------------ refresh
    def refresh(self) -> None:
        if self.result is None:
            return
        vis = {i for i in range(len(self.items)) if self._visible(i)}
        ticked = {i for i in vis if self._is_ticked(i)}
        labels = {i: self._label(i) for i in vis}
        self.timeline.set_items(self.items, vis, ticked, labels)
        # summary
        notes = [i for i in vis if not self.assign[i][0] and self.assign[i][1] is not None]
        hits = [i for i in vis if self.assign[i][0]]
        names = sorted({self.assign[i][1] for i in notes})
        txt = f"Found {len(vis)} clip point{'s' if len(vis) != 1 else ''}: "
        txt += f"{len(names)} note{'s' if len(names) != 1 else ''}"
        if names:
            txt += f" ({midi_to_name(names[0])}–{midi_to_name(names[-1])}, {len(notes)} takes)"
        txt += f" · {len(hits)} hit{'s' if len(hits) != 1 else ''}"
        self.summary.setText(txt)
        n_tick = len(ticked)
        self.add_btn.setText(f"Add ticked clips ({n_tick})")
        self.add_btn.setEnabled(n_tick > 0)
        self.add_sel_btn.setEnabled(self.sel >= 0 and self.sel not in self.removed)
        self._rebuild_chips(notes, hits)
        self._rebuild_tree(ticked)

    def _rebuild_chips(self, notes: list[int], hits: list[int]) -> None:
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                self.chip_group.removeButton(w)
                w.deleteLater()
        counts: dict[int, int] = {}
        for i in notes:
            counts[self.assign[i][1]] = counts.get(self.assign[i][1], 0) + 1
        chips = [("All", "all"), ("Notes", "notes"), (f"🥁 Hits ×{len(hits)}", "hits")]
        chips += [(f"{midi_to_name(n)} ×{counts[n]}", n) for n in sorted(counts)]
        for text, key in chips:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(self.filter == key)
            b.setStyleSheet("padding:3px 10px;")
            b.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self.chip_group.addButton(b)
            self.chips.addWidget(b)
        self.chips.addStretch(1)

    def _set_filter(self, key) -> None:
        self.filter = key
        self.refresh()

    def _rebuild_tree(self, ticked: set[int]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        cur = None
        for i in self._shown():
            c = self.items[i]
            drum, n = self.assign[i]
            sound = "🥁 hit" if c.kind == "hit" else (describe_detected(c.midi) if c.midi is not None else "?")
            becomes = (f"🥁 {drum_short(n)}" if n is not None else "🥁 ?") if drum else \
                (midi_to_name(n) if n is not None else "?")
            if i in self.best and not drum:
                becomes += "  ★"
            if i in self.added:
                becomes = "✔ added"
            bars = int(round(c.confidence * 5))
            it = QTreeWidgetItem(["", sound, becomes, f"{int(c.start) // 60}:{c.start % 60:05.2f}",
                                  f"{c.length:.2f} s", "■" * bars + "□" * (5 - bars)])
            it.setData(0, Qt.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if i in ticked else Qt.Unchecked)
            if i in self.added:
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
            self.tree.addTopLevelItem(it)
            if i == self.sel:
                cur = it
        if cur is not None:
            self.tree.setCurrentItem(cur)
        self.tree.blockSignals(False)

    # ------------------------------------------------------------------ selection + editing
    def select(self, i: int, from_timeline: bool = False) -> None:
        self.sel = i
        self.timeline.select(i, scroll=not from_timeline)
        ok = 0 <= i < len(self.items) and i not in self.removed
        for w in self._panel_widgets:
            w.setEnabled(ok)
        if not ok:
            self.d_heard.setText("—")
            return
        c = self.items[i]
        for sp, v in ((self.d_start, c.start), (self.d_end, c.end)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        self._fill_heard(i)
        drum, n = self.assign[i]
        for w in (self.d_drum, self.d_note, self.d_pad):
            w.blockSignals(True)
        self.d_drum.setChecked(drum)
        self.d_note.setVisible(not drum)
        self.d_pad.setVisible(drum)
        if n is not None:
            self._set_combo(self.d_pad if drum else self.d_note, n)
        for w in (self.d_drum, self.d_note, self.d_pad):
            w.blockSignals(False)
        locked = i == self.edit_idx
        self.d_drum.setEnabled(not locked)
        self.d_note.setEnabled(not locked)
        self.d_pad.setEnabled(not locked)
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.player.setPosition(int(c.start * 1000))
        if not from_timeline:
            return
        # keep the list in step with a click on the timeline
        self.tree.blockSignals(True)
        for k in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(k)
            if it.data(0, Qt.UserRole) == i:
                self.tree.setCurrentItem(it)
                break
        self.tree.blockSignals(False)
        self.add_sel_btn.setEnabled(True)

    def _fill_heard(self, i: int) -> None:
        c = self.items[i]
        if c.midi is not None:
            txt = f"<b>{describe_detected(c.midi)}</b>"
        else:
            txt = "no clear note (a percussive sound)" if c.kind == "hit" else "no clear note"
        if c.loudness_db is not None:
            txt += f"   ·   loudness {c.loudness_db:.0f} dB"
        self.d_heard.setText(txt)

    def _tree_pick(self, cur, _prev) -> None:
        if cur is not None:
            self.select(cur.data(0, Qt.UserRole))
            self.add_sel_btn.setEnabled(True)

    def _tree_ticked(self, item, col) -> None:
        if col == 0:
            i = item.data(0, Qt.UserRole)
            self.user_tick[i] = item.checkState(0) == Qt.Checked
            self.refresh()

    def _timeline_changed(self, i: int) -> None:
        if i == self.sel:
            c = self.items[i]
            for sp, v in ((self.d_start, c.start), (self.d_end, c.end)):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)

    def _spin_changed(self) -> None:
        i = self.sel
        if i < 0:
            return
        a, b = self.d_start.value(), self.d_end.value()
        if b - a < 0.05:
            return
        self.items[i].start, self.items[i].end = a, b
        self.timeline.update()
        self._schedule_refine(i)

    def _schedule_refine(self, i: int) -> None:
        self._refine_idx = i
        self._refine_timer.start(250)
        self.refresh()

    def _run_refine(self) -> None:
        i = self._refine_idx
        if i < 0 or self.audio is None or i >= len(self.items):
            return
        c = self.items[i]

        def done(out):
            midi, _conf, loud = out
            c.loudness_db = loud
            if c.kind == "note" or (midi is not None and i in self.user_made and not self.assign[i][0]):
                c.midi = midi
                if midi is not None and i not in self.manual and not self.assign[i][0]:
                    self.assign[i] = (False, min(NOTE_HI, max(NOTE_LO, int(round(midi)))))
            if i == self.sel:
                self.select(i)
            self.refresh()

        run_task(clipfinder.refine, self.audio, SR, c.start, c.end, on_done=done, on_error=lambda *_: None)

    def _create(self, start: float, end: float) -> None:
        kind = "hit" if self.mode == "drums" else "note"
        self.items.append(Candidate(start, end, None, kind, 1.0, None))
        i = len(self.items) - 1
        self.user_made.add(i)
        self.assign[i] = (True, self.hit_pad) if kind == "hit" else (False, self.target)
        self.select(i)
        self._schedule_refine(i)

    def _delete(self, i: int) -> None:
        if i < 0 or i == self.edit_idx:
            return
        self.removed.add(i)
        self.select(-1)
        self.refresh()

    def _drum_toggled(self, on: bool) -> None:
        i = self.sel
        if i < 0:
            return
        c = self.items[i]
        self.assign[i] = (True, self.hit_pad) if on else \
            (False, min(NOTE_HI, max(NOTE_LO, c.note)) if c.note is not None else self.target)
        self.manual.add(i)
        self.select(i)
        self.refresh()

    def _note_chosen(self) -> None:
        if self.sel >= 0:
            self.assign[self.sel] = (False, self.d_note.currentData())
            self.manual.add(self.sel)
            self.refresh()

    def _pad_chosen(self) -> None:
        if self.sel >= 0:
            self.assign[self.sel] = (True, self.d_pad.currentData())
            self.manual.add(self.sel)
            self.refresh()

    # ------------------------------------------------------------------ ticking
    def tick_all(self) -> None:
        for i in self._shown():
            self.user_tick[i] = True
        self.refresh()

    def untick_all(self) -> None:
        for i in self._shown():
            self.user_tick[i] = False
        self.refresh()

    def tick_best(self) -> None:
        shown = self._shown()
        best: dict[int, int] = {}
        for i in shown:
            drum, n = self.assign[i]
            if not drum and n is not None and (n not in best or self.items[i].confidence > self.items[best[n]].confidence):
                best[n] = i
        keep = set(best.values())
        for i in shown:
            self.user_tick[i] = i in keep or (self.assign[i][0] and self.mode == "drums")
        self.refresh()

    # ------------------------------------------------------------------ playing
    def play_clip(self, i: int) -> None:
        if i < 0 or i >= len(self.items):
            return
        c = self.items[i]
        if i != self.sel:
            self.select(i)
        self.vaudio.setMuted(False)
        # seeking happens in the background, so stop by position (not by a timer that may run out before the
        # sound has even started)
        self._play_end = c.end
        self.player.setPosition(int(c.start * 1000))
        self.player.play()
        self._stop.start(int(max(0.05, c.length) * 1000) + 2500)

    def _stop_playing(self) -> None:
        self._play_end = None
        self._stop.stop()
        self.player.pause()
        self.timeline.set_playhead(None)

    def _position(self, ms: int) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.timeline.set_playhead(ms / 1000)
            if self._play_end is not None and ms / 1000 >= self._play_end:
                self._stop_playing()

    def _clip_slot(self, i: int) -> tuple[ClipSlot, float]:
        c = self.items[i]
        drum, n = self.assign[i]
        slot = ClipSlot(self.path, c.start, c.end, None if drum else c.midi, autotune=not drum,
                        loudness_db=c.loudness_db)
        shift = 0.0 if drum or n is None else slot.autotune_shift(n)
        return slot, shift

    def hear(self) -> None:
        if self.sel >= 0:
            slot, shift = self._clip_slot(self.sel)
            self.pad.trigger_clip(slot, shift)

    # ------------------------------------------------------------------ adding
    def add(self, only_selected: bool = False) -> None:
        if only_selected:
            idxs = [self.sel] if self.sel >= 0 and self.sel not in self.removed else []
        else:
            idxs = [i for i in range(len(self.items)) if self._is_ticked(i)]
        idxs = [i for i in idxs if self.assign[i][1] is not None]
        if not idxs:
            QMessageBox.information(self, "Nothing to add", "Tick some clips first (and choose a note or pad "
                                                            "for any marked “?”).")
            return
        groups: dict[tuple[bool, int], list[int]] = {}
        for i in idxs:
            groups.setdefault(self.assign[i], []).append(i)
        proj = self.state.project
        existing = [g for g in groups if g[1] in (proj.drum_slots if g[0] else proj.slots)]
        policy = "extra"
        if existing:
            names = ", ".join(drum_short(n) if d else midi_to_name(n) for d, n in sorted(existing))
            box = QMessageBox(self)
            box.setWindowTitle("Some already have clips")
            box.setText(f"<b>{names}</b> already {'have' if len(existing) > 1 else 'has'} a clip.<br><br>"
                        "Add the new clips as extra takes (they’ll be used in turn), replace the old clips, "
                        "or skip those?")
            extra_b = box.addButton("Add as extra takes", QMessageBox.AcceptRole)
            repl_b = box.addButton("Replace", QMessageBox.DestructiveRole)
            skip_b = box.addButton("Skip those", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(extra_b)
            box.exec()
            clicked = box.clickedButton()
            if clicked is extra_b:
                policy = "extra"
            elif clicked is repl_b:
                policy = "replace"
            elif clicked is skip_b:
                policy = "skip"
            else:
                return
        touched: list[ClipSlot] = []
        n_clips = n_notes = n_pads = 0
        for (drum, n), members in groups.items():
            slots = proj.drum_slots if drum else proj.slots
            if n in slots and policy == "skip":
                continue
            members.sort(key=lambda i: -self.items[i].confidence)
            takes = [Take(self.path, self.items[i].start, self.items[i].end,
                          None if drum else self.items[i].midi, self.items[i].loudness_db) for i in members]
            if n in slots and policy == "extra":
                slots[n].extra_takes.extend(takes)
            else:
                t0 = takes[0]
                slots[n] = ClipSlot(self.path, t0.trim_start, t0.trim_end, t0.detected_midi, autotune=not drum,
                                    loudness_db=t0.loudness_db, extra_takes=takes[1:])
            touched.append(slots[n])
            self.added.update(members)
            n_clips += len(members)
            n_pads += drum
            n_notes += not drum
        if not touched:
            return
        self.state.touch_slots()
        if self.on_added:
            self.on_added(touched)
        parts = []
        if n_notes:
            parts.append(f"{n_notes} note{'s' if n_notes != 1 else ''}")
        if n_pads:
            parts.append(f"{n_pads} pad{'s' if n_pads != 1 else ''}")
        self.status.setText(f"✔ Added {n_clips} clip{'s' if n_clips != 1 else ''} to {' and '.join(parts)}.")
        self.refresh()

    def save_edit(self) -> None:
        if self.edit is None or self.edit_idx < 0:
            return
        drum, key, take = self.edit
        slots = self.state.project.drum_slots if drum else self.state.project.slots
        if key not in slots:
            return
        c = self.items[self.edit_idx]
        slots[key].set_take(take, Take(self.path, c.start, c.end, None if drum else c.midi, c.loudness_db))
        self.state.touch_slots()
        if self.on_added:
            self.on_added([slots[key]])
        self.status.setText("✔ Saved.")

    def closeEvent(self, e) -> None:
        if self.task:
            self.task.cancel.set()
        self.player.stop()
        super().closeEvent(e)
