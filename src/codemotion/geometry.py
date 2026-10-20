"""Backend-neutral value types shared by layout, themes, scenes and the canvas."""

from __future__ import annotations

import math
import re
from typing import NamedTuple

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class Rect(NamedTuple):
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def inset(self, dx: float, dy: float | None = None) -> Rect:
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    def union(self, other: Rect) -> Rect:
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        return Rect(x0, y0, max(self.right, other.right) - x0, max(self.bottom, other.bottom) - y0)

    def transformed(self, x: float, y: float, scale: float, rotation: float) -> Rect:
        """Bounding box after scaling and rotating (degrees) about the origin, then moving
        the origin to (x, y)."""
        a = math.radians(rotation)
        cos, sin = math.cos(a) * scale, math.sin(a) * scale
        corners = [(cx, cy) for cx in (self.x, self.right) for cy in (self.y, self.bottom)]
        xs = [x + cx * cos - cy * sin for cx, cy in corners]
        ys = [y + cx * sin + cy * cos for cx, cy in corners]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


class Color(NamedTuple):
    """Straight (non-premultiplied) RGBA, each channel 0-255."""

    r: int
    g: int
    b: int
    a: int = 255

    @classmethod
    def from_hex(cls, value: str) -> Color:
        """Parse ``#RRGGBB`` or ``#RRGGBBAA``."""
        if not _HEX_COLOR.match(value):
            raise ValueError(f"'{value}' is not a colour; use #RRGGBB or #RRGGBBAA")
        digits = value[1:]
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        a = int(digits[6:8], 16) if len(digits) == 8 else 255
        return cls(r, g, b, a)

    def with_alpha(self, opacity: float) -> Color:
        """Multiply the alpha channel by ``opacity`` (0-1)."""
        return Color(self.r, self.g, self.b, round(self.a * max(0.0, min(1.0, opacity))))
