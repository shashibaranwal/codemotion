"""Layout engine (SPEC 5.1, 5.2): font size, card rect and glyph positions.

The result is plain data; scenes turn it into drawing calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from codemotion import config
from codemotion.analysis.tokenize import Line
from codemotion.errors import UserFacingError
from codemotion.geometry import Rect
from codemotion.layout.fonts import FontRole, metrics, replace_missing_glyphs
from codemotion.models import Aspect

SCROLL_WARNING = "Code is long; it will scroll."
MISSING_GLYPH_WARNING = (
    "Some characters (e.g. emoji) aren't in the code font and are shown as empty boxes."
)


@dataclass(frozen=True)
class Run:
    """A token placed on the monospace grid; ``column`` is its first character's cell."""

    text: str
    kind: str
    column: int


@dataclass(frozen=True)
class LineLayout:
    number: int  # 1-based
    runs: tuple[Run, ...]
    top: float  # top of the line box
    baseline: float

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    unit: float  # px per design px (short edge / 1080)
    safe_rect: Rect
    card_rect: Rect
    title_bar_rect: Rect
    code_rect: Rect  # visible code area (clip), inside the padding
    font_size: float
    line_height: float
    char_advance: float
    gutter_digits: int
    gutter_right: float  # x where right-aligned line numbers end
    code_x: float  # x of column 0
    content_height: float  # height of all lines; > code_rect.h when scrolling
    scroll: bool
    lines: tuple[LineLayout, ...]
    warnings: tuple[str, ...]

    def column_x(self, column: int) -> float:
        return self.code_x + column * self.char_advance


def _too_wide_message(line_number: int, aspect: Aspect) -> str:
    hint = "shorten it" if aspect is Aspect.landscape else "shorten it or use 16:9"
    return f"Line {line_number} is too wide for this format; {hint}."


def safe_rect(aspect: Aspect, preview: bool = False) -> Rect:
    """The area inside the social-app safe margins, in output pixels."""
    width, height = config.frame_size(aspect.value, preview)
    sa = config.ASPECT_PRESETS[aspect.value].safe_area
    return Rect(
        width * sa.left,
        height * sa.top,
        width * (1 - sa.left - sa.right),
        height * (1 - sa.top - sa.bottom),
    )


def compute_layout(
    lines: list[Line], aspect: Aspect, preview: bool = False, region: Rect | None = None
) -> Layout:
    """Fit the code card into ``region`` (output pixels; default: the safe area)."""
    preset = config.ASPECT_PRESETS[aspect.value]
    width, height = config.frame_size(aspect.value, preview)
    unit = min(width, height) / config.REFERENCE_SHORT_EDGE
    safe = safe_rect(aspect, preview)
    area = region or safe
    mono = metrics(FontRole.mono)

    # Replace glyphs the font can't draw so every character occupies exactly one cell.
    warnings: list[str] = []
    replaced_total = 0
    display: list[list[tuple[str, str]]] = []
    for line in lines:
        runs = []
        for token in line.tokens:
            text, replaced = replace_missing_glyphs(token.text, FontRole.mono)
            replaced_total += replaced
            runs.append((text, token.kind))
        display.append(runs)
    if replaced_total:
        warnings.append(MISSING_GLYPH_WARNING)

    n_lines = max(1, len(lines))
    lengths = [sum(len(text) for text, _ in runs) for runs in display]
    max_cols = max(lengths, default=0)
    digits = max(config.GUTTER_MIN_DIGITS, len(str(n_lines)))

    padding = config.CODE_PADDING * unit
    title_h = config.TITLE_BAR_HEIGHT * unit
    avail_w = area.w - 2 * padding
    avail_h = area.h - title_h - 2 * padding
    min_font = preset.min_font * unit
    max_font = preset.max_font * unit

    cells = digits + config.GUTTER_GAP_CHARS + max_cols
    fit_w = avail_w / (cells * mono.advance)
    fit_h = avail_h / (n_lines * config.LINE_HEIGHT_FACTOR)
    if fit_w < min_font:
        raise UserFacingError(_too_wide_message(lengths.index(max_cols) + 1, aspect), "code")

    font_size = min(fit_w, fit_h, max_font)
    scroll = font_size < min_font  # only the height can force this; width was checked above
    if scroll:
        font_size = min_font
        warnings.append(SCROLL_WARNING)

    line_h = font_size * config.LINE_HEIGHT_FACTOR
    content_h = n_lines * line_h
    # When scrolling, show a whole number of lines so none is cut in half at rest.
    code_h = math.floor(avail_h / line_h) * line_h if scroll else content_h
    card_h = title_h + 2 * padding + code_h
    card = Rect(area.x, area.y + (area.h - card_h) / 2, area.w, card_h)
    title_bar = Rect(card.x, card.y, card.w, title_h)
    code_rect = Rect(card.x + padding, card.y + title_h + padding, avail_w, code_h)

    advance = mono.advance * font_size
    gutter_right = code_rect.x + digits * advance
    code_x = gutter_right + config.GUTTER_GAP_CHARS * advance
    baseline_offset = (line_h - (mono.ascent + mono.descent) * font_size) / 2
    baseline_offset += mono.ascent * font_size

    placed = []
    for index, runs in enumerate(display):
        column = 0
        positioned = []
        for text, kind in runs:
            positioned.append(Run(text, kind, column))
            column += len(text)
        top = code_rect.y + index * line_h
        placed.append(LineLayout(index + 1, tuple(positioned), top, top + baseline_offset))

    return Layout(
        width=width,
        height=height,
        unit=unit,
        safe_rect=safe,
        card_rect=card,
        title_bar_rect=title_bar,
        code_rect=code_rect,
        font_size=font_size,
        line_height=line_h,
        char_advance=advance,
        gutter_digits=digits,
        gutter_right=gutter_right,
        code_x=code_x,
        content_height=content_h,
        scroll=scroll,
        lines=tuple(placed),
        warnings=tuple(warnings),
    )
