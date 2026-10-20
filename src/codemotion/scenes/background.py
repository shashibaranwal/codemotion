"""Backdrop: rotating theme gradient, two drifting colour glows, the card's drop shadow and
seeded film grain.

Everything except the grain is smooth, so it is computed with numpy at a reduced
resolution (``BACKDROP_DOWNSAMPLE``) and block-upscaled; the full-resolution grain, added
as precomputed integer offsets, dithers away any block edges. This keeps the backdrop to a
few milliseconds per frame (see the performance note in ``render/canvas.py``).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from codemotion import config
from codemotion.geometry import Color
from codemotion.render.canvas import Canvas
from codemotion.scenes.base import SceneContext
from codemotion.themes.loader import Theme
from codemotion.timeline.easing import ease_in_out_cubic
from codemotion.timeline.keyframes import Track

FloatArray = npt.NDArray[np.float32]
U8Array = npt.NDArray[np.uint8]

# Each glow drifts along this unit direction over the whole video.
_BLOB_DIRECTIONS = ((0.8, 0.6), (-0.6, -0.8))
_GRAIN_LEVELS = 4  # grain fades in with the backdrop in this many steps
_erf = np.vectorize(math.erf, otypes=[np.float64])


def grain_texture(width: int, height: int, seed: int) -> U8Array:
    """Grey noise 0-255; identical for the same (size, seed) on every platform."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width), dtype=np.uint8)


# (centre x px, centre y px, scale, opacity) of a shadow-casting element at a frame,
# or None while it isn't shown.
ShadowState = Callable[[int], tuple[float, float, float, float] | None]


@dataclass(frozen=True)
class ShadowSpec:
    """A drop shadow for a card-like element, following its position, scale and opacity."""

    width: float
    height: float
    blur: float  # CSS-style blur radius in px (sigma = blur / 2)
    dy: float
    color: Color
    state: ShadowState


def _downsample(width: int, height: int) -> int:
    for f in (config.BACKDROP_DOWNSAMPLE, 2, 1):
        if width % f == 0 and height % f == 0:
            return f
    return 1


def _rgb(color: Color) -> FloatArray:
    return np.array(color[:3], dtype=np.float32)


class Background:
    def __init__(
        self,
        theme: Theme,
        seed: int,
        opacity: Track | None = None,
        shadows: list[ShadowSpec] | None = None,
        solid: Color | None = None,
    ) -> None:
        self.theme = theme
        self.seed = seed
        self.opacity = opacity or Track.constant(1.0)
        self.shadows = shadows or []
        self.solid = solid  # plain colour instead of the theme gradient and glows
        self._size: tuple[int, int] | None = None
        self._full: U8Array | None = None
        self._grain: U8Array | None = None
        self._offsets: dict[int, U8Array] = {}

    # -- full-resolution grain -------------------------------------------------------------

    def _grain_offsets(self, level: int, width: int, height: int) -> U8Array:
        """Signed grain offsets (two's complement in uint8) for one fade level."""
        cached = self._offsets.get(level)
        if cached is not None:
            return cached
        if self._grain is None:
            self._grain = grain_texture(width, height, self.seed)
        strength = config.BG_GRAIN_OPACITY * level / _GRAIN_LEVELS
        signed = np.rint((self._grain.astype(np.float32) - 127.5) * strength).astype(np.int16)
        offsets = np.zeros((height, width, 4), dtype=np.uint8)
        offsets[..., :3] = (signed.astype(np.uint8))[..., None]  # wraps: -1 -> 255
        self._offsets[level] = offsets
        return offsets

    # -- low-resolution smooth layer -------------------------------------------------------

    def _smooth(self, frame: int, ctx: SceneContext, f: int, opacity: float) -> FloatArray:
        h, w = ctx.height // f, ctx.width // f
        xs = (np.arange(w, dtype=np.float32) + 0.5) * f
        ys = (np.arange(h, dtype=np.float32) + 0.5) * f

        # Progress through the whole video drives the slow drift (no wall clock).
        drift = ease_in_out_cubic(frame / max(1, ctx.total_frames - 1))
        angle = math.radians(config.BG_GRADIENT_ANGLE + config.BG_ROTATION_DEG * drift)
        half = math.hypot(ctx.width, ctx.height) / 2
        ux, uy = math.cos(angle), math.sin(angle)
        # t = 0 at (centre - half * u), 1 at (centre + half * u)
        tx = (xs - ctx.width / 2) * np.float32(ux / (2 * half))
        ty = (ys - ctx.height / 2) * np.float32(uy / (2 * half)) + np.float32(0.5)
        t = np.clip(ty[:, None] + tx[None, :], 0.0, 1.0)
        c0, c1 = (_rgb(c) for c in self.theme.background.gradient)
        rgb: FloatArray = c0 + t[..., None] * (c1 - c0)
        if self.solid is not None:
            rgb[...] = _rgb(self.solid)

        if self.solid is None:
            self._add_glows(rgb, ctx, xs, ys, drift)

        if opacity < 1.0:
            rgb *= np.float32(opacity)  # fade in from black
        for shadow in self.shadows:
            self._apply_shadow(shadow, rgb, frame, xs, ys)
        return rgb

    def _add_glows(
        self, rgb: FloatArray, ctx: SceneContext, xs: FloatArray, ys: FloatArray, drift: float
    ) -> None:
        short = min(ctx.width, ctx.height)
        sigma = config.BG_BLOB_SIGMA * short
        travel = config.BG_BLOB_DRIFT * short * drift
        blobs = zip(
            self.theme.background.blobs, config.BG_BLOB_CENTERS, _BLOB_DIRECTIONS, strict=True
        )
        for color, (fx, fy), (dx, dy) in blobs:
            cx, cy = fx * ctx.width + dx * travel, fy * ctx.height + dy * travel
            gx = np.exp(-((xs - cx) ** 2) / np.float32(2 * sigma * sigma))
            gy = np.exp(-((ys - cy) ** 2) / np.float32(2 * sigma * sigma))
            peak = np.float32(color.a / 255 * config.BG_BLOB_OPACITY)
            a = (gy * peak)[:, None] * gx[None, :]
            rgb += (_rgb(color) - rgb) * a[..., None]

    def _apply_shadow(
        self, sh: ShadowSpec, rgb: FloatArray, frame: int, xs: FloatArray, ys: FloatArray
    ) -> None:
        state = sh.state(frame)
        if state is None:
            return
        cx, cy, s, opacity = state
        if opacity <= 0 or s <= 0:
            return
        x0, x1 = cx - sh.width * s / 2, cx + sh.width * s / 2
        y0, y1 = cy - sh.height * s / 2 + sh.dy * s, cy + sh.height * s / 2 + sh.dy * s
        sigma = max(1e-3, sh.blur / 2 * s) * math.sqrt(2)
        # Exact blur of the bounding rectangle (separable); the 28 px corner radius is
        # invisible under a 30 px sigma.
        fx = 0.5 * (_erf((xs - x0) / sigma) - _erf((xs - x1) / sigma))
        fy = 0.5 * (_erf((ys - y0) / sigma) - _erf((ys - y1) / sigma))
        peak = sh.color.a / 255 * min(1.0, opacity)
        a = (fy * peak).astype(np.float32)[:, None] * fx.astype(np.float32)[None, :]
        rgb += (_rgb(sh.color) - rgb) * a[..., None]

    # -- compose ---------------------------------------------------------------------------

    def render(self, frame: int, ctx: SceneContext) -> U8Array:
        """The full backdrop for ``frame`` as opaque RGBA (a reused buffer)."""
        width, height = ctx.width, ctx.height
        if self._size != (width, height):
            self._size = (width, height)
            self._full = np.empty((height, width, 4), dtype=np.uint8)
            self._grain = None
            self._offsets = {}
        assert self._full is not None
        f = _downsample(width, height)
        opacity = max(0.0, min(1.0, self.opacity.at(frame)))
        level = round(opacity * _GRAIN_LEVELS)
        strength = config.BG_GRAIN_OPACITY * level / _GRAIN_LEVELS

        rgb = self._smooth(frame, ctx, f, opacity)
        # Grain: mix toward its mean here, add the zero-mean part at full resolution.
        if strength > 0:
            rgb = rgb * np.float32(1 - strength) + np.float32(127.5 * strength)
        margin = math.ceil(127.5 * config.BG_GRAIN_OPACITY)  # offsets can't wrap
        low = np.empty((height // f, width // f, 4), dtype=np.uint8)
        low[..., :3] = np.clip(np.rint(rgb), margin, 255 - margin).astype(np.uint8)
        low[..., 3] = 255
        self._full.reshape(height // f, f, width // f, f, 4)[...] = low[:, None, :, None, :]
        if level > 0:
            np.add(self._full, self._grain_offsets(level, width, height), out=self._full)
        return self._full

    def draw(self, canvas: Canvas, frame: int, ctx: SceneContext) -> None:
        canvas.put_pixels(self.render(frame, ctx))
