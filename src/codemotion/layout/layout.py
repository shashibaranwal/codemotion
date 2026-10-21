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
WRAP_WARNING = "Some lines are too wide for this format; they wrap onto extra rows."
TOO_NARROW_MESSAGE = "The code area is too narrow to show code; make it wider."
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
    """One row of the card: a whole source line, or part of a wrapped one."""

    number: int  # 1-based source line
    runs: tuple[Run, ...]
    top: float  # top of the line box
    baseline: float
    continuation: bool = False  # a wrapped line's later rows (no line number)

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
    content_height: float  # height of all rows; > code_rect.h when scrolling
    scroll: bool
    lines: tuple[LineLayout, ...]  # rows, top to bottom
    warnings: tuple[str, ...]
    line_rows: tuple[tuple[int, int], ...] = ()  # per source line: (first, last) row index

    def column_x(self, column: int) -> float:
        return self.code_x + column * self.char_advance

    @property
    def line_count(self) -> int:
        """Number of source lines (rows can be more when long lines wrap)."""
        return len(self.line_rows)

    def rows_of(self, number: int) -> tuple[int, int]:
        """(first, last) row index of source line ``number`` (1-based)."""
        return self.line_rows[number - 1]


Cell = tuple[str, str]  # (character, token kind)


def _break_at(cells: list[Cell], width: int) -> int:
    """Where to end a row of at most ``width`` cells: after the last space or opening
    bracket/comma in its second half, else exactly at ``width``."""
    for end in range(width, width // 2, -1):
        if cells[end - 1][0] in " ,([{" or cells[end][0] == " ":
            return end
    return width


def _wrap(cells: list[Cell], columns: int) -> list[tuple[int, list[Cell]]]:
    """Split one source line into rows of at most ``columns`` cells: (indent, cells) each.

    Continuation rows hang one indent deeper than the line itself, like Python
    continuation lines; the spaces at a break are dropped.
    """
    if len(cells) <= columns:
        return [(0, cells)]
    indent = len(cells) - len("".join(ch for ch, _ in cells).lstrip())
    hang = min(indent + config.TAB_SIZE, columns // 2)
    rows: list[tuple[int, list[Cell]]] = []
    rest, lead, width = cells, 0, columns
    while len(rest) > width:
        end = _break_at(rest, width)
        row = rest[:end]
        while row and row[-1][0] == " ":
            row = row[:-1]
        rows.append((lead, row))
        rest = rest[end:]
        while rest and rest[0][0] == " ":
            rest = rest[1:]
        lead, width = hang, columns - hang
    if rest:
        rows.append((lead, rest))
    return rows


def _token_runs(tokens: list[tuple[str, str]]) -> tuple[Run, ...]:
    """A line that fits: one run per token, in column order."""
    runs, column = [], 0
    for text, kind in tokens:
        runs.append(Run(text, kind, column))
        column += len(text)
    return tuple(runs)


def _runs(lead: int, cells: list[Cell]) -> tuple[Run, ...]:
    """Group consecutive cells of one kind into runs; ``lead`` blank cells come first."""
    runs = [Run(" " * lead, "text", 0)] if lead else []
    column = lead
    for ch, kind in cells:
        if runs and runs[-1].kind == kind:
            last = runs[-1]
            runs[-1] = Run(last.text + ch, kind, last.column)
        else:
            runs.append(Run(ch, kind, column))
        column += 1
    return tuple(runs)


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

    gutter = digits + config.GUTTER_GAP_CHARS
    fit_w = avail_w / ((gutter + max_cols) * mono.advance)
    # Lines too wide even at the minimum font size wrap onto extra rows.
    wrap_cols = max_cols
    if fit_w < min_font:
        wrap_cols = math.floor(avail_w / (min_font * mono.advance) - gutter)
        if wrap_cols < config.WRAP_MIN_COLUMNS:
            raise UserFacingError(TOO_NARROW_MESSAGE, "code")
        fit_w = min_font
        warnings.append(WRAP_WARNING)
    rows: list[tuple[int, tuple[Run, ...]]] = []  # (source line index, runs)
    for index, runs in enumerate(display):
        if lengths[index] <= wrap_cols:
            rows.append((index, _token_runs(runs)))
            continue
        cells = [(ch, kind) for text, kind in runs for ch in text]
        rows.extend((index, _runs(lead, part)) for lead, part in _wrap(cells, wrap_cols))
    n_rows = max(1, len(rows))

    fit_h = avail_h / (n_rows * config.LINE_HEIGHT_FACTOR)
    font_size = min(fit_w, fit_h, max_font)
    scroll = font_size < min_font  # only the height can force this; width was handled above
    if scroll:
        font_size = min_font
        warnings.append(SCROLL_WARNING)

    line_h = font_size * config.LINE_HEIGHT_FACTOR
    content_h = n_rows * line_h
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
    line_rows: list[tuple[int, int]] = []
    for row, (index, row_runs) in enumerate(rows):
        top = code_rect.y + row * line_h
        continuation = index < len(line_rows)  # this source line already has a row
        if continuation:
            line_rows[index] = (line_rows[index][0], row)
        else:
            line_rows.append((row, row))
        placed.append(LineLayout(index + 1, row_runs, top, top + baseline_offset, continuation))

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
        line_rows=tuple(line_rows),
    )
