"""Scene nodes for script elements: an animated transform around a drawable.

Every element has an anchor (``x``, ``y`` as frame fractions, plus ``ox``/``oy`` pixel
offsets), a scale and rotation about that anchor, an opacity, and an optional ``reveal``
(0-1). Drawables draw centred on the origin; ``ElementNode`` applies the transform.

Groups (``GroupNode``) draw their children inside their own transform, which pivots on the
group's anchor; children keep frame coordinates. A translucent group is composited through
a bounded offscreen layer so it fades as one object.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from codemotion import config
from codemotion.geometry import Color, Rect
from codemotion.layout.fonts import FontRole, measure, metrics
from codemotion.layout.layout import Layout
from codemotion.render.canvas import Canvas, Image, Paint
from codemotion.scenes.base import Node, SceneContext
from codemotion.script.doc import Anim, LineElement, ShapeElement, TextElement
from codemotion.themes.loader import Theme
from codemotion.timeline.easing import resolve_ease
from codemotion.timeline.keyframes import Animation, Track, compile_track

_PROPS = ("x", "y", "ox", "oy", "opacity", "scale", "rotation", "reveal")
_LAYER_MARGIN = 4.0  # px around a group's content, for anti-aliased edges


@dataclass(frozen=True)
class ElementState:
    x: float  # anchor, px
    y: float
    scale: float
    rotation: float  # degrees
    opacity: float
    reveal: float  # 0-1


@dataclass(frozen=True)
class Pivot:
    """A group's transform as seen by its children: its tracks and its rest anchor (px)."""

    animated: Animated
    anchor: tuple[float, float]


class Animated:
    """The element's property tracks, its visible frame range and frame size."""

    def __init__(
        self,
        base: dict[str, float],
        anims: list[Anim],
        start: float,
        end: float | None,
        ctx: SceneContext,
        parent: Pivot | None = None,
    ) -> None:
        fps = ctx.fps
        self.ctx = ctx
        self.parent = parent
        self.first = round(start * fps)
        self.last = math.inf if end is None else round(end * fps)  # exclusive
        self.tracks: dict[str, Track] = {}
        for prop in _PROPS:
            mine = [
                Animation(a.start, a.length, a.to, a.by, a.from_, resolve_ease(a.ease))
                for a in anims
                if a.prop == prop
            ]
            self.tracks[prop] = compile_track(base[prop], mine, fps)

    def state(self, frame: int) -> ElementState | None:
        if not self.first <= frame < self.last:
            return None
        t = self.tracks
        ctx = self.ctx
        return ElementState(
            x=t["x"].at(frame) * ctx.width + t["ox"].at(frame) * ctx.unit,
            y=t["y"].at(frame) * ctx.height + t["oy"].at(frame) * ctx.unit,
            scale=max(0.0, t["scale"].at(frame)),
            rotation=t["rotation"].at(frame),
            opacity=max(0.0, min(1.0, t["opacity"].at(frame))),
            reveal=max(0.0, min(1.0, t["reveal"].at(frame))),
        )

    def shadow_state(self, frame: int) -> tuple[float, float, float, float] | None:
        """(x, y, scale, opacity) on the frame, through any enclosing groups."""
        state = self.state(frame)
        if state is None:
            return None
        x, y, scale, opacity = state.x, state.y, state.scale, state.opacity
        pivot = self.parent
        while pivot is not None:
            group = pivot.animated.state(frame)
            if group is None:
                return None
            a = math.radians(group.rotation)
            dx, dy = (x - pivot.anchor[0]) * group.scale, (y - pivot.anchor[1]) * group.scale
            x = group.x + dx * math.cos(a) - dy * math.sin(a)
            y = group.y + dx * math.sin(a) + dy * math.cos(a)
            scale *= group.scale
            opacity *= group.opacity
            pivot = pivot.animated.parent
        return x, y, scale, opacity


class Drawable(Protocol):
    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        """Draw centred on the origin (the element's anchor)."""
        ...

    def bounds(self) -> Rect:
        """Everything this draws, relative to the anchor, before scale and rotation."""
        ...


class SceneNode(Node, Protocol):
    def bounds(self, frame: int) -> Rect | None:
        """Bounding box on the parent's coordinates at ``frame``; None when not drawn."""
        ...


class ElementNode:
    def __init__(self, animated: Animated, drawable: Drawable) -> None:
        self.animated = animated
        self.drawable = drawable

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        state = self.animated.state(frame)
        if state is None or state.opacity <= 0 or state.scale <= 0:
            return
        canvas.save_alpha(state.opacity)
        canvas.translate(state.x, state.y)
        if state.rotation:
            canvas.rotate(state.rotation)
        if state.scale != 1.0:
            canvas.scale(state.scale)
        self.drawable.draw(canvas, frame, ctx, state)
        canvas.restore()

    def bounds(self, frame: int) -> Rect | None:
        state = self.animated.state(frame)
        if state is None or state.opacity <= 0 or state.scale <= 0:
            return None
        return self.drawable.bounds().transformed(state.x, state.y, state.scale, state.rotation)


class GroupNode:
    """Children drawn inside the group's transform, pivoting on its rest anchor."""

    def __init__(self, animated: Animated, anchor: tuple[float, float]) -> None:
        self.animated = animated
        self.anchor = anchor
        self.children: list[SceneNode] = []

    def _transform(self, canvas: Canvas, state: ElementState) -> None:
        canvas.translate(state.x, state.y)
        if state.rotation:
            canvas.rotate(state.rotation)
        if state.scale != 1.0:
            canvas.scale(state.scale)
        canvas.translate(-self.anchor[0], -self.anchor[1])

    def content_bounds(self, frame: int) -> Rect | None:
        boxes = [b for child in self.children if (b := child.bounds(frame)) is not None]
        if not boxes:
            return None
        result = boxes[0]
        for box in boxes[1:]:
            result = result.union(box)
        return result

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        state = self.animated.state(frame)
        if state is None or state.opacity <= 0 or state.scale <= 0:
            return
        canvas.save()
        self._transform(canvas, state)
        layered = state.opacity < 1
        if layered:
            content = self.content_bounds(frame)
            if content is None:
                canvas.restore()
                return
            canvas.save_layer(state.opacity, content.inset(-_LAYER_MARGIN / max(state.scale, 1e-3)))
        for child in self.children:
            child.draw(canvas, frame, ctx)
        if layered:
            canvas.restore()
        canvas.restore()

    def bounds(self, frame: int) -> Rect | None:
        state = self.animated.state(frame)
        if state is None or state.opacity <= 0 or state.scale <= 0:
            return None
        content = self.content_bounds(frame)
        if content is None:
            return None
        local = Rect(content.x - self.anchor[0], content.y - self.anchor[1], content.w, content.h)
        return local.transformed(state.x, state.y, state.scale, state.rotation)


# --------------------------------------------------------------------------- drawables

_FONT_ROLES = {
    "sans": FontRole.sans,
    "sans_bold": FontRole.sans_semibold,
    "mono": FontRole.mono,
    "mono_bold": FontRole.mono_bold,
}


class TextDrawable:
    def __init__(self, element: TextElement, theme: Theme, unit: float) -> None:
        self.lines = element.text.split("\n")
        self.role = _FONT_ROLES[element.font]
        self.size = element.size * unit
        self.color = theme.named_color(element.color)
        self.align = element.align
        self.line_height = element.size * element.line_height * unit
        self.widths = [measure(line, self.role, self.size) for line in self.lines]
        self.total = sum(len(line) for line in self.lines)

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        m = metrics(self.role)
        block = self.line_height * len(self.lines)
        baseline = -block / 2 + (self.line_height - (m.ascent + m.descent) * self.size) / 2
        baseline += m.ascent * self.size
        remaining = round(state.reveal * self.total)
        paint = Paint(color=self.color)
        for line, width in zip(self.lines, self.widths, strict=True):
            shown = line[: max(0, remaining)]
            remaining -= len(line)
            if shown.strip():
                x = {"left": 0.0, "center": -width / 2, "right": -width}[self.align]
                blob = canvas.make_text_blob(shown, self.role, self.size)
                canvas.text_blob(blob, x, baseline, paint)
            baseline += self.line_height

    def bounds(self) -> Rect:
        width = max(self.widths, default=0.0)
        block = max(self.line_height, self.size) * len(self.lines)
        x = {"left": 0.0, "center": -width / 2, "right": -width}[self.align]
        return Rect(x, -block / 2, width, block).inset(-self.size * 0.2)


class ShapeDrawable:
    """A filled and/or outlined rectangle or circle; the outline stays inside its size."""

    def __init__(self, element: ShapeElement, theme: Theme, unit: float) -> None:
        self.shape = element.shape
        self.w = element.width * unit
        self.h = element.height * unit
        self.radius = element.radius * unit
        self.fill: Color | None = (
            None if element.color is None else theme.named_color(element.color)
        )
        self.stroke: Color | None = (
            None if element.stroke is None else theme.named_color(element.stroke)
        )
        self.stroke_width = min(element.stroke_width * unit, self.w / 2, self.h / 2)

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        rect = Rect(-self.w / 2, -self.h / 2, self.w, self.h)
        if self.fill is not None:
            self._shape(canvas, rect, self.radius, Paint(color=self.fill))
        if self.stroke is not None:
            half = self.stroke_width / 2
            paint = Paint(color=self.stroke, stroke_width=self.stroke_width)
            self._shape(canvas, rect.inset(half), max(0.0, self.radius - half), paint)

    def _shape(self, canvas: Canvas, rect: Rect, radius: float, paint: Paint) -> None:
        if self.shape == "circle":
            canvas.circle(0, 0, rect.w / 2, paint)
        else:
            canvas.round_rect(rect, radius, paint)

    def bounds(self) -> Rect:
        return Rect(-self.w / 2, -self.h / 2, self.w, self.h)


class LineDrawable:
    """A line from one point to the other, drawn up to ``reveal``; optional arrowhead."""

    def __init__(self, element: LineElement, theme: Theme, ctx: SceneContext) -> None:
        dx = (element.x2 - element.x1) * ctx.width / 2
        dy = (element.y2 - element.y1) * ctx.height / 2
        self.p1 = (-dx, -dy)
        self.p2 = (dx, dy)
        self.width = element.width * ctx.unit
        self.color = theme.named_color(element.color)
        self.arrow = element.arrow
        self.head = max(config.ARROW_MIN_LENGTH * ctx.unit, self.width * config.ARROW_LENGTH_FACTOR)

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        (x1, y1), (x2, y2) = self.p1, self.p2
        length = math.hypot(x2 - x1, y2 - y1)
        drawn = length * state.reveal
        if drawn <= 0 or length == 0:
            return
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        tip = (x1 + ux * drawn, y1 + uy * drawn)
        head = min(self.head, drawn) if self.arrow else 0.0
        end = (tip[0] - ux * head * 0.8, tip[1] - uy * head * 0.8)
        if head < drawn:
            paint = Paint(color=self.color, stroke_width=self.width, round_cap=True)
            canvas.line(x1, y1, end[0], end[1], paint)
        if head > 0:
            back = (tip[0] - ux * head, tip[1] - uy * head)
            half = head * 0.6
            canvas.polygon(
                [
                    tip,
                    (back[0] - uy * half, back[1] + ux * half),
                    (back[0] + uy * half, back[1] - ux * half),
                ],
                Paint(color=self.color),
            )

    def bounds(self) -> Rect:
        (x1, y1), (x2, y2) = self.p1, self.p2
        pad = self.width + (self.head if self.arrow else 0.0)
        return Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)).inset(-pad)


class ImageDrawable:
    def __init__(self, pixels: npt.NDArray[np.uint8], width: float, unit: float) -> None:
        self.pixels = pixels
        h, w = pixels.shape[:2]
        self.w = width * unit
        self.h = self.w * h / w
        self._handle: Image | None = None
        self._owner: Canvas | None = None

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        if self._handle is None or self._owner is not canvas:
            self._handle = canvas.make_image(self.pixels)
            self._owner = canvas
        canvas.image(self._handle, Rect(-self.w / 2, -self.h / 2, self.w, self.h), 1.0)

    def bounds(self) -> Rect:
        return Rect(-self.w / 2, -self.h / 2, self.w, self.h)


Zoom = Callable[[int], tuple[float, float, float]]  # frame -> (factor, pivot x, pivot y)


class CodeDrawable:
    """The editor card (window chrome, code, cursor) centred on the anchor.

    ``zoom`` (highlight zoom) scales the card about a pivot in layout coordinates.
    """

    def __init__(
        self, layout: Layout, parts: list[Node], zoom: Zoom | None = None, max_zoom: float = 1.0
    ) -> None:
        card = layout.card_rect
        self.card = card
        self.center = (card.x + card.w / 2, card.y + card.h / 2)
        self.parts = parts
        self.zoom = zoom
        self.max_zoom = max_zoom

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext, state: ElementState) -> None:
        canvas.save()
        canvas.translate(-self.center[0], -self.center[1])
        if self.zoom is not None:
            factor, px, py = self.zoom(frame)
            if factor != 1.0:
                canvas.translate(px, py)
                canvas.scale(factor)
                canvas.translate(-px, -py)
        for part in self.parts:
            part.draw(canvas, frame, ctx)
        canvas.restore()

    def bounds(self) -> Rect:
        card = Rect(-self.card.w / 2, -self.card.h / 2, self.card.w, self.card.h)
        grow = (self.max_zoom - 1) * (self.card.w + self.card.h)
        return card.inset(-grow)
