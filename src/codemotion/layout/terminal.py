"""Terminal element layout and timing: panel size, font size, and when each line appears.

Coordinates are relative to the panel centre (the element's anchor), in output pixels;
times are seconds from the start of the video.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.layout.fonts import FontRole, metrics, replace_missing_glyphs
from codemotion.layout.layout import MISSING_GLYPH_WARNING, safe_rect
from codemotion.models import Aspect
from codemotion.script.doc import TerminalElement


@dataclass(frozen=True)
class TerminalLayout:
    width: float
    height: float
    unit: float
    font_size: float
    line_height: float
    char_advance: float
    padding: float
    title_height: float
    prompt: str
    command: str | None
    output: tuple[str, ...]
    # Timing, in seconds.
    reveal_start: float  # the command starts typing (or output starts printing)
    type_end: float  # the command is fully typed
    line_times: tuple[float, ...]  # when each output line appears
    end: float  # the final prompt appears (everything shown)
    # Rows, like a real terminal: lines wider than ``columns`` wrap, and once more rows
    # have printed than the panel holds (``visible_rows``) the oldest scroll off the top.
    columns: int = 0
    command_rows: int = 0  # rows the prompt and command take (0 without a command)
    output_rows: tuple[tuple[str, float], ...] = ()  # (text, when it appears)
    visible_rows: int = 0

    @property
    def line_count(self) -> int:
        """Rows once finished: command, output, final prompt."""
        return self.command_rows + len(self.output_rows) + (1 if self.command is not None else 0)

    def rows_at(self, t: float) -> int:
        """Rows printed by time ``t`` (the command's rows count from the start)."""
        printed = sum(1 for _, at in self.output_rows if at <= t)
        final = 1 if self.command is not None and t >= self.end else 0
        return self.command_rows + printed + final

    def first_row(self, t: float) -> int:
        """Rows scrolled off the top at time ``t``."""
        return max(0, self.rows_at(t) - self.visible_rows)

    def typed(self, t: float) -> int:
        """Characters of the command shown at time ``t``."""
        if self.command is None or t < self.reveal_start:
            return 0
        if t >= self.type_end:
            return len(self.command)
        return min(len(self.command), math.floor((t - self.reveal_start) * self.cps))

    @property
    def cps(self) -> float:
        span = self.type_end - self.reveal_start
        return len(self.command or "") / span if span > 0 else math.inf


def _clean(text: str) -> list[str]:
    lines = [line.replace("\t", " " * config.TAB_SIZE).rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def layout_terminal(
    element: TerminalElement, aspect: Aspect, duration: float, preview: bool = False
) -> tuple[TerminalLayout, list[str]]:
    """Size the panel and schedule the typing; raises ``UserFacingError`` if it can't fit."""
    label = f"Terminal '{element.id}'"
    width_px, height_px = config.frame_size(aspect.value, preview)
    unit = min(width_px, height_px) / config.REFERENCE_SHORT_EDGE
    warnings: list[str] = []

    output = _clean(element.output)
    replaced = 0
    shown: list[str] = []
    for line in output:
        text, count = replace_missing_glyphs(line, FontRole.mono)
        shown.append(text)
        replaced += count
    command = None
    if element.command is not None:
        command, count = replace_missing_glyphs(element.command, FontRole.mono)
        replaced += count
    if replaced:
        warnings.append(f"{label}: {MISSING_GLYPH_WARNING}")

    width = element.width * unit if element.width is not None else safe_rect(aspect, preview).w
    padding = config.TERMINAL_PADDING * unit
    mono = metrics(FontRole.mono)
    rows = list(shown)
    if command is not None:
        rows.append(f"{element.prompt} {command} ")  # room for the cursor
    widest = max((len(row) for row in rows), default=0)
    font = element.size * unit
    inner_w = width - 2 * padding
    columns = widest  # everything fits on its own row
    if widest:
        fit = inner_w / (widest * mono.advance)
        if fit < font:
            # Shrink to fit, down to TERMINAL_MIN_FONT_FACTOR; wider lines wrap.
            min_font = font * config.TERMINAL_MIN_FONT_FACTOR
            if fit >= min_font:
                font = fit
                warnings.append(f"{label}: the text was made smaller to fit the width.")
            else:
                font = min_font
                columns = math.floor(inner_w / (mono.advance * font))
                if columns < config.TERMINAL_MIN_COLUMNS:
                    raise UserFacingError(
                        f"{label} is too narrow; make the terminal wider.", "terminal"
                    )
                warnings.append(f"{label}: long lines wrap, as in a real terminal.")

    line_h = font * config.TERMINAL_LINE_HEIGHT_FACTOR
    title_h = config.TERMINAL_TITLE_HEIGHT * unit
    command_rows = 0
    if command is not None:
        # The prompt, the command and room for the cursor after it, wrapped.
        command_rows = (len(element.prompt) + 1 + len(command)) // columns + 1

    start = (
        element.reveal_start
        if element.reveal_start is not None
        else element.start + config.TERMINAL_START_DELAY
    )
    if command is not None:
        type_end = start + len(command) / element.type_speed
        first = type_end + config.TERMINAL_OUTPUT_DELAY
    else:
        type_end = first = start
    line_times = tuple(first + i * element.line_interval for i in range(len(shown)))
    end = (line_times[-1] + element.line_interval) if line_times else first
    visible_until = element.end if element.end is not None else duration
    if end > visible_until:
        warnings.append(f"{label} is still printing when it leaves the screen.")

    output_rows = tuple(
        (line[i : i + columns], at)
        for line, at in zip(shown, line_times, strict=True)
        for i in range(0, max(1, len(line)), columns)
    )
    total_rows = command_rows + len(output_rows) + (1 if command is not None else 0)
    # The panel grows with its rows until it would be taller than the safe area.
    room = safe_rect(aspect, preview).h - title_h - 2 * padding
    visible_rows = max(1, min(total_rows, math.floor(room / line_h)))
    if visible_rows < total_rows:
        warnings.append(f"{label}: older lines scroll up as new ones print.")
    height = title_h + 2 * padding + visible_rows * line_h

    return (
        TerminalLayout(
            width=width,
            height=height,
            unit=unit,
            font_size=font,
            line_height=line_h,
            char_advance=mono.advance * font,
            padding=padding,
            title_height=title_h,
            prompt=element.prompt,
            command=command,
            output=tuple(shown),
            reveal_start=start,
            type_end=type_end,
            line_times=line_times,
            end=end,
            columns=columns,
            command_rows=command_rows,
            output_rows=output_rows,
            visible_rows=visible_rows,
        ),
        warnings,
    )
