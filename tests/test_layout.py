from __future__ import annotations

from pathlib import Path

import pytest
from conftest import EXAMPLES, read_source

from codemotion import config
from codemotion.analysis.normalize import normalize
from codemotion.analysis.tokenize import tokenize
from codemotion.errors import UserFacingError
from codemotion.layout.layout import MISSING_GLYPH_WARNING, SCROLL_WARNING, Layout, compute_layout
from codemotion.models import Aspect


def layout_for(code: str, aspect: Aspect = Aspect.portrait, preview: bool = False) -> Layout:
    return compute_layout(tokenize(normalize(code)), aspect, preview)


def _inside(
    inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]
) -> bool:
    eps = 1e-6
    return (
        inner[0] >= outer[0] - eps
        and inner[1] >= outer[1] - eps
        and inner[0] + inner[2] <= outer[0] + outer[2] + eps
        and inner[1] + inner[3] <= outer[1] + outer[3] + eps
    )


@pytest.mark.parametrize("aspect", list(Aspect))
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: Path(p).name)
def test_font_within_clamps_and_card_in_safe_area(path: Path, aspect: Aspect) -> None:
    lay = layout_for(read_source(path), aspect)
    preset = config.ASPECT_PRESETS[aspect.value]
    assert preset.min_font <= lay.font_size <= preset.max_font
    assert _inside(lay.card_rect, lay.safe_rect)
    assert _inside(lay.code_rect, lay.card_rect)
    # Longest line + gutter fits the code area.
    longest = max(len(line.text) for line in lay.lines)
    assert lay.column_x(longest) <= lay.code_rect.right + 1e-6


def test_short_code_clamps_to_max_font() -> None:
    lay = layout_for("x = 1", Aspect.portrait)
    assert lay.font_size == config.ASPECT_PRESETS["9:16"].max_font
    assert not lay.scroll
    assert lay.warnings == ()


def test_long_code_scrolls_with_warning() -> None:
    lay = layout_for("\n".join(f"x{i} = {i}" for i in range(55)), Aspect.portrait)
    assert lay.scroll
    assert lay.font_size == config.ASPECT_PRESETS["9:16"].min_font
    assert SCROLL_WARNING in lay.warnings
    assert lay.content_height > lay.code_rect.h
    # Visible area is a whole number of lines and as tall as the safe area allows.
    visible = lay.code_rect.h / lay.line_height
    assert visible == pytest.approx(round(visible))
    assert lay.safe_rect.h - lay.line_height < lay.card_rect.h <= lay.safe_rect.h


def test_long_script_example_scrolls_in_every_aspect() -> None:
    code = read_source(next(p for p in EXAMPLES if p.name == "long_script.py"))
    assert all(layout_for(code, aspect).scroll for aspect in Aspect)


def test_fitting_code_does_not_scroll() -> None:
    lay = layout_for("\n".join(f"x{i} = {i}" for i in range(20)), Aspect.portrait)
    assert not lay.scroll
    assert lay.content_height == pytest.approx(lay.code_rect.h)


def test_too_wide_for_portrait_is_rejected_with_line_number() -> None:
    code = "a = 1\n" + "b = '" + "x" * 70 + "'"
    with pytest.raises(
        UserFacingError, match="Line 2 is too wide for this format; shorten it or use 16:9"
    ):
        layout_for(code, Aspect.portrait)
    assert layout_for(code, Aspect.landscape).font_size >= config.ASPECT_PRESETS["16:9"].min_font


def test_max_line_length_always_fits_landscape() -> None:
    lay = layout_for("x" * config.MAX_LINE_LENGTH + "\n" + "y\n" * 59, Aspect.landscape)
    assert lay.font_size >= config.ASPECT_PRESETS["16:9"].min_font


def test_preview_is_half_scale() -> None:
    code = "def f(x):\n    return x * 2\n"
    full = layout_for(code, Aspect.portrait)
    small = layout_for(code, Aspect.portrait, preview=True)
    assert (small.width, small.height) == (540, 960)
    assert small.unit == pytest.approx(0.5)
    assert small.font_size == pytest.approx(full.font_size / 2)
    assert small.card_rect.w == pytest.approx(full.card_rect.w / 2)


def test_glyph_positions_follow_monospace_grid() -> None:
    lay = layout_for("def f():\n    return 1")
    second = lay.lines[1]
    assert second.number == 2
    assert second.text == "    return 1"
    ret = next(r for r in second.runs if r.text == "return")
    assert ret.column == 4
    assert lay.column_x(4) == pytest.approx(lay.code_x + 4 * lay.char_advance)
    assert second.top - lay.lines[0].top == pytest.approx(lay.line_height)
    assert lay.lines[0].top < lay.lines[0].baseline < lay.lines[0].top + lay.line_height


def test_gutter_widens_for_three_digit_line_counts() -> None:
    assert layout_for("x\n" * 9).gutter_digits == 2
    # 100+ lines is beyond MAX_LINES, so exercise compute_layout directly.
    lines = tokenize("\n".join(["x"] * 100))
    assert compute_layout(lines, Aspect.landscape).gutter_digits == 3


def test_missing_glyphs_warn_and_keep_one_cell_each() -> None:
    lay = layout_for('s = "😀中"', Aspect.landscape)
    assert MISSING_GLYPH_WARNING in lay.warnings
    assert lay.lines[0].text == f's = "{config.MISSING_GLYPH_REPLACEMENT * 2}"'
