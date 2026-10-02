# Architecture

Keep this file up to date when modules are added, renamed or reworked (see [CLAUDE.md](../CLAUDE.md)).

## Pipeline
```
clips (videos) ──analyze_clip──▶ Project.slots / Project.drum_slots   (ClipSlot: path, trim, pitch, gain)
song file ──importers.load_song──▶ Song (NoteEvent list, tracks; drum events have drum=True)
Song + SongOptions ──songops.apply_options──▶ events to play (transpose, tempo, melody-only, track choice)
events + slots ──planner.make_plan──▶ Plan (NoteInstance per sounding note, plus missing / borrowed / octave / stand-in)
Plan ──render.renderer──▶ audio mix (audio_dsp.render_note) + video frames (render.compositor) ──▶ MP4 (PyAV)
```
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
- **Stretching:** when a note is longer than its clip, `audio_dsp.render_note` time-stretches it (Rubber Band,
  attack kept intact). `audio_dsp.time_map` keeps the video in sync.
- **Sample pad** (`gui/widgets/sampler.py`): renders each clip once with `renderer.render_slot_audio` (audio only,
  using the same gain and levelling as the render), saves it as a WAV and plays it with `QSoundEffect` for low
  latency. `warm()` gets every clip ready after the slots change.

## Files
| Path | What it does |
|---|---|
| `main.py` | Entry point: the GUI, or `--render project.vsproj out.mp4` from the command line |
| `vsampler/models.py` | Data classes: `ClipSlot`, `NoteEvent`, `Song`, `SongOptions`, `RenderSettings`, `Project` (+ JSON) |
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
| `vsampler/gui/main_window.py` | Window, menus, project open / save, help text |
| `vsampler/gui/clips_tab.py` | Step 1: Notes / Drums switch, keyboard and pads, computer-keyboard play, clip details and takes; long videos go to the clip finder |
| `vsampler/gui/clip_finder.py` | The clip finder window: suggestions list, chips, selected-clip panel, adding clips as takes |
| `vsampler/gui/song_tab.py` | Step 2: song loading, tracks, transpose and speed, Octave jump, missing notes and drums panel |
| `vsampler/gui/output_tab.py` | Step 3: look settings, preview frame, rendering |
| `vsampler/gui/widgets/` | `keyboard.py` (piano), `drum_pads.py`, `piano_roll.py`, `sampler.py` (sample pad, rotates takes), `timeline.py` (clip finder timeline), `player.py` |
| `tests/` | `pytest` suite. `fixtures.py` makes synthetic clips and MIDI files |
| `build_exe.py`, `VideoSampler.spec`, `installer/`, `setup_tools.py` | Release build (see `releasing.md`) |
