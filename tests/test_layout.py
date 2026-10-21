from __future__ import annotations

from pathlib import Path

import pytest
from conftest import EXAMPLES, read_source

from codemotion import config
from codemotion.analysis.normalize import normalize
from codemotion.analysis.tokenize import tokenize
from codemotion.errors import UserFacingError
from codemotion.geometry import Rect
from codemotion.layout.layout import (
    MISSING_GLYPH_WARNING,
    SCROLL_WARNING,
    WRAP_WARNING,
    Layout,
    compute_layout,
)
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


def test_too_wide_line_wraps_at_the_minimum_font() -> None:
    code = "a = 1\n" + "result = compute(" + ", ".join(f"arg{i}" for i in range(10)) + ")\nz = 2"
    lay = layout_for(code, Aspect.portrait)
    assert WRAP_WARNING in lay.warnings
    assert lay.font_size == pytest.approx(config.ASPECT_PRESETS["9:16"].min_font)
    assert lay.line_count == 3 and len(lay.lines) > 3
    first, last = lay.rows_of(2)
    assert first == 1 and last > first and lay.rows_of(3) == (last + 1, last + 1)
    rows = lay.lines[first : last + 1]
    assert [r.number for r in rows] == [2] * len(rows)
    assert [r.continuation for r in rows] == [False] + [True] * (len(rows) - 1)
    # Every row fits the card, breaks fall after ", " and continuation rows hang 4 deep.
    for row in rows:
        right = lay.column_x(row.runs[-1].column + len(row.runs[-1].text))
        assert right <= lay.code_rect.right + 1e-6
    assert all(r.text.startswith("    ") and not r.text.startswith("     ") for r in rows[1:])
    assert all(r.text.rstrip().endswith(",") for r in rows[:-1])
    assert "".join(r.text.strip() + " " for r in rows).strip() == code.split("\n")[1]
    # 16:9 is wide enough for the same line.
    assert WRAP_WARNING not in layout_for(code, Aspect.landscape).warnings


def test_unbreakable_line_wraps_at_the_column_limit() -> None:
    lay = layout_for("x" * 300, Aspect.portrait)
    widths = [len(r.text) for r in lay.lines]
    assert sum(len(r.text.strip()) for r in lay.lines) == 300
    assert len(set(widths[1:-1])) <= 1 and widths[0] >= widths[1]


def test_too_narrow_region_is_refused() -> None:
    narrow = Rect(0, 0, 200, 600)
    with pytest.raises(UserFacingError, match="too narrow"):
        compute_layout(tokenize("x = 1"), Aspect.portrait, region=narrow)


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
    assert layout_for("x\n" * 100, Aspect.landscape).gutter_digits == 3
    assert layout_for("x\n" * 1_000, Aspect.landscape).gutter_digits == 4


def test_missing_glyphs_warn_and_keep_one_cell_each() -> None:
    lay = layout_for('s = "😀中"', Aspect.landscape)
    assert MISSING_GLYPH_WARNING in lay.warnings
    assert lay.lines[0].text == f's = "{config.MISSING_GLYPH_REPLACEMENT * 2}"'
