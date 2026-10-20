"""Frame loop: stills, raw frame streams and MP4 rendering with progress and cancel."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codemotion import __version__
from codemotion.errors import UserFacingError
from codemotion.pipeline import PreparedScene
from codemotion.render.canvas import SkiaCanvas
from codemotion.render.encoder import (
    ExportFormat,
    FFmpegEncoder,
    export_video,
)
from codemotion.render.encoder import RenderCancelledError as RenderCancelledError
from codemotion.scenes.base import Scene
from codemotion.scenes.compose import build_scene
from codemotion.script.doc import SceneDoc


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    elapsed: float  # seconds
    eta: float | None  # seconds remaining, None until measurable
    stage: str = "frames"  # "frames", then "gif" / "webm" while exporting

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 1.0


ProgressCallback = Callable[[Progress], None]
FrameSink = Callable[[memoryview], None]


def frame_at(prepared: PreparedScene, t: float) -> int:
    duration = prepared.doc.duration
    if not 0 <= t <= duration:
        raise UserFacingError(f"Time t must be between 0 and {duration:g} seconds.", field="t")
    return min(round(t * prepared.fps), prepared.total_frames - 1)


def render_still(prepared: PreparedScene, t: float) -> SkiaCanvas:
    return StillRenderer(prepared).render(t)


class StillRenderer:
    """Draws stills of one scene, building it once (for scrubbing in the desktop app).

    Not thread-safe: use it from one thread at a time. ``render`` reuses one canvas.
    """

    def __init__(self, prepared: PreparedScene) -> None:
        self.prepared = prepared
        self._scene: Scene | None = None
        self._canvas: SkiaCanvas | None = None

    def render(self, t: float) -> SkiaCanvas:
        if self._scene is None or self._canvas is None:
            self._scene = build_scene(self.prepared)
            self._canvas = SkiaCanvas(self.prepared.width, self.prepared.height)
        self._scene.draw(self._canvas, frame_at(self.prepared, t))
        return self._canvas


def render_frames(
    prepared: PreparedScene,
    sink: FrameSink,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    workers: int | None = None,
    worker_memory: Callable[[int], None] | None = None,
) -> int:
    """Draw every frame in order and hand its RGBA bytes to ``sink``; returns frames drawn.

    ``workers`` > 1 draws in that many processes (``None``: decide from the render's size,
    see ``parallel.auto_workers``); the frames are identical either way. Memory stays flat
    regardless of duration (SPEC N8). ``worker_memory`` receives the workers' total peak
    memory in bytes after a multiprocess render.
    """
    from codemotion.render import parallel  # imports multiprocessing only when rendering

    count = parallel.auto_workers(prepared) if workers is None else max(1, workers)
    if count > 1:
        return _render_parallel(prepared, sink, progress, cancel, count, worker_memory)
    scene = build_scene(prepared)
    canvas = SkiaCanvas(prepared.width, prepared.height)
    total = prepared.total_frames
    started = time.monotonic()
    for frame in range(total):
        if cancel is not None and cancel.cancelled:
            raise RenderCancelledError
        scene.draw(canvas, frame)
        sink(canvas.pixels())
        if progress is not None:
            elapsed = time.monotonic() - started
            done = frame + 1
            eta = elapsed / done * (total - done) if done >= 3 else None
            progress(Progress(done, total, elapsed, eta))
    return total


def _render_parallel(
    prepared: PreparedScene,
    sink: FrameSink,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
    workers: int,
    worker_memory: Callable[[int], None] | None,
) -> int:
    from codemotion.render.parallel import render_frames_parallel

    total = prepared.total_frames
    started = time.monotonic()

    def on_frame(done: int) -> None:
        if progress is not None:
            elapsed = time.monotonic() - started
            eta = elapsed / done * (total - done) if done >= 3 else None
            progress(Progress(done, total, elapsed, eta))

    def cancelled() -> bool:
        return cancel is not None and cancel.cancelled

    stats = render_frames_parallel(prepared, sink, workers, on_frame, cancelled)
    if worker_memory is not None:
        worker_memory(stats.worker_peak_memory)
    return total


def default_output(stem: str, doc: SceneDoc, preview: bool, folder: Path) -> Path:
    """A new, unused ``folder/{stem}-{aspect}[-preview]-{timestamp}.mp4`` path."""
    tag = doc.aspect.value.replace(":", "x") + ("-preview" if preview else "")
    base = f"{stem}-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = folder / f"{base}.mp4"
    n = 2
    while path.exists():
        path = folder / f"{base}-{n}.mp4"
        n += 1
    return path


def sidecar_path(video: Path) -> Path:
    return video.with_suffix(".json")


def write_sidecar(
    video: Path,
    prepared: PreparedScene,
    source: str | None = None,
    warnings: list[str] | None = None,
    exports: list[Path] | None = None,
) -> Path:
    """Settings used for a render, next to the video (SPEC F15)."""
    data = {
        "codemotion": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "video": video.name,
        "exports": [path.name for path in exports or []],
        "source": source,
        "preview": prepared.preview,
        "frames": prepared.total_frames,
        "fps": prepared.fps,
        "size": [prepared.width, prepared.height],
        "scene": prepared.doc.model_dump(mode="json", by_alias=True),
        "warnings": warnings if warnings is not None else list(prepared.warnings),
    }
    path = sidecar_path(video)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def render_video(
    prepared: PreparedScene,
    output: Path,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    source: str | None = None,
    warnings: list[str] | None = None,
    formats: Sequence[ExportFormat] = (),
    workers: int | None = None,
    worker_memory: Callable[[int], None] | None = None,
) -> Path:
    """Render to MP4 at ``output`` (atomically) plus a JSON sidecar; returns the video path.

    ``formats`` also converts the MP4 to GIF and/or WebM next to it. Cancelling removes
    everything this render wrote.
    """
    soundtrack = _soundtrack(prepared, output)
    try:
        with FFmpegEncoder(output, prepared.width, prepared.height, prepared.fps,
                           soundtrack) as encoder:  # fmt: skip
            render_frames(prepared, encoder.write, progress, cancel, workers, worker_memory)
            video = encoder.finish()
    finally:
        if soundtrack is not None:
            soundtrack.unlink(missing_ok=True)
    exports: list[Path] = []
    try:
        for fmt in dict.fromkeys(formats):
            exports.append(_export(prepared, video, fmt, progress, cancel))
    except BaseException:
        for path in (video, *exports):
            path.unlink(missing_ok=True)
        raise
    try:
        write_sidecar(video, prepared, source, warnings, exports)
    except OSError as exc:
        raise UserFacingError(f"Could not write the settings file: {exc.strerror}.") from None
    return video


def _soundtrack(prepared: PreparedScene, output: Path) -> Path | None:
    """Mix the scene's sound cues into a temporary WAV next to ``output`` (None: silent)."""
    if not prepared.doc.sounds:
        return None
    from codemotion.render.audio import mix, write_wav

    output.parent.mkdir(parents=True, exist_ok=True)
    path = output.with_name(f".{output.stem}.audio.wav")
    try:
        return write_wav(path, mix(prepared.doc))
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise UserFacingError(f"Could not write the soundtrack: {exc.strerror}.") from None


def _export(
    prepared: PreparedScene,
    video: Path,
    fmt: ExportFormat,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
) -> Path:
    started = time.monotonic()
    total = prepared.total_frames

    def report(fraction: float) -> None:
        if progress is not None:
            progress(Progress(round(fraction * total), total, time.monotonic() - started,
                              None, stage=fmt))  # fmt: skip

    def cancelled() -> bool:
        return cancel is not None and cancel.cancelled

    size = (prepared.width, prepared.height)
    return export_video(video, fmt, size, prepared.doc.duration, report, cancelled)
