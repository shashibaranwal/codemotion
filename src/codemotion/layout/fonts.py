"""Bundled fonts: Skia typefaces, configured fonts and a metrics cache.

Fonts use linear metrics and sub-pixel positioning so text can move smoothly without
snapping to whole pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import cache, lru_cache

import skia

from codemotion import config

_METRICS_SIZE = 1000.0


class FontRole(StrEnum):
    mono = "mono"
    mono_bold = "mono_bold"
    sans = "sans"
    sans_semibold = "sans_semibold"


_FONT_FILES = {
    FontRole.mono: config.MONO_FONT_FILE,
    FontRole.mono_bold: config.MONO_BOLD_FONT_FILE,
    FontRole.sans: config.SANS_FONT_FILE,
    FontRole.sans_semibold: config.SANS_SEMIBOLD_FONT_FILE,
}


@dataclass(frozen=True)
class FontMetrics:
    """Metrics per 1 px of font size (multiply by the size in px)."""

    advance: float  # width of one monospace cell (meaningful for the mono role)
    ascent: float  # positive, above the baseline
    descent: float  # positive, below the baseline


@cache
def typeface(role: FontRole) -> skia.Typeface:
    path = _FONT_FILES[role]
    face = skia.Typeface.MakeFromFile(str(path)) if path.is_file() else None
    if face is None:
        raise RuntimeError(f"Bundled font is missing or unreadable: {path}")
    return face


def make_font(role: FontRole, size: float) -> skia.Font:
    font = skia.Font(typeface(role), size)
    font.setSubpixel(True)
    font.setLinearMetrics(True)
    font.setHinting(skia.FontHinting.kNone)
    font.setEdging(skia.Font.Edging.kAntiAlias)
    return font


@cache
def metrics(role: FontRole) -> FontMetrics:
    font = make_font(role, _METRICS_SIZE)
    m = font.getMetrics()
    return FontMetrics(
        advance=font.measureText("0") / _METRICS_SIZE,
        ascent=-m.fAscent / _METRICS_SIZE,
        descent=m.fDescent / _METRICS_SIZE,
    )


def measure(text: str, role: FontRole, size: float) -> float:
    """Advance width of ``text`` in px."""
    return float(make_font(role, _METRICS_SIZE).measureText(text)) * size / _METRICS_SIZE


@lru_cache(maxsize=4096)
def _has_glyph(role: FontRole, char: str) -> bool:
    return bool(typeface(role).unicharsToGlyphs([ord(char)])[0] != 0)


def replace_missing_glyphs(text: str, role: FontRole) -> tuple[str, int]:
    """Replace characters the font cannot draw with □; returns (text, number replaced)."""
    out = []
    replaced = 0
    for char in text:
        if char.isprintable() and not _has_glyph(role, char):
            out.append(config.MISSING_GLYPH_REPLACEMENT)
            replaced += 1
        else:
            out.append(char)
    return "".join(out), replaced
