# Video Sampler

Turn short video clips of single notes into a whole song, with a collage music video, like
*"Baby Ryan Sings Thunderstruck"*.

1. **Your sounds**: give each note (C4, C#4, B3, …) a video clip. Drop videos onto a piano keyboard, or use
   **Add clips** and the app works out which note each clip is and places it for you. Auto-tune puts every clip
   exactly in tune.
2. **The song**: open a **MIDI** file, **MusicXML** sheet music, an **audio recording** (mp3/wav/…; the notes are
   transcribed automatically with Spotify's *basic-pitch*), or a **PDF / image of sheet music** (needs the free
   [Audiveris](https://github.com/Audiveris/audiveris/releases)). Pick the parts to play and change the key or speed.
   The app shows which notes have clips and warns about any that don't. You can let it borrow the nearest clip and
   pitch-shift it instead.
3. **Make the video**: pick a collage style and render an MP4:
   - **Collage grid**: every clip has a tile, and a tile lights up and plays when its note sounds.
   - **Only who's singing**: the screen splits between whichever clips are sounding right now.

### Long notes keep their pitch
When a note lasts longer than its clip, the clip is **time-stretched without changing pitch**, using
[Rubber Band](https://breakfastquay.com/rubberband/) with formant preservation. The first ~70 ms (the attack) plays
untouched and the rest is stretched. The video uses the same time map (slowed, with frame blending), so the
picture stays in sync with the sound.

## Run the app

**Windows exe:** run `VideoSampler.exe`. You don't need to install anything else, except Audiveris if you want to
read PDF or image sheet music.

**From source (Python 3.11):**
```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --no-deps basic-pitch==0.4.0
.venv\Scripts\python setup_tools.py      # downloads Rubber Band into tools/
.venv\Scripts\python main.py
```

## Build the self-contained exe
```
.venv\Scripts\python build_exe.py            # dist\VideoSampler.exe (single file)
.venv\Scripts\python build_exe.py --onedir   # dist\VideoSampler\ (folder, starts faster)
```
PyInstaller does not support Python 3.10.0. Use 3.11.

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
Rubber Band (bundled in `tools/rubberband`) is GPL. See `tools/rubberband/COPYING.txt`.
