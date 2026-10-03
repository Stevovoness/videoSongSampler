# Video Sampler

Turn short video clips of single notes into a whole song, with a collage music video, like
*"Baby Ryan Sings Thunderstruck"*.

1. **Your sounds**: give each note (C4, C#4, B3, …) a video clip. Drop videos onto a piano keyboard, or use
   **Add clips** and the app works out which note each clip is and places it for you. Auto-tune puts every clip
   exactly in tune.
2. **The song**: open a **MIDI** file, **MusicXML** sheet music, an **audio recording** (mp3/wav/…; the notes are
   transcribed automatically with Spotify's *basic-pitch*), or a **PDF / image of sheet music** (needs the free
   [Audiveris](https://github.com/Audiveris/audiveris), bundled with the app). Pick the parts to play and change the key or speed.
   The app shows which notes have clips and warns about any that don't. You can let it borrow the nearest clip and
   pitch-shift it instead.
3. **Make the video**: pick a collage style and render an MP4:
   - **Collage grid**: every clip has a tile, and a tile lights up and plays when its note sounds.
   - **Only who's singing**: the screen splits between whichever clips are sounding right now.

### Cut clips from a long video
No need to record every note separately. Click **✂ Cut clips from a long video…** in Step 1 (or just add or drop
any video longer than 10 seconds) and the clip finder listens to the whole thing. It **suggests every clip point at
once**: each clearly sung or played note (often several takes of the same note) and each slap, clap or knock.
- For speeches and other talking, set **This video is: talking (faster)**. The app then only listens over the
  speaking-voice range, which is much quicker. It uses every core of your computer either way.
- Each video is analysed once: the result is saved, so opening the same video again (even in another project, or
  after restarting the app) shows its clip points straight away.
- They're shown on a timeline with the sound wave and the pitch line, and in a list. Untick the ones you don't
  want, drag a clip's edges to trim it, drag its middle to move it, or drag on an empty part of the timeline to
  cut your own.
- Pick the note or drum pad each clip becomes, click **♪ Hear it as in the video**, then **Add ticked clips**.
- The **sensitivity** slider shows more or fewer suggestions.

### Takes
A note (or drum pad) can have several clips, called takes. When the song plays that note again, the video uses the
next take, so the collage doesn't show the same moment every time. Pressing the key in Step 1 cycles through them
too. In the clip details, ◀ ▶ browse the takes, and you can make one the main take, remove one, or add another.
Several good takes of one note from the clip finder are added as takes automatically.

### Drums
Switch Step 1 to **🥁 Drums** to put percussive clips (slaps, claps, smacks, knocks…) on drum pads, one per drum
sound: kick, snare, hi-hat, toms, cymbals and the rest of the General MIDI kit. When a song has a drum part (a MIDI
channel-10 track, or a percussion part in MusicXML sheet music), its hits play your drum clips. Drum clips play
exactly as recorded: no auto-tune, no stretching. If the song uses a drum sound you have no clip for, a similar clip
can stand in (e.g. your crash for a ride cymbal). Drum tracks are switched on automatically once you've added a drum
clip, and the collage tiles are labelled with the drum name.

### Play your clips like an instrument
Click any key or pad to hear it exactly as it will sound in the video (trimmed, tuned and levelled), while the
clip's picture plays alongside. Or play with your computer keyboard:
- **Notes:** `A W S E D F T G Y H U J K O L P ;` play C up to the E an octave and a half above. `Z` / `X` move down / up an octave.
- **Drum pads:** `1 2 3 4`, `Q W E R`, `A S D F`, `Z X C V` play the 16 main pads.

### Octave jump
Song goes higher or lower than your clips? Tick **Octave jump** in Step 2 and a note with no clip plays the same
note from a clip one octave (or more) higher or lower, with no pitch-shifting, so it sounds natural. Octave-jumped
notes are shown in blue. Octave jump is tried before the nearest-clip pitch-shift option.

### Even volumes
While analysing each clip, the app measures how loud it is (only over the part where there's actually sound,
so a short squeal followed by silence measures correctly). When rendering, every clip is brought to the same
level. You can turn this off with **Even out clip volumes automatically** in Step 1, and the per-clip volume
slider still works on top of it.

### Quick previews
In Step 2, **▶ Listen with my clips** plays the song with your clips. By default it plays just the first 15
seconds, which is ready in a few seconds. Choose a longer preview or the whole song next to the button.
Replaying a preview you haven't changed is instant.

### Long notes keep their pitch
When a note lasts longer than its clip, the clip is **time-stretched without changing pitch**, using
[Rubber Band](https://breakfastquay.com/rubberband/) with formant preservation. The first ~70 ms (the attack) plays
untouched and the rest is stretched. The video uses the same time map (slowed, with frame blending), so the
picture stays in sync with the sound.

### Shorts, text and the command line
- Pick **Vertical / phone (1080×1920)** in Step 3 for YouTube Shorts, Instagram Reels and TikTok.
- Project files can hold **text on the video**: a hook line at the top, timed captions, a call to action at the
  end, and a watermark. Text stays clear of the buttons and captions those apps draw over the video. A project
  can also play an **outro** video after the song, and play only **part of the song** (`start_s` / `end_s`).
- The **command line** builds and renders without the window:
  ```
  python main.py analyse speech.mp4                      # every note and hit the clip finder hears (JSON)
  python main.py auto --videos speech.mp4 --song tune.mid --preset shorts --overlays text.json --out short.mp4
  python main.py render my_song.vsproj out.mp4
  ```
  Add or change the text of a finished video without rendering it again:
  `python main.py text --video plain.mp4 --overlays text.json --out final.mp4`.
  `auto` picks the clips, the takes and the key by itself, plays every part of the song (`--melody-only` for
  just the tune), and writes `short.vsproj` and `short.report.json`
  (how much of the song the clips cover, and where every clip came from). Run `python main.py auto --help` for
  every option. The same works on a Linux server: see `Dockerfile` and [docs/autopilot.md](docs/autopilot.md).

### The automation (daily Shorts, with a review page)
`python -m autopilot run` makes today's "<person> sings <song>" video from your ideas list and opens a review
page: watch it without text, edit the text it wrote, check the final video, approve. Set-up and details:
[docs/autopilot.md](docs/autopilot.md) (`pip install -r requirements-autopilot.txt` first).

## Download and run (Windows 10/11, 64-bit)

Go to [**Releases**](https://github.com/Stevovoness/videoSongSampler/releases/latest) and download one of:

- **`VideoSampler-Setup-x.y.z.exe`**: installer. Double-click it, then start *Video Sampler* from the Start menu.
  No admin rights needed.
- **`VideoSampler-x.y.z-Windows-portable.zip`**: no install. Unzip it, open the `VideoSampler` folder and run
  `VideoSampler.exe`. Keep the folder somewhere with a short path (e.g. `C:\Apps`), not deep inside other folders.

Everything is included: Python, FFmpeg, Rubber Band, the basic-pitch note-transcription model and Audiveris with
its own Java runtime. You don't need to install anything else.

Windows SmartScreen may warn about an unrecognised app the first time, because the download isn't code-signed.
Click **More info → Run anyway**.

Render a saved project from the command line: `VideoSampler.exe --render my_song.vsproj out.mp4`.

**From source (Python 3.11):**
```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --no-deps basic-pitch==0.4.0
.venv\Scripts\python setup_tools.py      # downloads Rubber Band + Audiveris into tools/
.venv\Scripts\python main.py
```
Always run it with the `.venv` Python, as above. In VS Code, `.vscode/settings.json` already selects it for the
Run button. A plain `python main.py` uses whichever Python is installed system-wide, which won't have the app's
packages.

## Build a release
```
winget install JRSoftware.InnoSetup                # once, for the installer
.venv\Scripts\python build_exe.py
```
This writes `release\VideoSampler-Setup-<version>.exe` and `release\VideoSampler-<version>-Windows-portable.zip`.
It's a one-folder PyInstaller build: Audiveris and its Java runtime add ~160 MB, which a single-file exe
would have to unpack on every launch. PyInstaller doesn't support Python 3.10.0, so use 3.11.

To publish, attach both files to a GitHub release:
```
gh release create v<version> release\* --title "Video Sampler <version>" --notes "..."
```

## Documentation
- [docs/architecture.md](docs/architecture.md): how the code fits together
- [docs/releasing.md](docs/releasing.md): building and publishing a release
- [docs/CHANGELOG.md](docs/CHANGELOG.md): what changed in each version
- [docs/TODO.md](docs/TODO.md): the to-do list, ticked off as work is done
- [docs/autopilot.md](docs/autopilot.md): the daily Shorts automation and custom-video service (design and status)
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md): rules for AI agents. **After every change: run the tests,
  update the docs and changelog, tick off `docs/TODO.md`, then commit and push to git.**

## Tests
```
.venv\Scripts\python -m pytest tests
```

## Project layout
| Path | What it does |
|---|---|
| `vsampler/clips.py` | Decodes clips, detects pitch (pYIN), auto-trims silence, stores frames |
| `vsampler/audio_dsp.py` | Stretches notes without changing pitch and pitch-shifts them (Rubber Band, librosa fallback) |
| `vsampler/importers/` | MIDI, MusicXML, audio (basic-pitch) and sheet-music (Audiveris) importers |
| `vsampler/planner.py` | Maps song notes and drum hits to clips: octave jumps, nearest-clip borrowing, drum stand-ins |
| `vsampler/drums.py` | General MIDI drum names, the drum pad layout and drum families |
| `vsampler/render/` | Collage layouts, frame compositor, text overlays, MP4 encoder (PyAV / x264 + AAC) |
| `vsampler/auto.py`, `vsampler/cli.py` | Building a project with no one at the controls; the command line |
| `autopilot/` | The daily automation and its review page (see `docs/autopilot.md`) |
| `vsampler/gui/` | PySide6 interface (piano keyboard, drum pads, low-latency sample pad) |

## Licences
Bundled third-party tools, redistributed unmodified:
- Rubber Band (`tools/rubberband`): GPL-2.0-or-later. See `tools/rubberband/COPYING.txt`.
- Audiveris (in the release builds): AGPL-3.0, source at https://github.com/Audiveris/audiveris.
- Anton font (`assets/fonts`): SIL Open Font License 1.1. See `assets/fonts/OFL.txt`.
