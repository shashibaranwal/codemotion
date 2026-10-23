"""Frame review: generate frames, edit and approve them, render the result (review.py)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pytest
from test_program import program

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.render.encoder import RenderCancelledError, probe_video
from codemotion.render.renderer import CancelToken, Progress, StillRenderer
from codemotion.review import (
    FrameSession,
    SceneFrames,
    fit,
    over,
    program_review,
    scene_review,
    write_png,
)
from codemotion.script.source import SourceMode, load_text

SCRIPT = """from codemotion import Shape, Text, Video

video = Video(duration=3, aspect="1:1", fps=30)
title = video.add(Text("Hi", 64, at=(0.5, 0.3)))
title.fade_in(0.2)
video.sound("pop", at={pop})
"""


def doc(tmp_path: Path, pop: float = 1.0):  # type: ignore[no-untyped-def]
    return load_text(SCRIPT.format(pop=pop), tmp_path, SourceMode.script).doc


def frames_of(video: Path) -> list[np.ndarray]:
    reader = imageio_ffmpeg.read_frames(str(video))
    meta = next(reader)
    w, h = meta["size"]
    return [np.frombuffer(raw, np.uint8).reshape(h, w, 3) for raw in reader]


def red_square(session: FrameSession) -> np.ndarray:
    width, height = session.size
    layer = np.zeros((height, width, 4), np.uint8)
    layer[100:300, 100:300] = (255, 0, 0, 255)
    return layer


@pytest.fixture
def session(tmp_path: Path) -> FrameSession:
    return scene_review(doc(tmp_path), tmp_path / "out", "hello")


# --------------------------------------------------------------------------- generating


def test_a_scene_review_has_every_frame_drawn_like_a_render(session: FrameSession) -> None:
    assert len(session.entries) == 90 and session.size == (1080, 1080) and session.fps == 30
    assert session.approved_count == 0 and session.edited_count == 0
    assert isinstance(session.frames, SceneFrames)
    expected = StillRenderer(session.frames.prepared).render_frame(45).to_array()
    assert np.array_equal(session.base(session.entries[45]), expected)
    small = session.preview(session.entries[45])
    assert small.shape == (540, 540, 4)  # thumbnails at 540p, but for every frame
    assert not np.array_equal(
        session.preview(session.entries[1]), session.preview(session.entries[5])
    )
    assert (session.folder / "session.json").is_file()
    assert session.folder.parent == session.folder.parent.parent / config.REVIEW_FOLDER


def test_a_program_review_keeps_its_frames_and_resumes(tmp_path: Path) -> None:
    out = tmp_path / "out"
    lines: list[str] = []
    stages: dict[str, int] = {}

    def on_progress(p: Progress) -> None:
        stages[p.stage] = max(stages.get(p.stage, 0), p.done)

    first = program_review(program(), out, "clip", on_output=lines.append, progress=on_progress)
    assert len(first.entries) == 10 and first.size == (64, 48) and first.fps == 10
    assert first.frames.kind == "program" and stages == {"program": 1000, "extract": 1000}
    assert "Rendered 10/10 frames" in lines
    frame = first.base(first.entries[9])
    assert frame.shape == (48, 64, 4)
    assert frame[20, 50, 2] > 200 and frame[20, 50, 0] < 80  # the blue dot has moved right
    first.approve([first.entries[0].id])
    # The same program again: the saved frames are reused, the program isn't run.
    lines.clear()
    again = program_review(program(), out, "clip", on_output=lines.append)
    assert lines == [] and again.folder == first.folder and again.approved_count == 1
    # Changed code is a new review.
    changed = program_review(program().replace("range(10)", "range(6)"), out, "clip")
    assert changed.folder != first.folder and len(changed.entries) == 6


def test_program_frames_lose_an_odd_row_and_column(tmp_path: Path) -> None:
    source = program().replace("(64, 48)", "(65, 49)")  # the writer and the images
    session = program_review(source, tmp_path / "out", "odd")
    assert session.size == (64, 48)


def test_a_failing_program_reports_its_line(tmp_path: Path) -> None:
    with pytest.raises(UserFacingError, match="ValueError: bad"):
        program_review("import cv2\nraise ValueError('bad')\n", tmp_path / "out", "x")
    assert not list((tmp_path / "out").rglob("*.png"))


def test_old_reviews_are_pruned(tmp_path: Path) -> None:
    out = tmp_path / "out"
    folders = [scene_review(doc(tmp_path, pop=p), out, "v").folder for p in (0.5, 1, 1.5, 2)]
    folders += [scene_review(doc(tmp_path, pop=p), out, "v").folder for p in (2.5, 2.6, 2.7)]
    kept = sorted(p for p in folders[0].parent.iterdir())
    assert len(kept) == config.REVIEW_SESSIONS_KEPT and folders[-1] in kept
    assert folders[0] not in kept
    (folders[0].parent / "mine").mkdir()  # folders without a review are never touched
    scene_review(doc(tmp_path, pop=0.1), out, "v")
    assert (folders[0].parent / "mine").is_dir()


# --------------------------------------------------------------------------- editing


def test_paint_is_drawn_over_the_frame_and_unapproves_it(session: FrameSession) -> None:
    entry = session.entries[30]
    session.approve([entry.id])
    session.set_paint(entry.id, red_square(session))
    assert session.is_painted(entry.id) and not entry.approved and session.edited_count == 1
    composed = session.composed(entry)
    assert tuple(composed[200, 200]) == (255, 0, 0, 255)
    assert np.array_equal(composed[600:], session.base(entry)[600:])  # the rest is untouched
    assert session.preview(entry).shape == (1080, 1080, 4)  # edited: full size
    session.set_paint(entry.id, np.zeros_like(red_square(session)))  # cleared: no file
    assert not session.is_painted(entry.id)
    with pytest.raises(ValueError, match="frame size"):
        session.set_paint(entry.id, np.zeros((10, 10, 4), np.uint8))


def test_frames_can_be_replaced_with_images_of_any_size(
    session: FrameSession, tmp_path: Path
) -> None:
    entry = session.entries[3]
    session.set_paint(entry.id, red_square(session))
    image = np.zeros((50, 100, 4), np.uint8)
    image[...] = (0, 255, 0, 255)
    write_png(tmp_path / "green.png", image)
    session.replace(entry.id, tmp_path / "green.png")
    assert session.is_replaced(entry.id) and not session.is_painted(entry.id)
    frame = session.base(entry)
    assert frame.shape == (1080, 1080, 4)
    assert tuple(frame[540, 540]) == (0, 255, 0, 255)  # fitted, centred…
    assert tuple(frame[10, 540]) == (0, 0, 0, 255)  # …on black
    (tmp_path / "junk.png").write_bytes(b"not an image")
    with pytest.raises(UserFacingError, match=r"junk\.png"):
        session.replace(entry.id, tmp_path / "junk.png")
    session.revert(entry.id)
    assert not session.is_edited(entry.id)
    assert np.array_equal(session.base(entry), session.frames.frame(3))


def test_flatten_bakes_the_paint_into_a_replacement(session: FrameSession) -> None:
    entry = session.entries[7]
    session.set_paint(entry.id, red_square(session))
    before = session.composed(entry)
    path = session.flatten(entry.id)
    assert path == session.edit_path(entry.id) and not session.is_painted(entry.id)
    assert np.array_equal(session.composed(entry), before)


def test_hold_and_delete_change_the_frame_list(session: FrameSession) -> None:
    entry = session.entries[10]
    session.set_paint(entry.id, red_square(session))
    copy = session.duplicate(entry.id)
    assert session.entries[11] is copy and copy.source == 10 and session.is_duplicate(copy)
    assert session.is_painted(copy.id)  # the copy keeps the edits
    assert not session.is_duplicate(entry)
    session.delete([session.entries[0].id, session.entries[1].id])
    assert len(session.entries) == 89 and session.entries[0].source == 2
    with pytest.raises(UserFacingError, match="at least one frame"):
        session.delete(e.id for e in session.entries)


def test_a_review_is_saved_and_reopened(session: FrameSession) -> None:
    entry = session.entries[5]
    session.set_paint(entry.id, red_square(session))
    copy = session.duplicate(entry.id)
    session.delete([session.entries[0].id])
    session.approve([copy.id])
    reopened = FrameSession(session.folder, session.frames, session.key)
    assert [(e.id, e.source, e.approved) for e in reopened.entries] == [
        (e.id, e.source, e.approved) for e in session.entries
    ]
    assert reopened.is_painted(copy.id)
    assert reopened.duplicate(copy.id).id not in {e.id for e in session.entries}
    # A different key (other frames) starts over and drops the old edits.
    fresh = FrameSession(session.folder, session.frames, "other")
    assert len(fresh.entries) == 90 and fresh.edited_count == 0


def test_a_damaged_session_file_starts_a_new_review(session: FrameSession) -> None:
    path = session.folder / "session.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["frames"][3][1] = 999  # a frame that doesn't exist
    path.write_text(json.dumps(data), encoding="utf-8")
    assert len(FrameSession(session.folder, session.frames, session.key).entries) == 90


def test_images_are_fitted_and_layers_composited() -> None:
    image = np.full((10, 20, 4), 255, np.uint8)
    out = fit(image, 20, 20)
    assert out.shape == (20, 20, 4) and tuple(out[0, 0]) == (0, 0, 0, 255)
    assert tuple(out[10, 10]) == (255, 255, 255, 255)
    assert fit(out, 20, 20) is out  # already a frame
    base = np.zeros((2, 2, 4), np.uint8)
    base[..., 3] = 255
    layer = np.zeros((2, 2, 4), np.uint8)
    layer[0, 0] = (255, 255, 255, 128)
    mixed = over(base, layer)
    assert tuple(mixed[0, 0]) == (128, 128, 128, 255) and tuple(mixed[1, 1]) == (0, 0, 0, 255)


# --------------------------------------------------------------------------- rendering


def test_rendering_needs_every_frame_approved(session: FrameSession, tmp_path: Path) -> None:
    session.approve(e.id for e in session.entries[:-1])
    with pytest.raises(UserFacingError, match="89 of 90"):
        session.render(tmp_path / "x.mp4")
    assert not (tmp_path / "x.mp4").exists()


def test_the_render_shows_the_reviewed_frames(session: FrameSession, tmp_path: Path) -> None:
    painted = session.entries[20]
    session.set_paint(painted.id, red_square(session))
    session.duplicate(painted.id)  # frames 20 and 21 show the painted frame
    session.delete(e.id for e in session.entries[:10])  # the first 10 are dropped
    session.approve(e.id for e in session.entries)
    video = session.render(tmp_path / "final.mp4", formats=("gif",))
    info = probe_video(video)
    assert (info.width, info.height, info.fps) == (1080, 1080, 30)
    frames = frames_of(video)
    assert len(frames) == 81
    for index in (10, 11):  # entries 10 and 11 are the painted frame and its copy
        r, g, b = (int(v) for v in frames[index][200, 200])
        assert r > 200 and g < 60 and b < 60
    r, g, b = (int(v) for v in frames[12][200, 200])
    assert not (r > 200 and g < 60)
    assert video.with_suffix(".gif").is_file()
    sidecar = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["review"] == {"frames": 81, "edited": 2} and sidecar["program"] is False
    assert sidecar["scene"] == {"aspect": "1:1", "duration": 2.7}


def test_sound_cues_follow_dropped_and_held_frames(tmp_path: Path) -> None:
    session = scene_review(doc(tmp_path, pop=1.0), tmp_path / "out", "s")
    samples = session.soundtrack_samples()
    assert samples is not None
    onset = int(np.argmax(np.abs(samples).max(axis=1) > 0.01))
    assert abs(onset / config.AUDIO_SAMPLE_RATE - 1.0) < 0.01
    session.delete(e.id for e in session.entries[:15])  # half a second earlier
    session.duplicate(session.entries[0].id)  # and one frame later
    moved = session.soundtrack_samples()
    assert moved is not None and len(moved) == round(session.duration * config.AUDIO_SAMPLE_RATE)
    onset = int(np.argmax(np.abs(moved).max(axis=1) > 0.01))
    assert abs(onset / config.AUDIO_SAMPLE_RATE - (0.5 + 1 / 30)) < 0.01


def test_a_programs_review_renders_its_frames(tmp_path: Path) -> None:
    session = program_review(program(), tmp_path / "out", "clip")
    session.duplicate(session.entries[0].id)
    session.approve(e.id for e in session.entries)
    progress: list[Progress] = []
    video = session.render(tmp_path / "clip.mp4", progress.append)
    assert len(frames_of(video)) == 11 and progress[-1].done == progress[-1].total == 11
    sidecar = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["program"] is True and sidecar["scene"]["aspect"] == "64x48"


def test_cancelling_a_render_leaves_no_video(session: FrameSession, tmp_path: Path) -> None:
    session.approve(e.id for e in session.entries)
    token = CancelToken()
    started = threading.Event()

    def on_progress(p: Progress) -> None:
        started.set()
        token.cancel()

    with pytest.raises(RenderCancelledError):
        session.render(tmp_path / "c.mp4", on_progress, token)
    assert started.is_set() and not list(tmp_path.glob("*c*.mp4"))
