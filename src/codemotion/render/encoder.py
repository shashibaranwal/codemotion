"""Stream raw RGBA frames into the bundled ffmpeg (SPEC 6.7), and convert MP4 to GIF/WebM.

Output goes to a temp file next to the target and is renamed into place only on success;
it is deleted on failure or cancel.
"""

from __future__ import annotations

import collections
import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Literal

from codemotion import config
from codemotion.errors import UserFacingError

ExportFormat = Literal["gif", "webm"]
EXPORT_FORMATS: tuple[ExportFormat, ...] = ("gif", "webm")


class RenderCancelledError(Exception):
    """Raised when the cancel token is set during rendering or export."""


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError):
        raise UserFacingError(
            "ffmpeg could not be found. Reinstall CodeMotion with 'uv sync'."
        ) from None


def mp4_command(
    exe: str, width: int, height: int, fps: int, path: Path, audio: Path | None = None
) -> list[str]:
    """Raw RGBA frames on stdin -> H.264 MP4; ``audio`` (a WAV as long as the video) is
    muxed in as AAC."""
    if audio is None:
        sound = ["-an"]
    else:
        sound = ["-i", str(audio), "-map", "0:v", "-map", "1:a",
                 "-c:a", "aac", "-b:a", config.AUDIO_AAC_BITRATE]  # fmt: skip
    return [
        exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        *sound,
        "-c:v",
        "libx264",
        "-preset",
        config.X264_PRESET,
        "-crf",
        str(config.X264_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(path),
    ]


def temp_path_for(path: Path) -> Path:
    return path.with_name(f".{path.stem}.partial{path.suffix}")


class FFmpegEncoder:
    """Context manager: ``write()`` frames, then ``finish()``; any exception aborts."""

    def __init__(
        self, path: Path, width: int, height: int, fps: int, audio: Path | None = None
    ) -> None:
        self.path = path
        self.audio = audio
        self.temp = temp_path_for(path)
        self.width = width
        self.height = height
        self.fps = fps
        self.frames_written = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=200)
        self._reader: threading.Thread | None = None

    def __enter__(self) -> FFmpegEncoder:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = subprocess.Popen(
                mp4_command(ffmpeg_exe(), self.width, self.height, self.fps, self.temp, self.audio),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=flags,
            )
        except OSError as exc:
            raise UserFacingError(f"ffmpeg could not be started: {exc.strerror}.") from None
        assert self._proc.stderr is not None
        self._reader = threading.Thread(target=self._drain, args=(self._proc.stderr,), daemon=True)
        self._reader.start()
        return self

    def _drain(self, stream: IO[bytes]) -> None:
        for raw in stream:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def _failure(self, what: str) -> UserFacingError:
        if self._reader is not None:
            self._reader.join(timeout=5)
        tail = [line for line in self._stderr if line][-config.FFMPEG_STDERR_LINES :]
        detail = ("\n" + "\n".join(tail)) if tail else ""
        return UserFacingError(f"{what}{detail}")

    def write(self, frame: memoryview | bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None, "encoder not started"
        try:
            self._proc.stdin.write(frame)
        except (BrokenPipeError, OSError):
            self._proc.wait()
            raise self._failure("Video encoding failed (ffmpeg stopped early).") from None
        self.frames_written += 1

    def finish(self) -> Path:
        assert self._proc is not None and self._proc.stdin is not None, "encoder not started"
        with contextlib.suppress(OSError):
            self._proc.stdin.close()
        code = self._proc.wait()
        if code != 0:
            raise self._failure(f"Video encoding failed (ffmpeg exit code {code}).")
        try:
            os.replace(self.temp, self.path)
        except OSError as exc:
            raise UserFacingError(f"Could not save {self.path}: {exc.strerror}.") from None
        return self.path

    def abort(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        if self._proc is not None and self._proc.stdin is not None:
            with contextlib.suppress(OSError):
                self._proc.stdin.close()
        self.temp.unlink(missing_ok=True)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.abort()


# --------------------------------------------------------------------------- GIF / WebM


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def export_command(exe: str, source: Path, target: Path, fmt: ExportFormat,
                   size: tuple[int, int],
                   gif_short_edge: int = config.GIF_SHORT_EDGE) -> list[str]:  # fmt: skip
    """ffmpeg arguments converting the MP4 ``source`` to ``target`` (GIF or WebM)."""
    head = [exe, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1", "-i", str(source)]  # fmt: skip
    if fmt == "gif":
        width, height = size
        ratio = gif_short_edge / min(width, height)
        scale = f"scale={_even(width * ratio)}:{_even(height * ratio)}:flags=lanczos"
        graph = (
            f"fps={config.GIF_FPS},{scale},split[a][b];"
            "[a]palettegen=stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
        )
        # GIFs are silent: drop any audio.
        return [*head, "-an", "-filter_complex", graph, "-loop", "0", "-f", "gif", str(target)]
    return [*head, "-c:v", "libvpx-vp9", "-crf", str(config.WEBM_CRF), "-b:v", "0",
            "-row-mt", "1", "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p",
            "-c:a", "libopus", "-b:a", config.AUDIO_OPUS_BITRATE,  # if the MP4 has sound
            "-f", "webm", str(target)]  # fmt: skip


def export_video(
    source: Path,
    fmt: ExportFormat,
    size: tuple[int, int],
    duration: float,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    gif_short_edge: int = config.GIF_SHORT_EDGE,
) -> Path:
    """Convert the MP4 ``source`` into ``source`` with a .gif/.webm suffix; returns it.

    ``progress`` gets the fraction done (0-1); ``cancelled`` is polled and stops the
    conversion with ``RenderCancelledError``.
    """
    target = source.with_suffix(f".{fmt}")
    temp = temp_path_for(target)
    command = export_command(ffmpeg_exe(), source, temp, fmt, size, gif_short_edge)
    return _convert(command, temp, target, duration, f"{fmt.upper()} export", progress, cancelled)


def _convert(
    command: list[str],
    temp: Path,
    target: Path,
    duration: float,
    what: str,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Run an ffmpeg ``command`` writing ``temp`` (with ``-progress pipe:1``), then move it
    to ``target``. ``duration`` (seconds) turns ffmpeg's progress into a fraction."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
    except OSError as exc:
        raise UserFacingError(f"ffmpeg could not be started: {exc.strerror}.") from None
    stdout, stderr = proc.stdout, proc.stderr
    assert stdout is not None and stderr is not None
    errors: collections.deque[str] = collections.deque(maxlen=200)
    reader = threading.Thread(
        target=lambda: errors.extend(r.decode("utf-8", "replace").rstrip() for r in stderr),
        daemon=True,
    )
    reader.start()
    stopped = False
    try:
        for raw in stdout:
            if cancelled is not None and cancelled():
                stopped = True
                proc.kill()
                break
            key, _, value = raw.decode("ascii", "replace").strip().partition("=")
            if key == "out_time_us" and progress is not None and duration > 0:
                with contextlib.suppress(ValueError):
                    progress(min(1.0, max(0.0, int(value) / 1e6 / duration)))
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        temp.unlink(missing_ok=True)
        raise
    reader.join(timeout=5)
    if stopped or (cancelled is not None and cancelled()):
        temp.unlink(missing_ok=True)
        raise RenderCancelledError
    if code != 0:
        temp.unlink(missing_ok=True)
        tail = [line for line in errors if line][-config.FFMPEG_STDERR_LINES :]
        detail = ("\n" + "\n".join(tail)) if tail else ""
        raise UserFacingError(f"{what} failed (ffmpeg exit code {code}).{detail}")
    try:
        os.replace(temp, target)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise UserFacingError(f"Could not save {target}: {exc.strerror}.") from None
    if progress is not None:
        progress(1.0)
    return target


# --------------------------------------------------------------------------- imported videos


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float  # seconds


def probe_video(path: Path) -> VideoInfo:
    """Size, frame rate and length of the video at ``path``."""
    import imageio_ffmpeg

    try:
        reader = imageio_ffmpeg.read_frames(str(path))
        meta = next(reader)
        reader.close()
        width, height = meta["size"]
        return VideoInfo(int(width), int(height), float(meta["fps"]), float(meta["duration"]))
    except (OSError, RuntimeError, StopIteration, KeyError, TypeError, ValueError):
        raise UserFacingError(f"{path.name} isn't a video ffmpeg can read.") from None


def import_command(exe: str, source: Path, target: Path) -> list[str]:
    """ffmpeg arguments re-encoding any video ``source`` as a CodeMotion-style MP4 (H.264,
    even size, yuv420p, AAC sound if it has any) so every player can show it."""
    return [exe, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
            "-progress", "pipe:1", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", str(config.X264_CRF),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", config.AUDIO_AAC_BITRATE,
            "-movflags", "+faststart", "-f", "mp4", str(target)]  # fmt: skip


def import_video(
    source: Path,
    target: Path,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> VideoInfo:
    """Re-encode the video a program wrote (any format ffmpeg reads) to the MP4 ``target``."""
    duration = probe_video(source).duration
    temp = temp_path_for(target)
    command = import_command(ffmpeg_exe(), source, temp)
    _convert(command, temp, target, duration, "Importing the video", progress, cancelled)
    return probe_video(target)
