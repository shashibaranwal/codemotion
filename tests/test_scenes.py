from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import EXAMPLES_DIR, code_scene, read_source

from codemotion import config
from codemotion.geometry import Color
from codemotion.models import Style
from codemotion.render.canvas import SkiaCanvas
from codemotion.scenes.background import Background, ShadowSpec
from codemotion.scenes.base import SceneContext
from codemotion.scenes.code_block import (
    FULL,
    CascadeReveal,
    FadeReveal,
    TypewriterReveal,
    build_typing_schedule,
)
from codemotion.scenes.compose import build_scene
from codemotion.scenes.cursor import Cursor, cursor_visible
from codemotion.scenes.elements import CodeDrawable, ElementNode
from codemotion.themes.loader import load_theme
from codemotion.timeline.keyframes import Track

LINES = ["def f(x):", "", "    return x + 1", "print(f(2))"]

# --------------------------------------------------------------------------- typewriter


def test_typing_schedule_covers_reveal_in_order() -> None:
    s = build_typing_schedule(LINES, start=24, end=210, fps=30, seed=7)
    times = [t for line in s.char_frames for t in line]
    assert len(times) == sum(len(line.lstrip()) for line in LINES)
    assert times == sorted(times)
    assert times[0] > 24
    assert times[-1] == pytest.approx(210)
    assert s.line_starts[0] == 24
    assert list(s.line_starts) == sorted(s.line_starts)


def test_indentation_is_not_typed() -> None:
    s = build_typing_schedule(LINES, start=0, end=100, fps=30, seed=1)
    assert s.indents == (0, 0, 4, 0)
    assert len(s.char_frames[2]) == len("return x + 1")
    view = TypewriterReveal(s).line_view(math.ceil(s.line_starts[2]), 2)
    assert view is not None and view.columns is not None and view.columns >= 4


def test_typing_rhythm_is_seeded() -> None:
    a = build_typing_schedule(LINES, 0, 100, 30, seed=1)
    assert a == build_typing_schedule(LINES, 0, 100, 30, seed=1)
    assert a != build_typing_schedule(LINES, 0, 100, 30, seed=2)


def test_line_end_pauses_are_bounded() -> None:
    lines = [f"x{i} = {i}" for i in range(40)]
    s = build_typing_schedule(lines, 0, 30, 30, seed=3)  # 1 s for 40 lines
    gaps = [b_start - a[-1] for a, b_start in zip(s.char_frames, s.line_starts[1:], strict=False)]
    assert max(gaps) <= config.TYPE_LINE_END_PAUSE_MAX * 30 + 1e-9
    assert sum(gaps) <= config.TYPE_PAUSE_SHARE_MAX * 30 + 1e-9


def test_typewriter_line_views_progress() -> None:
    reveal = TypewriterReveal(build_typing_schedule(LINES, 10, 100, 30, seed=1))
    assert reveal.line_view(5, 0) is None  # before typing
    middle = reveal.line_view(14, 0)
    assert middle is not None and 0 < (middle.columns or 0) < len(LINES[0])
    assert reveal.line_view(100, 3) is FULL
    assert reveal.line_view(12, 3) is None  # later lines hidden


def test_cursor_position_and_blink() -> None:
    reveal = TypewriterReveal(build_typing_schedule(LINES, 10, 100, 30, seed=1))
    assert reveal.cursor(5) is None
    line, column, last = reveal.cursor(500) or (0, 0, 0.0)
    assert (line, column) == (3, len(LINES[3]))
    assert last == pytest.approx(100)
    assert cursor_visible(101, 100, 30)  # just typed
    interval = config.CURSOR_BLINK_INTERVAL * 30
    assert not cursor_visible(100 + interval * 1.5, 100, 30)
    assert cursor_visible(100 + interval * 2.5, 100, 30)


# --------------------------------------------------------------------------- other reveals


def test_cascade_reveal() -> None:
    reveal = CascadeReveal(start=10, stagger=2, duration=10, offset=24)
    assert reveal.line_view(10, 0) is None
    first = reveal.line_view(15, 0)
    assert first is not None and 0 < first.opacity < 1 and 0 < first.dy < 24
    assert reveal.line_view(15, 3) is None  # starts at 16
    assert reveal.line_view(40, 3) is FULL


def test_fade_reveal() -> None:
    reveal = FadeReveal(Track.of((0, 0.0), (10, 1.0)))
    assert reveal.line_view(0, 0) is None
    half = reveal.line_view(5, 2)
    assert half is not None and half.opacity == pytest.approx(0.5)


# --------------------------------------------------------------------------- backdrop


def _ctx(width: int = 540, height: int = 960, total: int = 120) -> SceneContext:
    return SceneContext(width, height, width / 1080, 15, total)


def test_backdrop_is_opaque_and_deterministic() -> None:
    theme = load_theme("midnight")
    a = Background(theme, seed=1).render(50, _ctx()).copy()
    b = Background(theme, seed=1).render(50, _ctx())
    assert a.shape == (960, 540, 4)
    assert (a[..., 3] == 255).all()
    assert (a == b).all()


def test_backdrop_fades_in_from_black() -> None:
    bg = Background(load_theme("midnight"), seed=1, opacity=Track.of((0, 0.0), (10, 1.0)))
    first = bg.render(0, _ctx()).copy()
    later = bg.render(20, _ctx())
    assert first[..., :3].max() <= 8  # near-black (small margin keeps grain offsets in range)
    assert later[..., :3].mean() > first[..., :3].mean() + 10


def test_backdrop_grain_varies_per_pixel() -> None:
    frame = Background(load_theme("midnight"), seed=1).render(0, _ctx())
    patch = frame[400:420, 200:220, 0].astype(int)
    assert patch.std() > 0.5


def test_backdrop_drifts_over_time() -> None:
    bg = Background(load_theme("midnight"), seed=1)
    a = bg.render(0, _ctx()).astype(int)
    b = bg.render(119, _ctx()).astype(int)
    assert np.abs(a - b).mean() > 1.0


def test_card_shadow_darkens_below_the_card() -> None:
    theme = load_theme("midnight")
    # A 340x300 card centred at (270, 450): bottom edge at y=600, shadow shifted 12 px down.
    spec = ShadowSpec(340, 300, 30, 12, Color(0, 0, 0, 200), lambda f: (270, 450, 1.0, 1.0))
    with_shadow = Background(theme, 1, shadows=[spec]).render(0, _ctx()).astype(int)
    without = Background(theme, 1).render(0, _ctx()).astype(int)
    below = (slice(605, 615), slice(200, 340))
    far = (slice(900, 910), slice(200, 340))
    assert with_shadow[below].mean() < without[below].mean() - 5
    assert abs(with_shadow[far].mean() - without[far].mean()) < 0.5


# --------------------------------------------------------------------------- compose


@pytest.mark.parametrize("style", list(Style))
def test_scene_frames_change_through_the_reveal(style: Style) -> None:
    prepared = code_scene(read_source(EXAMPLES_DIR / "fib.py"), duration=6, style=style)
    scene = build_scene(prepared)
    canvas = SkiaCanvas(prepared.width, prepared.height)

    def snapshot(frame: int) -> np.ndarray:
        scene.draw(canvas, frame)
        return canvas.to_array().astype(int)

    total = prepared.total_frames
    first, middle, last = snapshot(0), snapshot(total // 2), snapshot(total - 1)
    assert first[..., :3].mean() < 10  # starts from black
    assert np.abs(last - middle).mean() < np.abs(last - first).mean()


def test_cursor_only_for_typewriter() -> None:
    for style, expected in ((Style.typewriter, True), (Style.fade_in, False)):
        scene = build_scene(code_scene("x = 1\ny = 2", duration=4, style=style))
        node = scene.nodes[1]
        assert isinstance(node, ElementNode) and isinstance(node.drawable, CodeDrawable)
        assert any(isinstance(n, Cursor) for n in node.drawable.parts) is expected


def test_outro_fade_ends_black() -> None:
    prepared = code_scene("x = 1", duration=3, outro="fade")
    canvas = SkiaCanvas(prepared.width, prepared.height)
    build_scene(prepared).draw(canvas, prepared.total_frames - 1)
    assert canvas.to_array()[..., :3].max() == 0
