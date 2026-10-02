# Changelog

Every change gets a line here (see the workflow in [CLAUDE.md](../CLAUDE.md)). New entries go under
**Unreleased** until a version is released.

## Unreleased
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
