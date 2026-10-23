"""Frame review: generate every frame of a video, fix and approve each one, then render.

Generating: a scene's frames (video scripts and code cards) are drawn when asked from its
``SceneDoc``, so nothing is stored for frames that stay as they are. A video program runs
once, and the frames of the video it writes are kept as PNGs, with its sound if any.

Reviewing: a frame can be painted on (a transparent layer over it), replaced with an image
(from a file, or edited in another app), held for longer (duplicated), dropped or reverted,
and each frame is approved. The final render needs every frame approved.

A review lives in a folder, so closing it keeps the edits and reopening the same video
resumes it::

    session.json    the frames in order: id, which generated frame it shows, approved
    edits/<id>.png  replacement images
    paint/<id>.png  paint layers, drawn over the frame
    frames/         a program's frames (000000.png, …) and its sound (audio.npy)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import threading
import time
from bisect import bisect_left
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
import skia

from codemotion import __version__, config
from codemotion.errors import UserFacingError
from codemotion.pipeline import prepare_scene
from codemotion.render.canvas import decode_image
from codemotion.render.encoder import (
    ExportFormat,
    FFmpegEncoder,
    RenderCancelledError,
    VideoInfo,
    export_video,
)
from codemotion.render.renderer import (
    CancelToken,
    Progress,
    ProgressCallback,
    StillRenderer,
    render_frames,
    write_program_sidecar,
)
from codemotion.script.doc import SceneDoc
from codemotion.script.program import (
    incomplete,
    program_files,
    run_program,
    stage_reporter,
)
from codemotion.script.runner import ScriptRunError

Image = npt.NDArray[np.uint8]  # (height, width, 4) RGBA, straight alpha
SESSION_FILE = "session.json"
FRAMES_DIR = "frames"
AUDIO_FILE = "audio.npy"


# --------------------------------------------------------------------------- images


def write_png(path: Path, image: Image) -> None:
    """Save RGBA ``image`` as a PNG, atomically (an app watching the file never sees half)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = skia.Image.fromarray(
        np.ascontiguousarray(image),
        colorType=skia.kRGBA_8888_ColorType,
        alphaType=skia.kUnpremul_AlphaType,
    ).encodeToData(skia.kPNG, 100)
    if data is None:
        raise UserFacingError(f"Could not encode {path.name}.")
    temp = path.with_name(f".{path.stem}.partial.png")
    try:
        temp.write_bytes(bytes(data))
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise UserFacingError(f"Could not save {path}: {exc.strerror}.") from None


def read_image(path: Path) -> Image:
    try:
        return decode_image(path)
    except ValueError:
        raise UserFacingError(f"{path.name} isn't an image CodeMotion can read.") from None


def fit(image: Image, width: int, height: int) -> Image:
    """``image`` as an opaque ``width`` x ``height`` frame: scaled to fit, centred on black."""
    h, w = image.shape[:2]
    if (w, h) == (width, height) and bool((image[..., 3] == 255).all()):
        return image
    out = np.zeros((height, width, 4), np.uint8)
    out[..., 3] = 255
    surface = skia.Surface(out, colorType=skia.kRGBA_8888_ColorType,
                           alphaType=skia.kPremul_AlphaType)  # fmt: skip
    scale = min(width / w, height / h)
    rect = skia.Rect.MakeXYWH((width - w * scale) / 2, (height - h * scale) / 2,
                              w * scale, h * scale)  # fmt: skip
    source = skia.Image.fromarray(np.ascontiguousarray(image),
                                  colorType=skia.kRGBA_8888_ColorType,
                                  alphaType=skia.kUnpremul_AlphaType)  # fmt: skip
    surface.getCanvas().drawImageRect(source, rect, skia.SamplingOptions(skia.FilterMode.kLinear))
    return out  # opaque, so premultiplied == straight alpha


def over(base: Image, layer: Image) -> Image:
    """``layer`` (straight alpha) composited over the opaque ``base``."""
    alpha = layer[..., 3:4].astype(np.uint16)
    out = base.copy()
    mixed = layer[..., :3] * alpha + base[..., :3] * (255 - alpha) + 127
    out[..., :3] = (mixed // 255).astype(np.uint8)
    return out


# --------------------------------------------------------------------------- generated frames


class Frames(Protocol):
    """The generated frames a review starts from."""

    kind: str  # "scene" | "program"
    fps: float
    width: int
    height: int
    count: int

    def frame(self, index: int) -> Image:
        """Frame ``index`` at full size."""
        ...

    def small(self, index: int) -> Image:
        """Frame ``index``, possibly smaller (for thumbnails)."""
        ...


class SceneFrames:
    """A scene's frames, drawn when asked. Safe to use from several threads."""

    kind = "scene"

    def __init__(self, doc: SceneDoc) -> None:
        self.doc = doc
        self.prepared = prepare_scene(doc)
        small = prepare_scene(doc, preview=True, fps=doc.fps)  # every frame, at 540p
        self.fps = float(self.prepared.fps)
        self.width, self.height = self.prepared.width, self.prepared.height
        self.count = self.prepared.total_frames
        self._renderers = (StillRenderer(self.prepared), StillRenderer(small))
        self._locks = (threading.Lock(), threading.Lock())

    def _draw(self, index: int, which: int) -> Image:
        with self._locks[which]:
            return self._renderers[which].render_frame(index).to_array()

    def frame(self, index: int) -> Image:
        return self._draw(index, 0)

    def small(self, index: int) -> Image:
        return self._draw(index, 1)


class FolderFrames:
    """Frames saved as ``000000.png``, … (a video program's)."""

    kind = "program"

    def __init__(self, folder: Path, fps: float, width: int, height: int, count: int) -> None:
        self.folder = folder
        self.fps = fps
        self.width, self.height = width, height
        self.count = count

    def path(self, index: int) -> Path:
        return self.folder / f"{index:06d}.png"

    def frame(self, index: int) -> Image:
        try:
            return decode_image(self.path(index))
        except ValueError:
            raise UserFacingError(
                f"Frame {index + 1} is missing from {self.folder}. Generate the frames again."
            ) from None

    def small(self, index: int) -> Image:
        return self.frame(index)

    @property
    def audio(self) -> npt.NDArray[np.float32] | None:
        path = self.folder / AUDIO_FILE
        if not path.is_file():
            return None
        samples: npt.NDArray[np.float32] = np.load(path)
        return samples


def extract_frames(
    video: Path,
    folder: Path,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> FolderFrames:
    """Decode every frame of ``video`` (and its sound) into ``folder/frames``.

    Odd sizes lose their last row or column (H.264 needs even sizes). The frames are
    written to a temporary folder first, so an interrupted run leaves no partial set.
    """
    import imageio_ffmpeg

    from codemotion.render.audio import decode_file

    temp = folder / f"{FRAMES_DIR}.partial"
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    try:
        reader = imageio_ffmpeg.read_frames(str(video), pix_fmt="rgba", bpp=4)
        try:
            meta = next(reader)
            src_w, src_h = (int(v) for v in meta["size"])
            fps = float(meta["fps"])
            expected = max(1, round(float(meta["duration"]) * fps))
            width, height = src_w - src_w % 2, src_h - src_h % 2
            count = 0
            for raw in reader:
                if cancelled():
                    raise RenderCancelledError
                image = np.frombuffer(raw, np.uint8).reshape(src_h, src_w, 4)[:height, :width]
                write_png(temp / f"{count:06d}.png", image)
                count += 1
                if progress is not None:
                    progress(min(1.0, count / expected))
        finally:
            reader.close()
        if count == 0 or width == 0 or height == 0:
            raise UserFacingError(f"{video.name} has no frames to review.")
        try:
            audio = decode_file(video)
        except UserFacingError:  # no sound
            audio = None
        if audio is not None and audio.size:
            np.save(temp / AUDIO_FILE, audio)
        target = folder / FRAMES_DIR
        shutil.rmtree(target, ignore_errors=True)
        os.replace(temp, target)
    except (OSError, RuntimeError, StopIteration, KeyError, TypeError, ValueError) as exc:
        shutil.rmtree(temp, ignore_errors=True)
        detail = f": {exc.strerror}" if isinstance(exc, OSError) and exc.strerror else ""
        raise UserFacingError(f"Could not read the frames of {video.name}{detail}.") from None
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return FolderFrames(target, fps, width, height, count)


# --------------------------------------------------------------------------- the review


@dataclass
class FrameEntry:
    id: int  # stable: names the frame's edit files
    source: int  # the generated frame it shows
    approved: bool = False


class FrameSession:
    """The frames of one review, in order, with their edits and approvals.

    Changes are saved as they happen. Frames are only read from other threads (thumbnails,
    the large view); every change happens on one thread.
    """

    def __init__(self, folder: Path, frames: Frames, key: str, source: str | None = None) -> None:
        self.folder = folder
        self.frames = frames
        self.key = key
        self.source = source  # the script's path, recorded with the render
        self.entries = [FrameEntry(i, i) for i in range(frames.count)]
        self._next_id = frames.count
        if not self._load():
            for name in ("edits", "paint"):  # from an older review that didn't match
                shutil.rmtree(folder / name, ignore_errors=True)

    # -- saving ----------------------------------------------------------------------------

    def _load(self) -> bool:
        try:
            data = json.loads((self.folder / SESSION_FILE).read_text(encoding="utf-8"))
            if data["key"] != self.key or data["count"] != self.frames.count:
                return False
            entries = [FrameEntry(int(i), int(s), bool(a)) for i, s, a in data["frames"]]
            next_id = int(data["next_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        ids = {e.id for e in entries}
        valid = all(0 <= e.source < self.frames.count and e.id < next_id for e in entries)
        sources = [e.source for e in entries]
        if not entries or len(ids) != len(entries) or not valid or sources != sorted(sources):
            return False
        self.entries, self._next_id = entries, next_id
        return True

    def save(self) -> None:
        data = {
            "codemotion": __version__,
            "key": self.key,
            "kind": self.frames.kind,
            "fps": self.frames.fps,
            "size": [self.frames.width, self.frames.height],
            "count": self.frames.count,
            "next_id": self._next_id,
            "frames": [[e.id, e.source, int(e.approved)] for e in self.entries],
        }
        path = self.folder / SESSION_FILE
        temp = path.with_name(f".{SESSION_FILE}.partial")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            raise UserFacingError(f"Could not save the review in {self.folder}: "
                                  f"{exc.strerror}.") from None  # fmt: skip

    def discard(self) -> None:
        """Delete the review's folder (its edits and any generated frames)."""
        shutil.rmtree(self.folder, ignore_errors=True)

    # -- reading ---------------------------------------------------------------------------

    @property
    def fps(self) -> float:
        return self.frames.fps

    @property
    def size(self) -> tuple[int, int]:
        return self.frames.width, self.frames.height

    @property
    def duration(self) -> float:
        return len(self.entries) / self.fps

    @property
    def approved_count(self) -> int:
        return sum(e.approved for e in self.entries)

    @property
    def all_approved(self) -> bool:
        return all(e.approved for e in self.entries)

    @property
    def edited_count(self) -> int:
        return sum(self.is_edited(e.id) for e in self.entries)

    def index(self, entry_id: int) -> int:
        for i, entry in enumerate(self.entries):
            if entry.id == entry_id:
                return i
        raise KeyError(entry_id)

    def entry(self, entry_id: int) -> FrameEntry:
        return self.entries[self.index(entry_id)]

    def edit_path(self, entry_id: int) -> Path:
        return self.folder / "edits" / f"{entry_id}.png"

    def paint_path(self, entry_id: int) -> Path:
        return self.folder / "paint" / f"{entry_id}.png"

    def is_replaced(self, entry_id: int) -> bool:
        return self.edit_path(entry_id).is_file()

    def is_painted(self, entry_id: int) -> bool:
        return self.paint_path(entry_id).is_file()

    def is_edited(self, entry_id: int) -> bool:
        return self.is_replaced(entry_id) or self.is_painted(entry_id)

    def is_duplicate(self, entry: FrameEntry) -> bool:
        """True if an earlier frame shows the same generated frame (it was held longer)."""
        i = self.index(entry.id)
        return i > 0 and self.entries[i - 1].source == entry.source

    def base(self, entry: FrameEntry) -> Image:
        """The frame under its paint: its replacement image, or the generated frame."""
        if self.is_replaced(entry.id):
            return fit(read_image(self.edit_path(entry.id)), *self.size)
        return self.frames.frame(entry.source)

    def paint(self, entry_id: int) -> Image | None:
        path = self.paint_path(entry_id)
        if not path.is_file():
            return None
        layer = read_image(path)
        return layer if layer.shape[:2] == (self.frames.height, self.frames.width) else None

    def composed(self, entry: FrameEntry, generated: Image | None = None) -> Image:
        """The frame as it will be rendered. ``generated``: its generated frame, if at hand."""
        image = self.base(entry) if generated is None or self.is_replaced(entry.id) else generated
        layer = self.paint(entry.id)
        return image if layer is None else over(image, layer)

    def preview(self, entry: FrameEntry) -> Image:
        """The frame for a thumbnail (smaller when it has no edits)."""
        if self.is_edited(entry.id):
            return self.composed(entry)
        return self.frames.small(entry.source)

    # -- editing ---------------------------------------------------------------------------

    def _changed(self, entry_id: int) -> None:
        self.entry(entry_id).approved = False  # an edited frame needs a new look
        self.save()

    def set_paint(self, entry_id: int, layer: Image | None) -> None:
        """Save the paint layer drawn over a frame (``None`` or fully clear: remove it)."""
        path = self.paint_path(entry_id)
        if layer is not None and layer.shape[:2] != (self.frames.height, self.frames.width):
            raise ValueError(
                f"paint layer is {layer.shape[1]}x{layer.shape[0]}, not the frame size"
            )
        if layer is None or not layer[..., 3].any():
            path.unlink(missing_ok=True)
        else:
            write_png(path, layer)
        self._changed(entry_id)

    def replace(self, entry_id: int, image_file: Path) -> None:
        """Show the image ``image_file`` instead of the frame (scaled to fit)."""
        self.set_replacement(entry_id, read_image(image_file))

    def set_replacement(self, entry_id: int, image: Image) -> Path:
        path = self.edit_path(entry_id)
        write_png(path, fit(image, *self.size))
        self.paint_path(entry_id).unlink(missing_ok=True)  # it was drawn for the old frame
        self._changed(entry_id)
        return path

    def flatten(self, entry_id: int) -> Path:
        """Turn the frame and its paint into one replacement image (for editing elsewhere)."""
        return self.set_replacement(entry_id, self.composed(self.entry(entry_id)))

    def revert(self, entry_id: int) -> None:
        """Back to the generated frame."""
        self.edit_path(entry_id).unlink(missing_ok=True)
        self.paint_path(entry_id).unlink(missing_ok=True)
        self._changed(entry_id)

    def duplicate(self, entry_id: int) -> FrameEntry:
        """Show a frame one frame longer: a copy (with its edits) right after it."""
        i = self.index(entry_id)
        copy = FrameEntry(self._next_id, self.entries[i].source)
        self._next_id += 1
        for path in (self.edit_path, self.paint_path):
            if path(entry_id).is_file():
                shutil.copyfile(path(entry_id), path(copy.id))
        self.entries.insert(i + 1, copy)
        self.save()
        return copy

    def delete(self, entry_ids: Iterable[int]) -> None:
        """Drop frames from the video."""
        ids = set(entry_ids)
        if not any(e.id not in ids for e in self.entries):
            raise UserFacingError("A video needs at least one frame.")
        for entry_id in ids:
            self.edit_path(entry_id).unlink(missing_ok=True)
            self.paint_path(entry_id).unlink(missing_ok=True)
        self.entries = [e for e in self.entries if e.id not in ids]
        self.save()

    def approve(self, entry_ids: Iterable[int], approved: bool = True) -> None:
        ids = set(entry_ids)
        for entry in self.entries:
            if entry.id in ids:
                entry.approved = approved
        self.save()

    # -- rendering -------------------------------------------------------------------------

    def render(
        self,
        output: Path,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
        formats: Sequence[ExportFormat] = (),
    ) -> Path:
        """Render the approved frames to the MP4 ``output`` (plus ``formats`` and a sidecar)."""
        if not self.all_approved:
            raise UserFacingError(f"Approve every frame first: {self.approved_count} of "
                                  f"{len(self.entries)} are approved.")  # fmt: skip
        width, height = self.size
        soundtrack = self._soundtrack(output)
        try:
            with FFmpegEncoder(output, width, height, self.fps, soundtrack) as encoder:
                if isinstance(self.frames, SceneFrames):
                    self._write_scene(self.frames, encoder.write, progress, cancel)
                else:
                    self._write_saved(encoder.write, progress, cancel)
                video = encoder.finish()
        finally:
            if soundtrack is not None:
                soundtrack.unlink(missing_ok=True)
        exports: list[Path] = []
        report = stage_reporter(progress)
        try:
            for fmt in dict.fromkeys(formats):
                exports.append(export_video(video, fmt, self.size, self.duration, report(fmt),
                                            lambda: bool(cancel and cancel.cancelled)))  # fmt: skip
        except BaseException:
            for path in (video, *exports):
                path.unlink(missing_ok=True)
            raise
        info = VideoInfo(width, height, self.fps, round(self.duration, 3))
        review = {"frames": len(self.entries), "edited": self.edited_count}
        extra = {"program": self.frames.kind == "program", "review": review}
        write_program_sidecar(video, info, self.source, exports, extra)
        return video

    def _write_scene(
        self,
        frames: SceneFrames,
        write: Callable[[memoryview | bytes], None],
        progress: ProgressCallback | None,
        cancel: CancelToken | None,
    ) -> None:
        """Draw the scene's frames in order (in parallel if it's big) and write each entry
        that shows it; edited ones are composed first."""
        width, height = self.size
        shown: dict[int, list[FrameEntry]] = {}
        for entry in self.entries:
            shown.setdefault(entry.source, []).append(entry)
        edited = {e.id for e in self.entries if self.is_edited(e.id)}
        drawn = [0]

        def sink(frame: memoryview) -> None:
            for entry in shown.get(drawn[0], ()):
                if entry.id in edited:
                    generated = np.frombuffer(frame, np.uint8).reshape(height, width, 4)
                    write(self.composed(entry, generated).tobytes())
                else:
                    write(frame)
            drawn[0] += 1

        render_frames(frames.prepared, sink, progress, cancel)

    def _write_saved(
        self,
        write: Callable[[memoryview | bytes], None],
        progress: ProgressCallback | None,
        cancel: CancelToken | None,
    ) -> None:
        total = len(self.entries)
        started = time.monotonic()
        for done, entry in enumerate(self.entries, 1):
            if cancel is not None and cancel.cancelled:
                raise RenderCancelledError
            write(self.composed(entry).tobytes())
            if progress is not None:
                elapsed = time.monotonic() - started
                eta = elapsed / done * (total - done) if done >= 3 else None
                progress(Progress(done, total, elapsed, eta))

    def soundtrack_samples(self) -> npt.NDArray[np.float32] | None:
        """The sound, following dropped and held frames; None if the video is silent.

        A scene's sound cues move with the frame they start on (or the next one kept); a
        program's recorded sound is cut and repeated with its frames.
        """
        from codemotion.render.audio import mix

        sources = [e.source for e in self.entries]
        fps = self.fps
        if isinstance(self.frames, SceneFrames):
            doc = self.frames.doc
            if not doc.sounds:
                return None
            cues = []
            for cue in doc.sounds:
                frame = math.floor(cue.at * fps + 1e-9)
                position = bisect_left(sources, frame)
                kept = position < len(sources) and sources[position] == frame
                at = position / fps + (cue.at - frame / fps if kept else 0.0)
                cues.append(cue.model_copy(update={"at": at}))
            return mix(doc.model_copy(update={"duration": self.duration, "sounds": cues}))
        audio = self.frames.audio if isinstance(self.frames, FolderFrames) else None
        if audio is None:
            return None
        per_frame = config.AUDIO_SAMPLE_RATE / fps
        pieces = [audio[round(s * per_frame) : round((s + 1) * per_frame)] for s in sources]
        out = np.zeros((round(self.duration * config.AUDIO_SAMPLE_RATE), 2), np.float32)
        joined = np.concatenate(pieces)[: len(out)]
        out[: len(joined)] = joined
        return out

    def _soundtrack(self, output: Path) -> Path | None:
        samples = self.soundtrack_samples()
        if samples is None:
            return None
        from codemotion.render.audio import write_wav

        output.parent.mkdir(parents=True, exist_ok=True)
        path = output.with_name(f".{output.stem}.audio.wav")
        try:
            return write_wav(path, samples)
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise UserFacingError(f"Could not write the soundtrack: {exc.strerror}.") from None


# --------------------------------------------------------------------------- starting a review


def _key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def review_folder(output_dir: Path, stem: str, key: str) -> Path:
    return output_dir / config.REVIEW_FOLDER / f"{stem}-{key[:12]}"


def _prune(current: Path, keep: int = config.REVIEW_SESSIONS_KEPT) -> None:
    """Delete the oldest reviews next to ``current`` so at most ``keep`` remain."""
    found: list[tuple[float, Path]] = []
    try:
        for folder in current.parent.iterdir():
            session = folder / SESSION_FILE
            if folder == current or not session.is_file():
                continue
            try:
                if "codemotion" in json.loads(session.read_text(encoding="utf-8")):
                    found.append((session.stat().st_mtime, folder))
            except (OSError, ValueError):
                continue
    except OSError:
        return
    found.sort(reverse=True)
    for _, folder in found[max(0, keep - 1) :]:
        shutil.rmtree(folder, ignore_errors=True)


def scene_review(
    doc: SceneDoc, output_dir: Path, stem: str, source: str | None = None
) -> FrameSession:
    """Start (or resume) reviewing a scene's frames."""
    key = _key("scene", doc.model_dump_json(by_alias=True))
    folder = review_folder(output_dir, stem, key)
    session = FrameSession(folder, SceneFrames(doc), key, source)
    session.save()
    _prune(folder)
    return session


def _saved_frames(folder: Path, key: str) -> FolderFrames | None:
    """The frames a program generated earlier for this review, if they're all there."""
    try:
        data = json.loads((folder / SESSION_FILE).read_text(encoding="utf-8"))
        if data["key"] != key or data["kind"] != "program":
            return None
        width, height = (int(v) for v in data["size"])
        frames = FolderFrames(folder / FRAMES_DIR, float(data["fps"]), width, height,
                              int(data["count"]))  # fmt: skip
    except (OSError, ValueError, KeyError, TypeError):
        return None
    complete = frames.count > 0 and all(frames.path(i).is_file() for i in range(frames.count))
    return frames if complete else None


def program_review(
    source: str,
    output_dir: Path,
    stem: str,
    script_path: Path | None = None,
    on_output: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    timeout: float = config.PROGRAM_TIMEOUT_SECONDS,
) -> FrameSession:
    """Run a video program and start reviewing the frames of the video it writes (or
    resume the review of the same program). ``progress`` stages: "program", "extract"."""
    key = _key("program", str(script_path), source.replace("\r\n", "\n"))
    folder = review_folder(output_dir, stem, key)
    frames = _saved_frames(folder, key)
    if frames is None:
        problem = incomplete(source)
        if problem is not None:
            raise ScriptRunError(*problem)
        token = cancel or CancelToken()
        report = stage_reporter(progress)
        with program_files(source, script_path) as (script, workdir):
            written, _, _ = run_program(script, workdir, on_output, report("program"),
                                        lambda: token.cancelled, timeout)  # fmt: skip
            frames = extract_frames(written, folder, report("extract"), lambda: token.cancelled)
    session = FrameSession(folder, frames, key, str(script_path) if script_path else None)
    session.save()
    _prune(folder)
    return session
