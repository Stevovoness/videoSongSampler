"""Song importers. `load_song` picks the right one from the file extension."""
from __future__ import annotations

import os

from ..models import Song, SongOptions

MIDI_EXT = {".mid", ".midi", ".kar"}
XML_EXT = {".musicxml", ".xml", ".mxl"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".mp4", ".mov", ".webm"}
SHEET_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

FILE_FILTER = (
    "All songs (*.mid *.midi *.kar *.musicxml *.xml *.mxl *.mp3 *.wav *.flac *.ogg *.m4a *.aac *.mp4 *.mov "
    "*.webm *.pdf *.png *.jpg *.jpeg *.tif *.tiff);;"
    "MIDI (*.mid *.midi *.kar);;MusicXML sheet music (*.musicxml *.xml *.mxl);;"
    "Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.mp4 *.mov *.webm);;"
    "PDF / image sheet music (*.pdf *.png *.jpg *.jpeg *.tif *.tiff)"
)


def song_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in MIDI_EXT:
        return "midi"
    if ext in XML_EXT:
        return "musicxml"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in SHEET_EXT:
        return "sheet"
    raise ValueError(f"Unsupported song file type: {ext}")


def load_song(path: str, opts: SongOptions, audiveris: str | None = None) -> Song:
    kind = song_kind(path)
    if kind == "midi":
        from .midi_import import load_midi
        return load_midi(path)
    if kind == "musicxml":
        from .musicxml_import import load_musicxml
        return load_musicxml(path)
    if kind == "audio":
        from .audio_import import load_audio_song
        return load_audio_song(path, opts.audio_onset_threshold, opts.audio_min_note_ms, opts.audio_melody_only)
    from .omr_import import load_sheet
    return load_sheet(path, audiveris)
