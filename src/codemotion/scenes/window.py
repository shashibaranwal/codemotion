"""Editor card: rounded card, title bar with window dots and filename tab."""

from __future__ import annotations

from codemotion import config
from codemotion.geometry import Rect
from codemotion.layout.fonts import FontRole, measure, metrics
from codemotion.layout.layout import Layout
from codemotion.render.canvas import Canvas, Paint
from codemotion.scenes.base import SceneContext
from codemotion.themes.loader import Theme


class Window:
    def __init__(self, theme: Theme, layout: Layout, filename: str) -> None:
        self.theme = theme
        self.layout = layout
        self.filename = filename

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        u = ctx.unit
        card = self.layout.card_rect
        bar = self.layout.title_bar_rect
        radius = config.CARD_RADIUS * u

        # The drop shadow is part of the backdrop (scenes/background.py).
        canvas.round_rect(card, radius, Paint(color=self.theme.card.fill))

        # Title bar: separator, window dots, filename centred.
        sep_h = config.TITLE_BAR_SEPARATOR_WIDTH * u
        canvas.rect(
            Rect(card.x, bar.bottom - sep_h, card.w, sep_h), Paint(color=self.theme.card.border)
        )
        cy = bar.y + bar.h / 2
        for i, color in enumerate(self.theme.chrome.dots):
            cx = card.x + (config.TITLE_BAR_DOT_INSET + i * config.TITLE_BAR_DOT_SPACING) * u
            canvas.circle(cx, cy, config.TITLE_BAR_DOT_RADIUS * u, Paint(color=color))

        size = config.TITLE_BAR_TAB_FONT_SIZE * u
        sans = metrics(FontRole.sans)
        text_w = measure(self.filename, FontRole.sans, size)
        baseline = cy + (sans.ascent - sans.descent) * size / 2
        blob = canvas.make_text_blob(self.filename, FontRole.sans, size)
        canvas.text_blob(
            blob, bar.x + (bar.w - text_w) / 2, baseline, Paint(color=self.theme.chrome.tab_text)
        )

        # 1 px inner border, drawn on top so it stays crisp.
        bw = config.CARD_BORDER_WIDTH * u
        canvas.round_rect(
            card.inset(bw / 2),
            radius - bw / 2,
            Paint(color=self.theme.card.border, stroke_width=bw),
        )
