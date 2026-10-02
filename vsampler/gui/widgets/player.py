"""Tiny audio player for previews."""
from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class AudioPlayer(QObject):
    position = Signal(float)   # seconds
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self)
        self.out.setVolume(0.9)
        self.player.setAudioOutput(self.out)
        self.player.positionChanged.connect(lambda ms: self.position.emit(ms / 1000.0))
        self.player.playbackStateChanged.connect(self._state)

    def _state(self, s) -> None:
        if s == QMediaPlayer.StoppedState:
            self.stopped.emit()

    def play(self, path: str) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState
