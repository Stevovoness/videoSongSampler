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

### Even volumes
While analysing each clip, the app measures how loud it is (only over the part where there's actually sound,
so a short squeal followed by silence measures correctly). When rendering, every clip is brought to the same
level. You can turn this off with **Even out clip volumes automatically** in Step 1, and the per-clip volume
slider still works on top of it.

### Long notes keep their pitch
When a note lasts longer than its clip, the clip is **time-stretched without changing pitch**, using
[Rubber Band](https://breakfastquay.com/rubberband/) with formant preservation. The first ~70 ms (the attack) plays
untouched and the rest is stretched. The video uses the same time map (slowed, with frame blending), so the
picture stays in sync with the sound.

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
| `vsampler/planner.py` | Maps song notes to clips: missing notes and nearest-clip borrowing |
| `vsampler/render/` | Collage layouts, frame compositor, MP4 encoder (PyAV / x264 + AAC) |
| `vsampler/gui/` | PySide6 interface |

## Licences
Bundled third-party tools, redistributed unmodified:
- Rubber Band (`tools/rubberband`): GPL-2.0-or-later. See `tools/rubberband/COPYING.txt`.
- Audiveris (in the release builds): AGPL-3.0, source at https://github.com/Audiveris/audiveris.
