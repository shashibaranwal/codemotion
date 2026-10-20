"""Multiprocess frame rendering (SPEC 6.3).

Each worker process rebuilds the scene from the ``SceneDoc`` (rendering is a pure function
of it, so every worker draws identical frames) and draws frames into slots of one shared
memory block. The parent hands finished frames to the sink in order and reuses their
slots, so at most ``slots`` frames exist at once and memory stays flat (SPEC N8).
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from multiprocessing import shared_memory

import numpy as np
import numpy.typing as npt

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.pipeline import PreparedScene, prepare_scene
from codemotion.render.canvas import SkiaCanvas
from codemotion.render.encoder import RenderCancelledError
from codemotion.scenes.base import Scene
from codemotion.script.doc import SceneDoc
from codemotion.sysinfo import peak_memory, usable_cpus

FrameSink = Callable[[memoryview], None]


@dataclass(frozen=True)
class ParallelStats:
    workers: int
    worker_peak_memory: int  # sum of each worker's peak memory (sysinfo.peak_memory), bytes


def auto_workers(prepared: PreparedScene) -> int:
    """How many worker processes a render should use (1 = draw in this process).

    Short or small renders stay in-process: starting workers costs about a second.
    """
    work = prepared.total_frames * prepared.width * prepared.height / 1e6  # megapixel frames
    if work < config.PARALLEL_MIN_WORK:
        return 1
    return max(1, min(config.RENDER_MAX_WORKERS, usable_cpus() - 1))


# --------------------------------------------------------------------------- worker side

_scene: Scene | None = None
_canvas: SkiaCanvas | None = None
_slots: npt.NDArray[np.uint8] | None = None
_memory: shared_memory.SharedMemory | None = None


def _init(doc_json: str, preview: bool, memory_name: str, count: int) -> None:
    global _scene, _canvas, _slots, _memory
    from codemotion.scenes.compose import build_scene

    prepared = prepare_scene(SceneDoc.model_validate_json(doc_json), preview)
    _scene = build_scene(prepared)
    _canvas = SkiaCanvas(prepared.width, prepared.height)
    _memory = shared_memory.SharedMemory(name=memory_name)
    shape = (count, prepared.height, prepared.width, 4)
    _slots = np.ndarray(shape, dtype=np.uint8, buffer=_memory.buf)


def _draw(frame: int, slot: int) -> tuple[int, int]:
    """Draw ``frame`` into ``slot``; returns (worker pid, its peak memory so far)."""
    assert _scene is not None and _canvas is not None and _slots is not None
    _scene.draw(_canvas, frame)
    np.copyto(_slots[slot], _canvas.view())
    return multiprocessing.current_process().pid or 0, peak_memory()


# --------------------------------------------------------------------------- parent side


def render_frames_parallel(
    prepared: PreparedScene,
    sink: FrameSink,
    workers: int,
    on_frame: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ParallelStats:
    """Draw every frame with ``workers`` processes and pass each to ``sink`` in order.

    ``on_frame(done)`` is called after each frame; ``cancelled()`` is polled and stops the
    render with ``RenderCancelledError``. Worker errors are re-raised here.
    """
    total = prepared.total_frames
    frame_bytes = prepared.width * prepared.height * 4
    count = workers * config.PARALLEL_SLOTS_PER_WORKER
    memory = shared_memory.SharedMemory(create=True, size=count * frame_bytes)
    peaks: dict[int, int] = {}
    try:
        slots = np.ndarray((count, frame_bytes), dtype=np.uint8, buffer=memory.buf)
        context = multiprocessing.get_context("spawn")  # same behaviour on every platform
        args = (prepared.doc.model_dump_json(by_alias=True), prepared.preview, memory.name, count)
        with ProcessPoolExecutor(workers, context, initializer=_init, initargs=args) as pool:
            pending: dict[int, tuple[Future[tuple[int, int]], int]] = {}
            free = list(range(count))
            submitted = 0
            try:
                for frame in range(total):
                    while submitted < total and free:
                        slot = free.pop()
                        pending[submitted] = (pool.submit(_draw, submitted, slot), slot)
                        submitted += 1
                    future, slot = pending.pop(frame)
                    while not future.done():
                        if cancelled is not None and cancelled():
                            raise RenderCancelledError
                        wait([future], timeout=0.05)
                    if cancelled is not None and cancelled():
                        raise RenderCancelledError
                    try:
                        pid, peak = future.result()
                    except BrokenProcessPool:
                        raise UserFacingError(
                            "A render worker stopped unexpectedly (out of memory?). "
                            "Close other programs and try again."
                        ) from None
                    peaks[pid] = max(peaks.get(pid, 0), peak)
                    sink(memoryview(slots[slot]))
                    free.append(slot)
                    if on_frame is not None:
                        on_frame(frame + 1)
            except BaseException:
                for future, _ in pending.values():
                    future.cancel()
                pool.shutdown(wait=True, cancel_futures=True)
                raise
    finally:
        memory.close()
        memory.unlink()
    return ParallelStats(workers, sum(peaks.values()))
