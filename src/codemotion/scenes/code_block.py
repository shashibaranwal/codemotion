"""Code text, line-number gutter and reveal logic (SPEC 5.4).

A ``Reveal`` decides, per frame and line, how much of the line shows and how. The timing
it uses comes from ``compose``; this module never reads the plan.
"""

from __future__ import annotations

import bisect
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from codemotion import config
from codemotion.analysis.ranges import contiguous_runs
from codemotion.geometry import Rect
from codemotion.layout.fonts import FontRole
from codemotion.layout.layout import Layout
from codemotion.render.canvas import Canvas, Paint
from codemotion.scenes.base import SceneContext
from codemotion.themes.loader import Theme
from codemotion.timeline.easing import ease_out_cubic
from codemotion.timeline.keyframes import Track


@dataclass(frozen=True)
class LineView:
    columns: int | None = None  # visible columns; None shows the whole line
    opacity: float = 1.0
    dy: float = 0.0  # vertical offset in px


FULL = LineView()


class Reveal(Protocol):
    def line_view(self, frame: int, index: int) -> LineView | None:
        """How line ``index`` looks at ``frame``; None hides it (and its line number)."""
        ...


class Revealed:
    """Everything visible (used for stills of the final state)."""

    def line_view(self, frame: int, index: int) -> LineView | None:
        return FULL


class FadeReveal:
    def __init__(self, opacity: Track) -> None:
        self.opacity = opacity

    def line_view(self, frame: int, index: int) -> LineView | None:
        opacity = self.opacity.at(frame)
        return LineView(opacity=opacity) if opacity > 0 else None


class CascadeReveal:
    """Each line fades in and rises into place, staggered."""

    def __init__(self, start: float, stagger: float, duration: float, offset: float) -> None:
        self.start = start  # frames
        self.stagger = stagger  # frames
        self.duration = duration  # frames
        self.offset = offset  # px

    def line_view(self, frame: int, index: int) -> LineView | None:
        p = (frame - (self.start + index * self.stagger)) / self.duration
        if p <= 0:
            return None
        if p >= 1:
            return FULL
        e = ease_out_cubic(p)
        return LineView(opacity=e, dy=self.offset * (1 - e))


@dataclass(frozen=True)
class TypingSchedule:
    """When each line starts and each typed character appears, in (fractional) frames."""

    line_starts: tuple[float, ...]
    indents: tuple[int, ...]
    char_frames: tuple[tuple[float, ...], ...]  # per line, for columns indent .. end
    end: float


def build_typing_schedule(
    lines: list[str], start: float, end: float, fps: int, seed: int
) -> TypingSchedule:
    """Type ``lines`` between frames ``start`` and ``end``.

    Leading indentation appears instantly. A small seeded pause follows each line, so the
    rhythm is identical for the same code on every run.
    """
    rng = random.Random(seed)
    reveal = max(0.0, end - start)
    pauses = [rng.uniform(0, config.TYPE_LINE_END_PAUSE_MAX * fps) for _ in lines[:-1]] + [0.0]
    budget = config.TYPE_PAUSE_SHARE_MAX * reveal
    if sum(pauses) > budget:
        pauses = [p * budget / sum(pauses) for p in pauses]
    indents = tuple(len(line) - len(line.lstrip()) for line in lines)
    chars = sum(len(line) - indent for line, indent in zip(lines, indents, strict=True))
    step = (reveal - sum(pauses)) / chars if chars else 0.0

    t = start
    line_starts: list[float] = []
    char_frames: list[tuple[float, ...]] = []
    for line, indent, pause in zip(lines, indents, pauses, strict=True):
        line_starts.append(t)
        # Clamp so float drift never pushes the last keystroke past the reveal's end.
        times = tuple(min(end, t + (k + 1) * step) for k in range(len(line) - indent))
        char_frames.append(times)
        t = (times[-1] if times else t) + pause
    return TypingSchedule(tuple(line_starts), indents, tuple(char_frames), end)


class TypewriterReveal:
    def __init__(self, schedule: TypingSchedule) -> None:
        self.schedule = schedule

    def line_view(self, frame: int, index: int) -> LineView | None:
        s = self.schedule
        if frame < s.line_starts[index]:
            return None
        typed = bisect.bisect_right(s.char_frames[index], frame)
        if typed == len(s.char_frames[index]):
            return FULL
        return LineView(columns=s.indents[index] + typed)

    def cursor(self, frame: int) -> tuple[int, int, float] | None:
        """(line index, column, frame of the last keystroke) or None before typing starts."""
        s = self.schedule
        line = bisect.bisect_right(s.line_starts, frame) - 1
        if line < 0:
            return None
        times = s.char_frames[line]
        typed = bisect.bisect_right(times, frame)
        last = times[typed - 1] if typed else s.line_starts[line]
        return line, s.indents[line] + typed, last


# --------------------------------------------------------------------------- camera scroll


class Scroller:
    """Vertical scroll offset of the code (px) per frame; 0 when the code fits."""

    def __init__(self, offsets: Sequence[float] = ()) -> None:
        self.offsets = tuple(offsets)

    def offset(self, frame: int) -> float:
        if not self.offsets:
            return 0.0
        return self.offsets[min(max(frame, 0), len(self.offsets) - 1)]


def scroll_target(layout: Layout, first: int, last: int, center: bool = False) -> float:
    """Scroll offset that shows lines ``first..last`` (0-based), in whole lines.

    ``center`` centres them (highlights); otherwise the last one sits at
    ``SCROLL_ANCHOR`` of the visible height (the line being revealed).
    """
    view, line_h = layout.code_rect.h, layout.line_height
    if center:
        target = (first + last + 1) / 2 * line_h - view / 2
    else:
        target = (last + 1) * line_h - view * config.SCROLL_ANCHOR
    target = round(target / line_h) * line_h
    return min(max(target, 0.0), max(0.0, layout.content_height - view))


def follow(
    targets: Sequence[float], fps: int, omega: float = config.SCROLL_SPRING_OMEGA
) -> list[float]:
    """A critically damped spring chasing ``targets`` frame by frame (no overshoot).

    Each step is the exact solution for a target held constant over the frame.
    """
    if not targets:
        return []
    x, v = targets[0], 0.0
    dt = 1 / fps
    decay = math.exp(-omega * dt)
    result = []
    for target in targets:
        delta = x - target
        k = v + omega * delta
        x = target + (delta + k * dt) * decay
        v = (v - omega * k * dt) * decay
        result.append(x)
    return result


@dataclass(frozen=True)
class Spotlight:
    """Highlighted lines (1-based); ``amount`` goes 0 -> 1 -> 0 as it fades in and out."""

    lines: frozenset[int]
    amount: Track


class CodeBlock:
    def __init__(
        self,
        theme: Theme,
        layout: Layout,
        reveal: Reveal | None = None,
        spotlights: list[Spotlight] | None = None,
        scroller: Scroller | None = None,
    ) -> None:
        self.theme = theme
        self.layout = layout
        self.reveal = reveal or Revealed()
        self.spotlights = spotlights or []
        self.scroller = scroller or Scroller()

    @staticmethod
    def _dimming(number: int, active: list[tuple[Spotlight, float]]) -> float:
        """Opacity factor for line ``number``: lines outside an active highlight dim."""
        factor = 1.0
        for spot, amount in active:
            if number not in spot.lines:
                factor = min(factor, 1 - (1 - config.HIGHLIGHT_DIM_OPACITY) * amount)
        return factor

    def _draw_bars(
        self, canvas: Canvas, active: list[tuple[Spotlight, float]], scroll: float
    ) -> None:
        lay = self.layout
        accent = self.theme.accent.color
        bar_w = config.HIGHLIGHT_BAR_WIDTH * lay.unit
        for spot, amount in active:
            for first, last in contiguous_runs(spot.lines):
                top = lay.lines[lay.rows_of(first)[0]].top - scroll
                bottom = lay.lines[lay.rows_of(last)[1]].top + lay.line_height - scroll
                band = Rect(lay.card_rect.x, top, lay.card_rect.w, bottom - top)
                fill = Paint(color=accent, opacity=config.HIGHLIGHT_FILL_OPACITY * amount)
                canvas.rect(band, fill)
                canvas.rect(Rect(band.x, top, bar_w, band.h), Paint(color=accent, opacity=amount))

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        lay = self.layout
        size = lay.font_size
        active = [(s, a) for s in self.spotlights if (a := s.amount.at(frame)) > 0]
        scroll = self.scroller.offset(frame)
        if active:
            canvas.save()
            canvas.clip_rect(Rect(lay.card_rect.x, lay.code_rect.y, lay.card_rect.w,
                                  lay.code_rect.h))  # fmt: skip
            self._draw_bars(canvas, active, scroll)
            canvas.restore()
        canvas.save()
        canvas.clip_rect(lay.code_rect)
        # Lines are evenly spaced, so start at the first one the scroll leaves in view
        # instead of walking past thousands above it every frame.
        first = max(0, int(scroll // lay.line_height))
        for index in range(first, len(lay.lines)):
            line = lay.lines[index]
            top = line.top - scroll
            if top >= lay.code_rect.bottom:
                break
            if top + lay.line_height <= lay.code_rect.y:
                continue
            view = self.reveal.line_view(frame, index)
            if view is None or view.opacity <= 0:
                continue
            opacity = view.opacity * self._dimming(line.number, active)
            baseline = line.baseline - scroll + view.dy
            if not line.continuation:  # wrapped rows share their line's number
                number = str(line.number)
                x = lay.gutter_right - len(number) * lay.char_advance
                canvas.text_blob(
                    canvas.make_text_blob(number, FontRole.mono, size),
                    x,
                    baseline,
                    Paint(color=self.theme.text.gutter, opacity=opacity),
                )
            if view.columns is not None:
                canvas.save()
                visible_w = lay.column_x(view.columns) - lay.code_x
                canvas.clip_rect(Rect(lay.code_x, top + view.dy, visible_w, lay.line_height))
            for run in line.runs:
                if run.text.isspace():
                    continue
                if view.columns is not None and run.column >= view.columns:
                    break
                blob = canvas.make_text_blob(run.text, FontRole.mono, size)
                paint = Paint(color=self.theme.token_color(run.kind), opacity=opacity)
                canvas.text_blob(blob, lay.column_x(run.column), baseline, paint)
            if view.columns is not None:
                canvas.restore()
        canvas.restore()
