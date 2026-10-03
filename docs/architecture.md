# Architecture

Keep this file up to date when modules are added, renamed or reworked (see [CLAUDE.md](../CLAUDE.md)).

## Pipeline
```
clips (videos) ──analyze_clip──▶ Project.slots / Project.drum_slots   (ClipSlot: path, trim, pitch, gain)
song file ──importers.load_song──▶ Song (NoteEvent list, tracks; drum events have drum=True)
Song + SongOptions ──songops.apply_options──▶ events to play (transpose, tempo, melody-only, track choice)
events + slots ──planner.make_plan──▶ Plan (NoteInstance per sounding note, plus missing / borrowed / octave / stand-in)
Plan ──render.renderer──▶ audio mix (audio_dsp.render_note) + video frames (render.compositor)
     ──▶ + outro video, text overlays (render.overlays), loudness ──▶ MP4 (PyAV)
```
Without the window: `cli.py` (`analyse`, `auto`, `render`) drives the same engine. `auto.auto_project` replaces the
person at the controls: long videos ──clipfinder──▶ slots and takes ──best_transpose──▶ Project + `AutoReport`.
The daily Shorts automation built on top of this is described in [autopilot.md](autopilot.md).
The GUI (`vsampler/gui/`) edits a single `Project` held by `AppState`. It listens to the `slots_changed`,
`song_changed`, `options_changed` and `project_replaced` signals, and runs slow work off the UI thread with
`worker.run_task`.

## Key concepts
- **Takes:** a `ClipSlot` is take 0, and `ClipSlot.extra_takes` holds more `Take`s. `make_plan` rotates through them
  per slot in time order (`NoteInstance.take`), and every take gets its own auto-tune shift.
  `renderer.project_take(project, key, take)` gives the clip for any instance, and `Prepared.sources` is keyed by
  `(slot key, take)`. The compositor keeps one tile per slot and shows whichever take is sounding.
- **Clip finder:** `clipfinder.analyse` runs pYIN over the whole long video in 30 s chunks and returns a
  `FinderResult` (waveform overview, pitch curve, `Candidate`s). Note candidates are runs of steady, voiced pitch,
  split at onsets so repeated notes become separate takes. Hit candidates are strong onsets with no pitch.
  `refine` re-checks a moved clip. Clips cut from a long video are ordinary slots or takes whose `path` is the long
  video, with their own trims, so the rest of the engine needs nothing special. Thumbnails are keyed by
  `clips.trim_thumb_key`, and `render.sources` keeps an LRU of clip sources, so many clips from one file can live
  side by side.
- **Slots:** `Project.slots` maps a MIDI note to a `ClipSlot`, and `Project.drum_slots` maps a General MIDI drum
  note (36 = kick, 38 = snare…) to a `ClipSlot`. Inside the engine (plan instances, clip `sources`, collage tiles),
  a drum clip is referred to by `drums.drum_key(n) = 1000 + n`, so it never collides with a piano key. Use
  `renderer.project_slot(project, key)` to look up the clip behind any key, and `drums.slot_label(key)` for its
  label.
- **Resolving a note:** `planner.resolve()` decides what plays a pitched note: its own clip (`exact`), else the same
  note an octave away (`octave`, if Octave jump is on), else the nearest clip pitch-shifted (`borrowed`, if
  allowed), else `missing`. `planner.resolve_drum()` does the same for drums: the pad's own clip, else a stand-in
  from the same drum family (`drums.nearest_drum`). `make_plan` and the sample pad both use these functions, so a
  key sounds exactly like it will in the video.
- **Drums are one-shots.** No auto-tune or pitch-shift, and the hit lasts exactly the clip's trimmed length
  (`renderer.prepare` sets it), so a drum hit is never stretched or cut short. Transpose and melody-only ignore drums.
- **Default tracks:** with `SongOptions.enabled_tracks = None`, all pitched tracks play, and drum tracks play only
  if the project has drum clips (`songops.default_tracks`).
- **Speed:** `renderer.prepare` loads clips in parallel (a thread pool; PyAV decoding and Rubber Band
  subprocesses run outside the GIL), and `mix_audio` renders each distinct (clip, shift, length) note once, also in
  parallel. `render_audio_preview` uses `prepare(audio_only=True, max_seconds=…)`: sound only
  (`sources.get_audio_source`, no video frames), limited to the start of the song. `sources.get_audio` holds a
  per-file lock, so clips from one long video decode its sound once.
- **Stretching:** when a note is longer than its clip, `audio_dsp.render_note` time-stretches it (Rubber Band,
  attack kept intact). `audio_dsp.time_map` keeps the video in sync.
- **Sample pad** (`gui/widgets/sampler.py`): renders each clip once with `renderer.render_clip_audio` (same gain
  and levelling as the render) and keeps it in memory. A small mixer (`_Mixer`, a `QIODevice`) streams to a
  `QAudioSink` in pull mode, so presses overlap and restart reliably. `warm()` gets every take ready after the
  slots change. Don't go back to `QSoundEffect`: restarting it is unreliable on Windows.
- **Song excerpt and length:** `SongOptions.start_s` / `end_s` (seconds in the song file) are applied first in
  `songops.apply_options`, so only notes starting inside the excerpt play, cut off at its end. Then tempo and the
  usual "start right away" shift apply. `RenderSettings.max_duration` is applied in `renderer.prepare`: notes
  after it are dropped and sounding ones are cut at it.
- **Presets:** `models.PRESETS` / `apply_preset` set several `RenderSettings` at once. `shorts` is 1080×1920,
  dynamic layout, no labels, ≤ 55 s, `loudness_db` -14.
- **Overlays and outro:** `render_video` mixes the song, then appends `RenderSettings.outro` (a whole video,
  levelled to the song's loudness, frames scaled with `layouts.cover`). Every frame, song or outro, goes through
  `overlays.OverlayPainter.apply`. Overlay times are in the finished video (a negative start counts from the end).
  Text is drawn with Pillow (bundled Anton font plus the system colour-emoji font), each block rendered once and
  cached, and placed inside `overlays.safe_area` (vertical videos avoid the right-hand buttons and bottom
  caption area). `apply` returns a copy, because the compositor reuses cached frames.
- **Loudness:** `renderer.set_loudness` scales the mix to `loudness_db` (dBFS RMS of the sound, the same measure
  as clip levelling) with a soft limiter above 0.9, so it never clips.
- **Automatic projects:** `auto.build_slots` groups clip-finder candidates by note (most confident first, then
  takes, optionally cut to `max_fragment`). The loudest hits go on kick, snare and hi-hat. `best_transpose` tries
  -12..12 with `make_plan`, scoring exact 1, octave 0.9 and pitch-shifted 0.5. `report_for` gives the coverage and
  every clip's source and times.
- **Screen fitting** (`gui/screen.py`): `ScreenGuard` is installed on the app in `main.py` and keeps every
  top-level window inside the screen's available area. It also caps the minimum size Qt derives from the layout.
  Use `preferred_size()` for a window's starting size and `scrollable()` around big content.

## Files
| Path | What it does |
|---|---|
| `main.py` | Entry point: the GUI, or the command line (`analyse`, `auto`, `render`, old `--render`) via `vsampler/cli.py` |
| `vsampler/cli.py`, `vsampler/__main__.py` | Command line (`python -m vsampler …`): JSON out, progress to stderr and `<out>.log`, exit codes |
| `vsampler/auto.py` | `auto_project`, `find_clips`, `build_slots`, `best_transpose`, `report_for` (`AutoReport`) |
| `vsampler/models.py` | Data classes: `ClipSlot`, `NoteEvent`, `Song`, `SongOptions`, `TextOverlay`, `RenderSettings`, `Project` (+ JSON), `PRESETS` / `apply_preset` |
| `vsampler/notes.py` | Note-name ↔ MIDI helpers |
| `vsampler/drums.py` | GM drum names, the `CORE_KIT` pad layout, drum families, drum key helpers |
| `vsampler/clipfinder.py` | Suggests every note and hit clip point in a long recording (`analyse`, `best_takes`, `refine`) |
| `vsampler/clips.py` | Decoding, pitch detection (pYIN), auto-trim, loudness, frame storage |
| `vsampler/audio_dsp.py` | Pitch-preserving stretch and pitch-shift (Rubber Band CLI, librosa fallback) |
| `vsampler/importers/` | MIDI, MusicXML (with percussion-part detection), audio (basic-pitch), sheet music (Audiveris) |
| `vsampler/songops.py` | Track choice, transpose, tempo, melody-only (skyline), best-fit transpose |
| `vsampler/planner.py` | `resolve` / `resolve_drum` / `make_plan` / `plan_for_project` |
| `vsampler/render/sources.py` | Cache of decoded clip audio and frames (`get_source`, `trimmed_audio`) |
| `vsampler/render/renderer.py` | `prepare`, `mix_audio`, `render_video`, `render_audio_preview`, `render_slot_audio` |
| `vsampler/render/compositor.py`, `layouts.py` | Grid / "only who's singing" frame building |
| `vsampler/render/overlays.py` | Text overlays (`OverlayPainter`, `STYLES`, `safe_area`) |
| `vsampler/gui/main_window.py` | Window, menus, project open / save, help text |
| `vsampler/gui/clips_tab.py` | Step 1: Notes / Drums switch, keyboard and pads, computer-keyboard play, clip details and takes; long videos go to the clip finder |
| `vsampler/gui/clip_finder.py` | The clip finder window: suggestions list, chips, selected-clip panel, adding clips as takes |
| `vsampler/gui/song_tab.py` | Step 2: song loading, tracks, transpose and speed, Octave jump, missing notes and drums panel |
| `vsampler/gui/output_tab.py` | Step 3: look settings, preview frame, rendering |
| `vsampler/gui/widgets/` | `keyboard.py` (piano), `drum_pads.py`, `piano_roll.py`, `sampler.py` (sample pad, rotates takes), `timeline.py` (clip finder timeline), `player.py` |
| `tests/` | `pytest` suite (`test_engine.py`, `test_automation.py`). `fixtures.py` makes synthetic clips and MIDI files |
| `Dockerfile`, `requirements-server.txt` | Engine-only Linux image for a server (see `autopilot.md`) |
| `assets/fonts/` | Anton (SIL OFL), the overlay font |
| `build_exe.py`, `VideoSampler.spec`, `installer/`, `setup_tools.py` | Release build (see `releasing.md`) |
