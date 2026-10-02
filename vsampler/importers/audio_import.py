"""Audio song (mp3/wav/...) -> Song using Spotify's basic-pitch transcription model."""
from __future__ import annotations

import logging
import os
import tempfile

import numpy as np
import soundfile as sf

from ..models import Song
from .midi_import import song_from_pretty_midi


def _to_wav(path: str) -> str:
    """Decode any audio/video file to a temporary mono wav (handles mp3, m4a, mp4 ...)."""
    from ..clips import load_audio

    audio = load_audio(path, sr=22050)
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(tmp, audio.mean(axis=0).astype(np.float32), 22050)
    return tmp


def load_audio_song(path: str, onset_threshold: float = 0.5, min_note_ms: float = 120.0,
                    melody_only: bool = True) -> Song:
    logging.getLogger().setLevel(logging.ERROR)  # basic-pitch warns about unused backends
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import Model, predict

    from ..paths import is_frozen, resource_dir

    model_path = ICASSP_2022_MODEL_PATH
    if is_frozen():
        model_path = resource_dir() / "basic_pitch" / "saved_models" / "icassp_2022" / "nmp.onnx"
    wav = _to_wav(path)
    try:
        _, pm, _ = predict(
            wav,
            Model(model_path),
            onset_threshold=onset_threshold,
            frame_threshold=0.3,
            minimum_note_length=min_note_ms,
            minimum_frequency=60.0,
            maximum_frequency=2100.0,
            melodia_trick=True,
        )
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    song = song_from_pretty_midi(pm, path, "audio", ["Transcribed melody"])
    if melody_only:
        from ..songops import skyline
        song.events = skyline(song.events)
    return song
