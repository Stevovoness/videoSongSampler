# Changelog

Every change gets a line here (see the workflow in [CLAUDE.md](../CLAUDE.md)). New entries go under
**Unreleased** until a version is released.

## Unreleased
- **Long speeches work with `auto`.** Videos of any length are cut into ~10-minute pieces first (`media.split_video`,
  no re-encoding, takes seconds), so a 2-hour video no longer needs gigabytes of memory.
- **Speech is analysed ~25× faster.** `auto` listens over the speaking-voice range (60–600 Hz) with a coarser pitch
  grid, on several CPU cores (`clipfinder.analyse(pitch_range=, resolution=, workers=)`); about 8× faster than real
  time on 8 cores. `--full-range` keeps the old behaviour for singing and instruments.
- Each piece's analysis is saved next to it, so trying another song with the same footage is instant.
- `auto` plays just the song's tune: with a piano arrangement it picks the highest-sounding track, so the left
  hand's bass line doesn't creep in (`auto.melody_track`).
- `auto` options `--full-range`, `--workers`, `--work-dir`.
- When several key changes play the song equally well, `auto` picks the one that puts the tune in the middle of the
  clips, where the voice has the most takes (`auto.clip_centre`); it now tries -24..24 semitones.
- Tested on real footage: 3 h 20 min of White House video (a 43-minute announcement and the Marine Corps 250th)
  analysed in 32 minutes and gave 710 pitched syllables from D2 to C#5. The "Never Gonna Give You Up" chorus and
  Yankee Doodle were both covered 100% by exact notes, and the rendered Shorts were in tune (every checked note
  within half a semitone).
- `docs/TODO.md`: a to-do list for the app and the automation, ticked off as work is done. `CLAUDE.md` and
  `AGENTS.md` now require updating the docs and ticking off the list after every change.

## 1.4.0
- **Shorts / Reels / TikTok format.** A `shorts` preset makes a vertical 1080×1920 video under a minute, in the
  "only who's singing" layout, without note labels and at the loudness of other short videos (`apply_preset`).
- **Text on the video.** `RenderSettings.overlays` holds text lines: a big hook at the top (it shrinks after 3 s),
  timed captions, a call-to-action card and a watermark. Text wraps, fits, shows colour emoji and stays clear of
  the app buttons and captions on phones (`render/overlays.py`, drawn with the bundled Anton font).
- **Outro.** `RenderSettings.outro` plays a video after the song (e.g. a personal sign-off), at the song's loudness.
- **Play part of a song.** `SongOptions.start_s` / `end_s` pick the catchy section; `RenderSettings.max_duration`
  cuts the video's song part to a length; `RenderSettings.loudness_db` sets the finished loudness.
- **Automatic projects.** `vsampler/auto.py` builds a whole project from long videos and a song: the best clip of
  every note (more become takes), drum hits on pads, the key change that plays the most of the song, and a report
  of how much of the song is covered and where every clip came from.
- **Command line.** `main.py analyse | auto | render …` (also `python -m vsampler …`), with JSON output and exit
  codes. The old `--render project out.mp4` still works.
- **Runs on a Linux server.** `requirements-server.txt` and a `Dockerfile` (engine only, no window).
- `docs/autopilot.md` describes the planned daily Shorts automation and custom-video service, and what is built.
- Project files gain `overlays`, `outro`, `preset`, `max_duration`, `loudness_db`, `start_s`, `end_s`. Older files
  still open.
- `build_exe.py` empties `release/` and `installer/Output/` first, so leftovers from an interrupted build can't be
  published alongside the new files.

## 1.3.1
- **Much faster previews.** "Listen with my clips" in Step 2 plays just the first 15 seconds by default (choose
  15 s / 30 s / 1 minute / whole song next to the button, and the choice is remembered). It loads only the clips'
  sound, never their video, and replays instantly if nothing has changed. A 60-second test song went from about
  45 s to about 6 s.
- **Faster video rendering.** Clips load in parallel, and notes are stretched and tuned in parallel on all CPU
  cores. Parallel jobs decode a long video's sound only once.
- **Windows always fit the screen.** An app-wide guard (`gui/screen.py`) shrinks and moves every window, dialog and
  message box so it stays inside the visible screen area. Big windows start at a size relative to the screen,
  and each step and the clip finder scroll instead of growing past the edge. The clip finder's buttons sit in a
  fixed footer that is always visible, and a draggable divider sits between its timeline and the list.
- **Sample pad plays every press.** It now mixes sounds itself and streams them straight to the sound card
  (`QAudioSink`), instead of using `QSoundEffect`, which on Windows sometimes ignored a restart or played nothing.
  Presses overlap, and pressing again restarts the sound cleanly.
- **"Play clip" no longer goes silent.** Video previews (in the clip finder and Step 1) now stop when the video
  reaches the clip's end, instead of after a timer that could run out while the video was still seeking.
- Background jobs whose window was closed no longer print errors.
- `build_exe.py` keeps PyInstaller's scratch and output folders in `%LOCALAPPDATA%\VideoSampler-build`. OneDrive
  was locking `build\` and `dist\` and making the build fail with "Access is denied". The installer script takes
  the output folder as `/DSourceDir`.
- `docs/releasing.md` explains how updates work: a newer installer upgrades in place.

## 1.3.0
- **Clip finder:** give the app a longer video and it suggests every clip point in it in one go: every held,
  clearly pitched note (often several takes of each) and every percussive hit.
  - Everything shows on a zoomable timeline (waveform and pitch curve) and in a list, with a filter chip for each
    note.
  - Drag a block's edges to trim a clip, drag its middle to move it, or drag on empty space to cut your own. The
    pitch is re-detected after each change.
  - Choose the note or drum pad each clip becomes, hear it as it will sound in the video, and add all ticked clips
    at once. Opens from "✂ Cut clips from a long video…" in Step 1, or automatically for any video longer than
    10 seconds.
  - Engine: `vsampler/clipfinder.py`. GUI: `gui/clip_finder.py` and `gui/widgets/timeline.py`.
- **Takes:** a note or pad can hold several clips, used in turn each time the song plays that note (and each time
  you press the key). Each take is auto-tuned on its own.
  - In Step 1 you can browse the takes (◀ ▶), make one the main take, remove one, or add another ("＋ Add take…").
  - Keys and pads with several takes show "×N".
  - "✂ Adjust in the video…" re-opens a take in the clip finder.
- Clips cut from the same video each get their own thumbnail and cached frames (the clip-source cache is now an LRU
  instead of one entry per file).
- Project files gain `extra_takes` on each clip. Older files still open.
- Added a `docs/` folder (architecture, release steps, this changelog), plus agent instructions in `CLAUDE.md` and
  `AGENTS.md`: update the docs, then commit and push after every change.
- `main.py` started with a Python that lacks the app's packages now explains how to run it with `.venv`, instead
  of crashing. `.vscode/settings.json` points VS Code's Run button and test runner at `.venv`.

## 1.2.0
- **Drums:** a 🎹 Notes | 🥁 Drums switch in Step 1. Percussive clips (slaps, claps, smacks…) go on General MIDI
  drum pads and play the song's drum part (a MIDI channel-10 track, or a percussion part in MusicXML).
  - Drum clips play exactly as recorded (no auto-tune, no stretching).
  - A missing drum sound can use a similar drum clip instead.
  - Drum tracks switch on automatically once there's a drum clip.
  - The piano roll has a drum lane, and collage tiles show drum names.
- **Sample pad:** clicking a key or pad plays it exactly as it will sound in the video, with the clip's picture.
  The computer keyboard plays notes (`A W S E D F T G Y H U J K O L P ;`, with `Z`/`X` for the octave) and pads
  (`1234 / QWER / ASDF / ZXCV`).
- **Octave jump:** an optional setting in Step 2. A note with no clip plays from a clip one or more octaves
  higher or lower, with no pitch-shifting. It's tried before the nearest-clip pitch-shift, and these notes show
  in blue.
- MusicXML: percussion parts are found from the score itself (percussion clef, unpitched notes, drum
  instruments), because music21 sometimes puts pitched parts on MIDI channel 10.
- Project files are now version 2 (they add `drum_slots` and the new song options). Version 1 files still open.

## 1.1.0
- Bundled the Audiveris sheet-music reader, with its own Java runtime.
- Clip volumes are evened out automatically.
- Windows installer (Inno Setup) and a portable zip.

## 1.0.0
- First release: note clips on a piano keyboard with pitch detection and auto-tune, and MIDI / MusicXML / audio /
  sheet-music import. Long notes are time-stretched without changing pitch, and collage videos come in two styles:
  a grid, or only the clips that are sounding.
