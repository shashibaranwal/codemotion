"""Terminal element: a panel where a command is typed and its output prints line by line."""

from __future__ import annotations

from codemotion import config
from codemotion.geometry import Color, Rect
from codemotion.layout.fonts import FontRole, measure, metrics
from codemotion.layout.terminal import TerminalLayout
from codemotion.render.canvas import Canvas, Paint
from codemotion.scenes.base import SceneContext
from codemotion.scenes.cursor import cursor_visible
from codemotion.scenes.elements import ElementState
from codemotion.themes.loader import Theme


class TerminalDrawable:
    def __init__(self, layout: TerminalLayout, theme: Theme, title: str) -> None:
        self.layout = layout
        self.theme = theme
        self.title = title

    def bounds(self) -> Rect:
        lay = self.layout
        return Rect(-lay.width / 2, -lay.height / 2, lay.width, lay.height)

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        lay, theme = self.layout, self.theme
        t = frame / ctx.fps
        u = lay.unit
        panel = self.bounds()
        radius = config.TERMINAL_RADIUS * u
        canvas.round_rect(panel, radius, Paint(color=theme.terminal.fill))

        # Title bar: dots on the left, title centred, separator.
        bar_cy = panel.y + lay.title_height / 2
        for i, color in enumerate(theme.chrome.dots):
            cx = panel.x + (22 + i * 20) * u
            canvas.circle(cx, bar_cy, 6 * u, Paint(color=color))
        size = config.TERMINAL_TITLE_FONT_SIZE * u
        sans = metrics(FontRole.sans)
        title_w = measure(self.title, FontRole.sans, size)
        canvas.text_blob(
            canvas.make_text_blob(self.title, FontRole.sans, size),
            -title_w / 2,
            bar_cy + (sans.ascent - sans.descent) * size / 2,
            Paint(color=theme.chrome.tab_text),
        )
        separator = Rect(panel.x, panel.y + lay.title_height - u, panel.w, u)
        canvas.rect(separator, Paint(color=theme.card.border))

        mono = metrics(FontRole.mono)
        x0 = panel.x + lay.padding
        top = panel.y + lay.title_height + lay.padding
        offset = (lay.line_height - (mono.ascent + mono.descent) * lay.font_size) / 2
        baseline = top + offset + mono.ascent * lay.font_size

        # Rows above ``first`` have scrolled off the top; ``row`` counts from the first
        # row ever printed.
        first = lay.first_row(t)

        def text(value: str, column: int, row: int, color: Color) -> None:
            if value.strip() and first <= row < first + lay.visible_rows:
                blob = canvas.make_text_blob(value, FontRole.mono, lay.font_size)
                x = x0 + column * lay.char_advance
                y = baseline + (row - first) * lay.line_height
                canvas.text_blob(blob, x, y, Paint(color=color))

        def cursor(column: int, row: int, since: float) -> None:
            if first <= row < first + lay.visible_rows and cursor_visible(
                t * ctx.fps, since * ctx.fps, ctx.fps
            ):
                height = lay.font_size * config.CURSOR_HEIGHT_FACTOR
                y = top + (row - first) * lay.line_height + (lay.line_height - height) / 2
                x = x0 + column * lay.char_advance
                canvas.rect(Rect(x, y, lay.char_advance, height), Paint(color=theme.text.cursor))

        def at_cell(offset: int) -> tuple[int, int]:
            """(column, row) of the command line's ``offset``-th cell, wrapped."""
            return offset % lay.columns, offset // lay.columns

        prompt = lay.prompt + " "
        if lay.command is not None:
            text(lay.prompt, 0, 0, theme.terminal.prompt)
            typed = lay.typed(t)
            command = lay.command[:typed]
            for row in range(lay.command_rows):  # the command's part on each row
                lo = max(row * lay.columns, len(prompt))
                part = command[lo - len(prompt) : (row + 1) * lay.columns - len(prompt)]
                text(part, lo - row * lay.columns, row, theme.text.default)
            if t < lay.reveal_start:
                cursor(*at_cell(len(prompt)), -1.0)  # blinking, waiting
            elif not lay.line_times or t < lay.line_times[0]:
                last_key = min(t, lay.type_end) if typed else lay.reveal_start
                cursor(*at_cell(len(prompt) + typed), last_key)
        for index, (line, at) in enumerate(lay.output_rows):
            if t >= at:
                text(line, 0, lay.command_rows + index, theme.terminal.text)
        if lay.command is not None and t >= lay.end:
            final = lay.command_rows + len(lay.output_rows)
            text(lay.prompt, 0, final, theme.terminal.prompt)
            cursor(len(prompt), final, lay.end)

        # 1 px border on top, like the code card.
        bw = config.CARD_BORDER_WIDTH * u
        canvas.round_rect(
            panel.inset(bw / 2), radius - bw / 2, Paint(color=theme.card.border, stroke_width=bw)
        )
