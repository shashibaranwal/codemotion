"""Preview panel: a still at the scrubber's time, or a rendered video in the player."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codemotion import config


class FrameView(QWidget):
    """Draws a frame scaled to fit, keeping its aspect ratio."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: QImage | None = None
        self.placeholder = "Run the script (Ctrl+Enter) to see a preview."
        self.setMinimumSize(QSize(160, 160))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, image: QImage | None) -> None:
        self.image = image
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(config.UI_COLORS["base"]))
        if self.image is None or self.image.isNull():
            painter.setPen(QColor(config.UI_COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
        else:
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - size.width()) / 2
            y = (self.height() - size.height()) / 2
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(x, y, size.width(), size.height()), self.image)
        painter.end()


class PreviewPanel(QWidget):
    """Stills while editing; the player after a quick preview or render.

    In still mode the scrubber emits ``time_changed``; in player mode it seeks the video.
    """

    time_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.duration = 0.0
        self.video: Path | None = None
        self._player: QMediaPlayer | None = None  # created on first play
        self._video_widget: QVideoWidget | None = None
        self._audio: QAudioOutput | None = None

        self.frame = FrameView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.frame)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._on_slider)
        self.time_label = QLabel("t = 0.00 s")
        self.time_label.setMinimumWidth(110)
        self.play_button = QToolButton()
        self.play_button.setText("▶")
        self.play_button.setToolTip("Play / pause the video")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_play)
        self.stills_button = QToolButton()
        self.stills_button.setText("Stills")
        self.stills_button.setToolTip("Back to the live preview of the script")
        self.stills_button.setEnabled(False)
        self.stills_button.clicked.connect(self.show_stills)

        row = QHBoxLayout()
        row.addWidget(self.time_label)
        row.addWidget(self.slider, 1)
        row.addWidget(self.play_button)
        row.addWidget(self.stills_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack, 1)
        layout.addLayout(row)

    # -- stills --------------------------------------------------------------------------

    @property
    def in_player(self) -> bool:
        return self.stack.currentWidget() is not self.frame

    @property
    def time(self) -> float:
        return self.slider.value() * config.SCRUB_STEP_SECONDS

    def set_duration(self, duration: float) -> None:
        """Set the scrubber's range; keeps the current time when it still fits."""
        self.duration = duration
        if not self.in_player:
            self._set_range(duration)
        self.slider.setEnabled(duration > 0)

    def set_time(self, t: float) -> None:
        self.slider.setValue(round(t / config.SCRUB_STEP_SECONDS))

    def show_image(self, image: QImage) -> None:
        self.frame.set_image(image)

    def show_stills(self) -> None:
        if self._player is not None:
            self._player.stop()
        self.stack.setCurrentWidget(self.frame)
        self.play_button.setEnabled(False)
        self.play_button.setText("▶")
        self.stills_button.setEnabled(False)
        self._set_range(self.duration)
        self.time_changed.emit(self.time)

    # -- player --------------------------------------------------------------------------

    def play(self, video: Path) -> None:
        """Show and play ``video`` in the embedded player."""
        if self._player is None or self._video_widget is None:
            self._video_widget = QVideoWidget()
            self.stack.addWidget(self._video_widget)
            player = QMediaPlayer(self)
            player.setVideoOutput(self._video_widget)
            self._audio = QAudioOutput(self)  # renders with sound effects play them
            player.setAudioOutput(self._audio)
            player.durationChanged.connect(self._on_video_duration)
            player.positionChanged.connect(self._on_video_position)
            player.playbackStateChanged.connect(self._on_playback_state)
            self._player = player
        self.video = video
        self.stack.setCurrentWidget(self._video_widget)
        self.play_button.setEnabled(True)
        self.stills_button.setEnabled(True)
        self._player.setSource(QUrl.fromLocalFile(str(video)))
        self._player.play()

    def release(self) -> None:
        """Stop the player and let go of the video file (so it can be deleted)."""
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())

    def toggle_play(self) -> None:
        player = self._player
        if player is None or not self.in_player:
            return
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()

    def _on_video_duration(self, ms: int) -> None:
        self._set_range(ms / 1000)

    def _on_video_position(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.set_time(ms / 1000)
            self.slider.blockSignals(False)
            self._show_time()

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("❚❚" if playing else "▶")

    # -- shared --------------------------------------------------------------------------

    def _set_range(self, duration: float) -> None:
        steps = max(0, round(duration / config.SCRUB_STEP_SECONDS))
        self.slider.blockSignals(True)
        self.slider.setRange(0, steps)
        self.slider.blockSignals(False)
        self._show_time()

    def _on_slider(self, value: int) -> None:
        self._show_time()
        if self.in_player:
            if self._player is not None:
                self._player.setPosition(round(self.time * 1000))
        else:
            self.time_changed.emit(self.time)

    def _show_time(self) -> None:
        self.time_label.setText(f"t = {self.time:.2f} s")
