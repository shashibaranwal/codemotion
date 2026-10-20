"""Canvas protocol (SPEC 6.6) and its Skia implementation.

Scenes depend only on ``Canvas`` and the backend-neutral ``Paint``; text blobs and images
are opaque handles created by the canvas itself, so another backend can be swapped in.

Performance note: skia-python's Windows wheels run Skia's shader pipeline (gradients,
image draws, blurs, layers) ~50-100x slower than solid fills, anti-aliased shapes and
text. Scenes therefore keep per-frame Skia work to fast primitives: the smooth backdrop
is computed with numpy and handed over with ``put_pixels``, element opacity is applied
per primitive with ``save_alpha``, and offscreen layers (``save_layer``, for translucent
groups) are always bounded to the group's area.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import skia

from codemotion.geometry import Color, Rect
from codemotion.layout.fonts import FontRole, make_font

TextBlob = Any  # backend-specific handle from Canvas.make_text_blob
Image = Any  # backend-specific handle from Canvas.make_image
WHITE = Color(255, 255, 255)


@dataclass(frozen=True)
class LinearGradient:
    start: tuple[float, float]
    end: tuple[float, float]
    colors: tuple[Color, ...]


@dataclass(frozen=True)
class Paint:
    color: Color = WHITE
    opacity: float = 1.0
    gradient: LinearGradient | None = None
    blur: float = 0.0  # Gaussian sigma in px
    stroke_width: float | None = None  # None fills the shape
    round_cap: bool = False  # strokes: round line ends


class Canvas(Protocol):
    width: int
    height: int

    def clear(self, color: Color) -> None: ...
    def put_pixels(self, rgba: npt.NDArray[np.uint8]) -> None: ...
    def save(self) -> None: ...
    def restore(self) -> None: ...
    def save_alpha(self, opacity: float) -> None: ...
    def save_layer(self, opacity: float, bounds: Rect) -> None: ...
    def translate(self, x: float, y: float) -> None: ...
    def scale(self, s: float) -> None: ...
    def rotate(self, degrees: float) -> None: ...
    def clip_rect(self, rect: Rect) -> None: ...
    def rect(self, rect: Rect, paint: Paint) -> None: ...
    def round_rect(self, rect: Rect, r: float, paint: Paint) -> None: ...
    def circle(self, cx: float, cy: float, r: float, paint: Paint) -> None: ...
    def line(self, x1: float, y1: float, x2: float, y2: float, paint: Paint) -> None: ...
    def polygon(self, points: list[tuple[float, float]], paint: Paint) -> None: ...
    def shadow(self, rect: Rect, r: float, blur: float, dy: float, color: Color) -> None: ...
    def make_text_blob(self, text: str, role: FontRole, size: float) -> TextBlob: ...
    def text_blob(self, blob: TextBlob, x: float, y: float, paint: Paint) -> None: ...
    def make_image(self, rgba: npt.NDArray[np.uint8]) -> Image: ...
    def image(self, img: Image, rect: Rect, opacity: float) -> None: ...
    def pixels(self) -> memoryview: ...


def _skia_color(color: Color, opacity: float = 1.0) -> int:
    c = color.with_alpha(opacity)
    return int(skia.ColorSetARGB(c.a, c.r, c.g, c.b))


def _skia_rect(rect: Rect) -> skia.Rect:
    return skia.Rect.MakeXYWH(rect.x, rect.y, rect.w, rect.h)


def decode_image(path: Path) -> npt.NDArray[np.uint8]:
    """Decode a PNG/JPEG file to straight-alpha RGBA; raises ``ValueError`` if unreadable."""
    try:
        image = skia.Image.open(str(path)) if path.is_file() else None
    except RuntimeError:  # Skia: "Failed to decode an image"
        image = None
    if image is None:
        raise ValueError(f"cannot decode {path.name}")
    pixels: npt.NDArray[np.uint8] = image.toarray(
        colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType
    )
    return pixels


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


class SkiaCanvas:
    """Raster canvas drawing straight into an RGBA numpy buffer (zero-copy ``pixels()``)."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._buffer: npt.NDArray[np.uint8] = np.zeros((height, width, 4), dtype=np.uint8)
        self._surface = skia.Surface(
            self._buffer, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType
        )
        self._canvas = self._surface.getCanvas()
        self._blobs: dict[tuple[str, FontRole, float], Any] = {}
        self._alpha: list[float] = [1.0]

    # -- state ---------------------------------------------------------------------------

    def clear(self, color: Color) -> None:
        self._canvas.clear(_skia_color(color))

    def put_pixels(self, rgba: npt.NDArray[np.uint8]) -> None:
        """Replace the whole frame with opaque RGBA pixels (ignores transform and clip)."""
        np.copyto(self._buffer, rgba)

    def save(self) -> None:
        self._canvas.save()
        self._alpha.append(self._alpha[-1])

    def save_alpha(self, opacity: float) -> None:
        """Like ``save()``, and multiply the alpha of everything drawn until ``restore()``.

        Applied per primitive (no offscreen layer), so overlapping shapes inside the group
        show through each other slightly while it is translucent.
        """
        self._canvas.save()
        self._alpha.append(self._alpha[-1] * _clamp01(opacity))

    def save_layer(self, opacity: float, bounds: Rect) -> None:
        """Like ``save()``, but draw into an offscreen layer composited at ``opacity`` on
        ``restore()``, so overlapping shapes don't show through each other.

        ``bounds`` (in current coordinates) limits the layer; keep it tight, layers are slow.
        """
        alpha = round(_clamp01(opacity * self._alpha[-1]) * 255)
        self._canvas.saveLayerAlpha(_skia_rect(bounds), alpha)
        self._alpha.append(1.0)

    def restore(self) -> None:
        self._canvas.restore()
        if len(self._alpha) > 1:
            self._alpha.pop()

    def translate(self, x: float, y: float) -> None:
        self._canvas.translate(x, y)

    def scale(self, s: float) -> None:
        self._canvas.scale(s, s)

    def rotate(self, degrees: float) -> None:
        self._canvas.rotate(degrees)

    def clip_rect(self, rect: Rect) -> None:
        self._canvas.clipRect(_skia_rect(rect), doAntiAlias=True)

    # -- shapes --------------------------------------------------------------------------

    def _paint(self, paint: Paint) -> skia.Paint:
        opacity = paint.opacity * self._alpha[-1]
        p = skia.Paint(AntiAlias=True, Color=_skia_color(paint.color, opacity))
        if paint.gradient is not None:
            g = paint.gradient
            p.setShader(
                skia.GradientShader.MakeLinear(
                    [skia.Point(*g.start), skia.Point(*g.end)], [_skia_color(c) for c in g.colors]
                )
            )
            p.setAlphaf(_clamp01(opacity))
        if paint.blur > 0:
            p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, paint.blur))
        if paint.stroke_width is not None:
            p.setStyle(skia.Paint.kStroke_Style)
            p.setStrokeWidth(paint.stroke_width)
            if paint.round_cap:
                p.setStrokeCap(skia.Paint.kRound_Cap)
        return p

    def rect(self, rect: Rect, paint: Paint) -> None:
        self._canvas.drawRect(_skia_rect(rect), self._paint(paint))

    def round_rect(self, rect: Rect, r: float, paint: Paint) -> None:
        self._canvas.drawRoundRect(_skia_rect(rect), r, r, self._paint(paint))

    def circle(self, cx: float, cy: float, r: float, paint: Paint) -> None:
        self._canvas.drawCircle(cx, cy, r, self._paint(paint))

    def line(self, x1: float, y1: float, x2: float, y2: float, paint: Paint) -> None:
        self._canvas.drawLine(x1, y1, x2, y2, self._paint(paint))

    def polygon(self, points: list[tuple[float, float]], paint: Paint) -> None:
        if len(points) >= 3:
            path = skia.Path()
            path.addPoly([skia.Point(x, y) for x, y in points], True)
            self._canvas.drawPath(path, self._paint(paint))

    def shadow(self, rect: Rect, r: float, blur: float, dy: float, color: Color) -> None:
        """Drop shadow; ``blur`` is a CSS-style blur radius (sigma = blur / 2)."""
        shifted = Rect(rect.x, rect.y + dy, rect.w, rect.h)
        self.round_rect(shifted, r, Paint(color=color, blur=blur / 2))

    # -- text and images -----------------------------------------------------------------

    def make_text_blob(self, text: str, role: FontRole, size: float) -> TextBlob:
        key = (text, role, size)
        blob = self._blobs.get(key)
        if blob is None:
            blob = skia.TextBlob.MakeFromString(text, make_font(role, size))
            self._blobs[key] = blob
        return blob

    def text_blob(self, blob: TextBlob, x: float, y: float, paint: Paint) -> None:
        if blob is not None:  # Skia returns None for empty strings
            self._canvas.drawTextBlob(blob, x, y, self._paint(paint))

    def make_image(self, rgba: npt.NDArray[np.uint8]) -> Image:
        """Image handle from straight-alpha RGBA pixels."""
        return skia.Image.fromarray(
            np.ascontiguousarray(rgba),
            colorType=skia.kRGBA_8888_ColorType,
            alphaType=skia.kUnpremul_AlphaType,
        )

    def image(self, img: Image, rect: Rect, opacity: float) -> None:
        paint = skia.Paint(AntiAlias=True)
        paint.setAlphaf(_clamp01(opacity) * self._alpha[-1])
        self._canvas.drawImageRect(
            img, _skia_rect(rect), skia.SamplingOptions(skia.FilterMode.kLinear), paint
        )

    # -- output --------------------------------------------------------------------------

    def pixels(self) -> memoryview:
        """RGBA bytes, row-major. Frames are opaque, so premultiplied == straight alpha."""
        return memoryview(self._buffer).cast("B")

    def to_array(self) -> npt.NDArray[np.uint8]:
        return self._buffer.copy()

    def view(self) -> npt.NDArray[np.uint8]:
        """The live (height, width, 4) RGBA buffer; it changes with the next draw."""
        return self._buffer

    def png_bytes(self) -> bytes:
        data = self._surface.makeImageSnapshot().encodeToData(skia.kPNG, 100)
        return bytes(data)

    def save_png(self, path: Path) -> None:
        path.write_bytes(self.png_bytes())
