from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest
from conftest import SCRIPTS_DIR

from codemotion.pipeline import prepare_scene
from codemotion.render.canvas import SkiaCanvas
from codemotion.render.renderer import render_still
from codemotion.script.doc import SceneDoc
from codemotion.timeline.easing import ease_out_cubic
from codemotion.timeline.keyframes import Animation, compile_track


def scene(*elements: dict[str, object], **settings: object) -> SceneDoc:
    data = {"duration": 4, "aspect": "1:1", "background": "#000000", "fade_in": 0, **settings}
    return SceneDoc.model_validate({**data, "elements": list(elements)})


def frame_at(doc: SceneDoc, t: float) -> npt.NDArray[np.int32]:
    canvas: SkiaCanvas = render_still(prepare_scene(doc, preview=True), t)
    return canvas.to_array()[..., :3].astype(np.int32)


def bright(px: npt.NDArray[np.int32]) -> npt.NDArray[np.bool_]:
    return px.max(axis=2) > 120  # background is near-black (+ grain)


def center_of(mask: npt.NDArray[np.bool_]) -> tuple[float, float]:
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


# --------------------------------------------------------------------------- compile_track


def test_compile_sequential_relative_and_from() -> None:
    track = compile_track(1.0, [Animation(0, 1, to=0.0), Animation(2, 1, by=5.0)], fps=10)
    assert [track.at(f) for f in (0, 10, 20, 30, 40)] == [1.0, 0.0, 0.0, 5.0, 5.0]
    track = compile_track(1.0, [Animation(1, 1, from_=0.0, to=1.0)], fps=10)
    assert track.at(0) == 0.0  # value before the first animation is its `from`


def test_compile_overlap_takes_over_in_progress_value() -> None:
    track = compile_track(0.0, [Animation(1, 1, to=1.0), Animation(1.5, 1, to=0.0)], fps=10)
    assert track.at(15) == pytest.approx(0.5)
    assert track.at(20) == pytest.approx(0.25)
    assert track.at(25) == 0.0


def test_compile_zero_length_jumps() -> None:
    track = compile_track(5.0, [Animation(1, 0, to=9.0)], fps=10)
    assert (track.at(9), track.at(10)) == (5.0, 9.0)


def test_compile_uses_easing() -> None:
    track = compile_track(0.0, [Animation(0, 1, to=1.0, ease=ease_out_cubic)], fps=10)
    assert track.at(5) == pytest.approx(ease_out_cubic(0.5))


# --------------------------------------------------------------------------- elements


def test_text_is_centred_on_its_anchor() -> None:
    doc = scene({"id": "t", "type": "text", "text": "HELLO", "size": 80, "color": "#FFFFFF",
                 "x": 0.3, "y": 0.6})  # fmt: skip
    cx, cy = center_of(bright(frame_at(doc, 1)))
    assert cx == pytest.approx(0.3 * 540, abs=6)
    assert cy == pytest.approx(0.6 * 540, abs=6)


def test_left_aligned_text_starts_at_anchor() -> None:
    doc = scene({"id": "t", "type": "text", "text": "HELLO", "size": 80, "color": "#FFFFFF",
                 "x": 0.3, "align": "left"})  # fmt: skip
    xs = np.nonzero(bright(frame_at(doc, 1)))[1]
    assert xs.min() == pytest.approx(0.3 * 540, abs=6)


def test_offsets_scale_and_visibility_window() -> None:
    base = {"id": "s", "type": "shape", "shape": "rect", "width": 100, "height": 100,
            "color": "#FFFFFF", "start": 1, "end": 3}  # fmt: skip
    moved = dict(base, anims=[{"prop": "ox", "start": 0, "length": 0, "by": 200},
                              {"prop": "scale", "start": 0, "length": 0, "to": 2}])  # fmt: skip
    assert not bright(frame_at(scene(base), 0.5)).any()  # before start
    assert not bright(frame_at(scene(base), 3.5)).any()  # after end
    plain = bright(frame_at(scene(base), 2))
    shifted = bright(frame_at(scene(moved), 2))
    assert center_of(shifted)[0] - center_of(plain)[0] == pytest.approx(100, abs=2)  # 200 design px
    assert shifted.sum() == pytest.approx(plain.sum() * 4, rel=0.05)


def test_rotation_and_opacity() -> None:
    rect = {"id": "s", "type": "shape", "shape": "rect", "width": 300, "height": 20,
            "color": "#FFFFFF"}  # fmt: skip
    flat = bright(frame_at(scene(rect), 1))
    turned = bright(frame_at(scene(dict(rect, rotation=90)), 1))
    assert np.ptp(np.nonzero(flat)[1]) > np.ptp(np.nonzero(flat)[0])
    assert np.ptp(np.nonzero(turned)[0]) > np.ptp(np.nonzero(turned)[1])
    half = frame_at(scene(dict(rect, opacity=0.5)), 1)
    assert half.max() == pytest.approx(128, abs=6)


def test_text_type_on_reveals_progressively() -> None:
    text = {"id": "t", "type": "text", "text": "ABCDEFGH", "size": 80, "color": "#FFFFFF",
            "anims": [{"prop": "reveal", "start": 0, "length": 2, "from": 0, "to": 1,
                       "ease": "linear"}]}  # fmt: skip
    counts = [bright(frame_at(scene(text), t)).sum() for t in (0, 1, 2.5)]
    assert counts[0] == 0
    assert 0 < counts[1] < counts[2]


def test_circle_and_theme_colors() -> None:
    doc = scene({"id": "c", "type": "shape", "shape": "circle", "width": 200, "color": "accent"})
    px = frame_at(doc, 1)
    assert tuple(px[270, 270]) == pytest.approx((0x7C, 0x7C, 0xFF), abs=6)


def test_image_element_draws_pixels() -> None:
    path = SCRIPTS_DIR / "assets" / "logo.png"
    doc = scene({"id": "i", "type": "image", "path": str(path), "width": 200})
    px = frame_at(doc, 1)
    assert px[270, 270].max() > 100
    assert px[20, 20].max() < 30


def test_code_element_follows_its_anchor() -> None:
    code = {"id": "c", "type": "code", "code": "x = 1", "reveal": "none",
            "region": [0.1, 0.1, 0.5, 0.5]}  # fmt: skip
    at_region = bright(frame_at(scene(code), 1))
    moved = bright(frame_at(scene(dict(code, x=0.7, y=0.7)), 1))
    assert center_of(moved)[0] - center_of(at_region)[0] == pytest.approx(0.4 * 540, abs=8)


def test_code_highlight_dims_other_lines() -> None:
    code = {"id": "c", "type": "code", "code": "aaaa = 1\nbbbb = 2", "reveal": "none"}
    plain = frame_at(scene(code), 3)
    lit = frame_at(
        scene(dict(code, highlights=[{"lines": "2", "start": 0, "fade": 0.1, "zoom": 1}])), 3
    )
    prepared = prepare_scene(scene(code), preview=True)
    first = prepared.codes["c"].layout.lines[0]
    band = (slice(int(first.top), int(first.top + prepared.codes["c"].layout.line_height)),)
    assert lit[band].max() < plain[band].max() * 0.6
