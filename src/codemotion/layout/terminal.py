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

    @property
    def line_count(self) -> int:
        """Lines on screen once finished: command, output, final prompt."""
        return len(self.output) + (2 if self.command is not None else 0)

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
    if len(output) > config.TERMINAL_MAX_LINES:
        raise UserFacingError(
            f"{label} has {len(output)} output lines; the limit is {config.TERMINAL_MAX_LINES}.",
            "terminal",
        )
    replaced = 0
    shown: list[str] = []
    for line in output:
        if len(line) > config.TERMINAL_MAX_LINE:
            raise UserFacingError(
                f"{label}: output lines can be at most {config.TERMINAL_MAX_LINE} characters.",
                "terminal",
            )
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
    columns = max((len(row) for row in rows), default=0)
    font = element.size * unit
    if columns:
        fit = (width - 2 * padding) / (columns * mono.advance)
        if fit < font * config.TERMINAL_MIN_FONT_FACTOR:
            widest = max(range(len(rows)), key=lambda i: len(rows[i]))
            what = "the command" if command is not None and widest == len(rows) - 1 else (
                f"output line {widest + 1}"
            )  # fmt: skip
            raise UserFacingError(
                f"{label}: {what} is too wide; shorten it or make the terminal wider.",
                "terminal",
            )
        if fit < font:
            warnings.append(f"{label}: the text was made smaller to fit the width.")
            font = fit

    line_h = font * config.TERMINAL_LINE_HEIGHT_FACTOR
    title_h = config.TERMINAL_TITLE_HEIGHT * unit
    n_lines = len(shown) + (2 if command is not None else 0)
    height = title_h + 2 * padding + max(1, n_lines) * line_h

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
        ),
        warnings,
    )
