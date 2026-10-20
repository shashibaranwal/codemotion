from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import skia
from conftest import EXAMPLES_DIR, GOLDEN_DIR, SCRIPTS_DIR, SNIPPETS_DIR, code_scene, read_source

from codemotion.errors import UserFacingError
from codemotion.geometry import Color, Rect
from codemotion.layout.fonts import FontRole
from codemotion.pipeline import prepare_scene
from codemotion.render.canvas import LinearGradient, Paint, SkiaCanvas
from codemotion.render.renderer import frame_at, render_still
from codemotion.scenes.background import grain_texture
from codemotion.scenes.compose import seed_for
from codemotion.script.runner import run_script


def still(
    code: str, aspect: str = "9:16", preview: bool = True, t: float = 8.0, theme: str = "midnight"
) -> SkiaCanvas:
    return render_still(code_scene(code, preview, aspect=aspect, theme=theme), t)


def as_array(canvas: SkiaCanvas) -> npt.NDArray[np.uint8]:
    return np.frombuffer(canvas.pixels(), dtype=np.uint8).reshape(canvas.height, canvas.width, 4)


# --------------------------------------------------------------------------- canvas


def test_canvas_clear_and_pixels_are_rgba() -> None:
    canvas = SkiaCanvas(4, 3)
    canvas.clear(Color(10, 20, 30))
    px = as_array(canvas)
    assert px.shape == (3, 4, 4)
    assert (px == [10, 20, 30, 255]).all()
    assert len(canvas.pixels()) == 4 * 3 * 4


def test_canvas_shapes_draw() -> None:
    canvas = SkiaCanvas(100, 100)
    canvas.clear(Color(0, 0, 0))
    canvas.round_rect(Rect(10, 10, 80, 80), 10, Paint(color=Color(255, 0, 0)))
    canvas.circle(50, 50, 5, Paint(color=Color(0, 255, 0)))
    px = as_array(canvas)
    assert tuple(px[20, 50]) == (255, 0, 0, 255)
    assert tuple(px[50, 50]) == (0, 255, 0, 255)
    assert tuple(px[1, 1]) == (0, 0, 0, 255)


def test_canvas_gradient_clip_and_opacity() -> None:
    canvas = SkiaCanvas(100, 10)
    canvas.clear(Color(0, 0, 0))
    grad = LinearGradient((0, 0), (100, 0), (Color(0, 0, 0), Color(255, 255, 255)))
    canvas.save()
    canvas.clip_rect(Rect(0, 0, 50, 10))
    canvas.rect(Rect(0, 0, 100, 10), Paint(gradient=grad))
    canvas.restore()
    canvas.rect(Rect(60, 0, 40, 10), Paint(color=Color(200, 200, 200), opacity=0.5))
    px = as_array(canvas)
    assert px[5, 10, 0] < px[5, 40, 0]  # gradient increases left to right
    assert px[5, 55, 0] == 0  # clipped
    assert px[5, 80, 0] == pytest.approx(100, abs=2)  # 50% over black


def test_text_blobs_are_cached_and_draw() -> None:
    canvas = SkiaCanvas(200, 60)
    canvas.clear(Color(0, 0, 0))
    blob = canvas.make_text_blob("def", FontRole.mono, 40)
    assert canvas.make_text_blob("def", FontRole.mono, 40) is blob
    canvas.text_blob(blob, 10, 45, Paint(color=Color(255, 255, 255)))
    canvas.text_blob(canvas.make_text_blob("", FontRole.mono, 40), 0, 0, Paint())  # no crash
    assert as_array(canvas)[..., 0].max() > 200


def test_png_round_trip(tmp_path: Path) -> None:
    canvas = SkiaCanvas(8, 8)
    canvas.clear(Color(1, 2, 3))
    path = tmp_path / "x.png"
    canvas.save_png(path)
    back = skia.Image.open(str(path)).toarray(colorType=skia.kRGBA_8888_ColorType)
    assert (back == as_array(canvas)).all()


# --------------------------------------------------------------------------- scenes


def test_grain_is_seeded() -> None:
    a = grain_texture(16, 8, seed=42)
    assert a.shape == (8, 16)
    assert (a == grain_texture(16, 8, seed=42)).all()
    assert not (a == grain_texture(16, 8, seed=43)).all()


def test_canvas_save_alpha_multiplies_and_restores() -> None:
    canvas = SkiaCanvas(10, 10)
    canvas.clear(Color(0, 0, 0))
    canvas.save_alpha(0.5)
    canvas.save_alpha(0.5)
    canvas.rect(Rect(0, 0, 5, 10), Paint(color=Color(200, 200, 200)))
    canvas.restore()
    canvas.restore()
    canvas.rect(Rect(5, 0, 5, 10), Paint(color=Color(200, 200, 200)))
    px = as_array(canvas)
    assert px[5, 2, 0] == pytest.approx(50, abs=2)  # 25% of 200
    assert px[5, 7, 0] == 200


def test_canvas_put_pixels_replaces_frame() -> None:
    canvas = SkiaCanvas(4, 2)
    rgba = np.full((2, 4, 4), 77, dtype=np.uint8)
    rgba[..., 3] = 255
    canvas.put_pixels(rgba)
    assert (as_array(canvas) == rgba).all()


def test_seed_depends_only_on_code() -> None:
    assert seed_for("x = 1") == seed_for("x = 1")
    assert seed_for("x = 1") != seed_for("x = 2")


def test_still_is_deterministic() -> None:
    code = read_source(EXAMPLES_DIR / "fib.py")
    a = hashlib.sha256(still(code).pixels()).hexdigest()
    b = hashlib.sha256(still(code).pixels()).hexdigest()
    assert a == b


@pytest.mark.parametrize("aspect", ["9:16", "1:1", "16:9"])
def test_full_resolution_stills(aspect: str) -> None:
    canvas = still("print('hi')", aspect=aspect, preview=False)
    w, h = (int(v) for v in aspect.split(":"))
    assert canvas.width * h == canvas.height * w
    assert min(canvas.width, canvas.height) == 1080
    px = as_array(canvas)
    assert (px[..., 3] == 255).all()  # opaque


def test_frame_at_bounds() -> None:
    prepared = code_scene("x", preview=False, fps=30)
    assert frame_at(prepared, 0) == 0
    assert frame_at(prepared, 4.2) == 126
    assert frame_at(prepared, 8) == 239
    for bad in (-0.1, 8.01):
        with pytest.raises(UserFacingError, match="between 0 and 8 seconds"):
            frame_at(prepared, bad)


def test_preview_fps_is_used_for_frames() -> None:
    prepared = code_scene("x", preview=True, fps=60)
    assert prepared.fps == 15
    assert prepared.total_frames == 120


# --------------------------------------------------------------------------- golden frames

GOLDEN_CASES = [
    ("hello_9x16", EXAMPLES_DIR / "hello.py", "9:16"),
    ("hello_1x1", EXAMPLES_DIR / "hello.py", "1:1"),
    ("hello_16x9", EXAMPLES_DIR / "hello.py", "16:9"),
    ("fib_9x16", EXAMPLES_DIR / "fib.py", "9:16"),
    ("long_script_9x16", EXAMPLES_DIR / "long_script.py", "9:16"),
    ("unicode_16x9", SNIPPETS_DIR / "unicode.py", "16:9"),
]


def check_golden(name: str, actual: npt.NDArray[np.uint8], update: bool) -> None:
    golden_path = GOLDEN_DIR / f"{name}.png"
    if update:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        skia.Image.fromarray(actual.copy(), colorType=skia.kRGBA_8888_ColorType).save(
            str(golden_path), skia.kPNG
        )
        pytest.skip(f"updated {golden_path.name}")
    assert golden_path.is_file(), f"missing {golden_path.name}; run pytest --update-golden"
    expected = skia.Image.open(str(golden_path)).toarray(colorType=skia.kRGBA_8888_ColorType)
    assert expected.shape == actual.shape
    diff = np.abs(expected.astype(np.int16) - actual.astype(np.int16)).mean()
    assert diff < 1.0, f"{name}: mean abs diff {diff:.3f}"


@pytest.mark.parametrize(("name", "path", "aspect"), GOLDEN_CASES, ids=[c[0] for c in GOLDEN_CASES])
def test_golden_frames(name: str, path: Path, aspect: str, update_golden: bool) -> None:
    check_golden(name, as_array(still(read_source(path), aspect=aspect)), update_golden)


@pytest.mark.parametrize("theme", ["slate", "daylight", "contrast"])
def test_theme_golden_frames(theme: str, update_golden: bool) -> None:
    code = read_source(EXAMPLES_DIR / "fib.py")
    check_golden(f"theme_{theme}", as_array(still(code, theme=theme)), update_golden)


SCRIPT_GOLDEN_CASES = [
    ("title_card", 3.0),
    ("code_explainer", 8.0),
    ("why_python", 5.0),
    ("terminal_demo", 9.0),
    ("long_code", 9.5),  # scrolled to the highlight, zoomed in
    ("request_flow", 8.0),
]


@pytest.mark.parametrize(
    ("name", "t"), SCRIPT_GOLDEN_CASES, ids=[c[0] for c in SCRIPT_GOLDEN_CASES]
)
def test_script_golden_frames(name: str, t: float, update_golden: bool) -> None:
    doc = run_script(SCRIPTS_DIR / f"{name}.py").doc
    canvas = render_still(prepare_scene(doc, preview=True), t)
    check_golden(f"script_{name}", as_array(canvas), update_golden)
