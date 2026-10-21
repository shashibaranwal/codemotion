"""Background work for the desktop app: script runs, stills and renders off the UI thread.

Results come back to the UI thread through queued Qt signals: a worker thread only ever
emits a private signal connected to a slot of an object living on the UI thread, which
then emits the public signal there.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage

from codemotion.models import ScenePlan
from codemotion.pipeline import PreparedScene, prepare_scene, scene_plan
from codemotion.render.encoder import ExportFormat
from codemotion.render.renderer import (
    CancelToken,
    Progress,
    RenderCancelledError,
    StillRenderer,
    render_video,
)
from codemotion.script.doc import SceneDoc
from codemotion.script.program import make_video
from codemotion.script.source import Loaded, SourceMode, load_text

Job = Callable[[], object]

_PROGRESS_INTERVAL = 0.05  # seconds between progress updates sent to the UI


class LatestWins(QObject):
    """Runs one job at a time on a worker thread.

    A job submitted while another is running waits; a newer submission replaces it, so a
    burst of requests (typing, scrubbing) runs only the current job and the latest one.
    """

    finished = Signal(object)  # the job's result
    failed = Signal(object)  # the exception it raised
    _done = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._pending: Job | None = None
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def submit(self, job: Job) -> None:
        if self._thread is not None:
            self._pending = job
        else:
            self._start(job)

    def wait(self, timeout: float | None = None) -> None:
        """Block until the running job's thread ends (its result is still delivered later)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _start(self, job: Job) -> None:
        def work() -> None:
            try:
                result = job()
            except Exception as exc:
                self._done.emit(None, exc)
            else:
                self._done.emit(result, None)

        self._thread = threading.Thread(target=work, name="codemotion-worker", daemon=True)
        self._thread.start()

    def _on_done(self, result: object, error: object) -> None:
        self._thread = None
        if error is not None:
            self.failed.emit(error)
        else:
            self.finished.emit(result)
        if self._pending is not None and self._thread is None:
            job, self._pending = self._pending, None
            self._start(job)


# --------------------------------------------------------------------------- script runs


@dataclass(frozen=True)
class RunRequest:
    text: str
    base_dir: Path
    mode: SourceMode = SourceMode.auto
    code_options: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunResult:
    request: RunRequest
    loaded: Loaded
    prepared: PreparedScene  # preview quality, for stills
    plan: ScenePlan
    seconds: float
    stills: StillRenderer = field(repr=False)


def run_source(request: RunRequest) -> RunResult:
    """Run the script (or build the code card) and prepare it for preview stills."""
    started = time.monotonic()
    loaded = load_text(request.text, request.base_dir, request.mode, request.code_options)
    prepared = prepare_scene(loaded.doc, preview=True)
    plan = scene_plan(prepared, loaded.warnings)
    return RunResult(
        request=request,
        loaded=loaded,
        prepared=prepared,
        plan=plan,
        seconds=time.monotonic() - started,
        stills=StillRenderer(prepared),
    )


def still_image(stills: StillRenderer, t: float) -> QImage:
    """Render the still at ``t`` as a QImage (safe to build off the UI thread)."""
    canvas = stills.render(t)
    data = bytes(canvas.pixels())
    image = QImage(
        data, canvas.width, canvas.height, canvas.width * 4, QImage.Format.Format_RGBA8888
    )
    return image.copy()  # detach from ``data``


# --------------------------------------------------------------------------- renders


@dataclass(frozen=True)
class RenderJob:
    doc: SceneDoc
    output: Path
    preview: bool
    source: str | None = None
    warnings: list[str] = field(default_factory=list)
    formats: tuple[ExportFormat, ...] = ()  # also export GIF / WebM


@dataclass(frozen=True)
class ProgramJob:
    """Run a video program (see ``script.program``) and import its video as ``output``."""

    source: str
    output: Path
    preview: bool  # quick runs go to a temp folder and aren't listed with the renders
    script_path: Path | None = None
    formats: tuple[ExportFormat, ...] = ()


class RenderWorker(QObject):
    """Renders one video at a time with progress and cancel: a scene (``RenderJob``) or
    a video program's own video (``ProgramJob``)."""

    progress = Signal(object)  # Progress
    output = Signal(str)  # a line a video program printed
    finished = Signal(object)  # (RenderJob, Path of the video) | (ProgramJob, ProgramResult)
    failed = Signal(object)  # (job, exception)
    cancelled = Signal(object)  # the job
    _progress = Signal(object)
    _output = Signal(str)
    _done = Signal(object, object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._token = CancelToken()
        self.job: RenderJob | ProgramJob | None = None
        self._progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._output.connect(self._on_output, Qt.ConnectionType.QueuedConnection)
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)

    def _on_output(self, line: str) -> None:
        if self._thread is not None:
            self.output.emit(line)

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _on_progress(self, progress: Progress) -> None:
        if self._thread is not None:  # drop updates that arrive after the render ended
            self.progress.emit(progress)

    def start(self, job: RenderJob | ProgramJob) -> None:
        if self._thread is not None:
            raise RuntimeError("a render is already running")
        self.job = job
        self._token = CancelToken()
        token = self._token
        last = [0.0]

        def on_progress(p: Progress) -> None:
            now = time.monotonic()
            if p.done == p.total or now - last[0] >= _PROGRESS_INTERVAL:
                last[0] = now
                self._progress.emit(p)

        def work() -> None:
            result: object
            try:
                if isinstance(job, ProgramJob):
                    result = make_video(job.source, job.output, job.script_path, job.formats,
                                        self._output.emit, on_progress, token)  # fmt: skip
                else:
                    prepared = prepare_scene(job.doc, job.preview)
                    result = render_video(prepared, job.output, on_progress, token, job.source,
                                          job.warnings, job.formats)  # fmt: skip
            except Exception as exc:
                self._done.emit(job, None, exc)
            else:
                self._done.emit(job, result, None)

        self._thread = threading.Thread(target=work, name="codemotion-render", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._token.cancel()

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _on_done(
        self, job: RenderJob | ProgramJob, result: object, error: Exception | None
    ) -> None:
        self._thread = None
        if isinstance(error, RenderCancelledError):
            self.cancelled.emit(job)
        elif error is not None:
            self.failed.emit((job, error))
        else:
            self.finished.emit((job, result))
