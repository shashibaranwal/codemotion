"""Block cursor for the typewriter: solid while typing, blinking when idle."""

from __future__ import annotations

from codemotion import config
from codemotion.geometry import Rect
from codemotion.layout.layout import Layout
from codemotion.render.canvas import Canvas, Paint
from codemotion.scenes.base import SceneContext
from codemotion.scenes.code_block import Scroller, TypewriterReveal
from codemotion.themes.loader import Theme


def cursor_visible(frame: float, last_keystroke: float, fps: int) -> bool:
    """Solid right after a keystroke; then on/off every ``CURSOR_BLINK_INTERVAL`` seconds."""
    idle = (frame - last_keystroke) / fps
    if idle < config.CURSOR_IDLE_SECONDS:
        return True
    return (idle % (2 * config.CURSOR_BLINK_INTERVAL)) < config.CURSOR_BLINK_INTERVAL


class Cursor:
    def __init__(
        self,
        theme: Theme,
        layout: Layout,
        typing: TypewriterReveal,
        scroller: Scroller | None = None,
    ) -> None:
        self.theme = theme
        self.layout = layout
        self.typing = typing
        self.scroller = scroller or Scroller()

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        state = self.typing.cursor(frame)
        if state is None:
            return
        line_index, column, last = state
        if not cursor_visible(frame, last, ctx.fps):
            return
        lay = self.layout
        line_top = lay.lines[line_index].top - self.scroller.offset(frame)
        if line_top >= lay.code_rect.bottom or line_top + lay.line_height <= lay.code_rect.y:
            return
        height = lay.font_size * config.CURSOR_HEIGHT_FACTOR
        top = line_top + (lay.line_height - height) / 2
        rect = Rect(lay.column_x(column), top, lay.char_advance, height)
        canvas.rect(rect, Paint(color=self.theme.text.cursor))
