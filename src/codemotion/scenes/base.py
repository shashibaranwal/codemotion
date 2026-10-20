"""Scene graph basics: nodes draw themselves for a given frame index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from codemotion.geometry import Color, Rect
from codemotion.render.canvas import Canvas, Paint
from codemotion.timeline.keyframes import Track


@dataclass(frozen=True)
class SceneContext:
    width: int
    height: int
    unit: float  # px per design px
    fps: int
    total_frames: int


class Node(Protocol):
    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None: ...


@dataclass
class Scene:
    ctx: SceneContext
    clear_color: Color
    nodes: list[Node] = field(default_factory=list)

    def draw(self, canvas: Canvas, frame: int) -> None:
        canvas.clear(self.clear_color)
        for node in self.nodes:
            node.draw(canvas, frame, self.ctx)


class FadeOverlay:
    """Full-frame colour drawn on top at the track's opacity (e.g. the outro fade)."""

    def __init__(self, color: Color, opacity: Track) -> None:
        self.color = color
        self.opacity = opacity

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        opacity = self.opacity.at(frame)
        if opacity > 0:
            canvas.rect(Rect(0, 0, ctx.width, ctx.height), Paint(color=self.color, opacity=opacity))
