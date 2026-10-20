"""Draw the app icon: ``src/codemotion/assets/icon.png`` (window icon) and
``packaging/icon.ico`` (Windows executables). Run: ``uv run python packaging/make_icon.py``.

A rounded square in the Midnight gradient with a code chevron and a play triangle.
The .ico embeds PNG images (supported since Windows Vista), so no imaging library is needed.
"""

from __future__ import annotations

import struct
from pathlib import Path

import skia

ROOT = Path(__file__).resolve().parent.parent
PNG_OUT = ROOT / "src" / "codemotion" / "assets" / "icon.png"
ICO_OUT = ROOT / "packaging" / "icon.ico"
SIZES = (256, 64, 48, 32, 16)


def draw(size: int) -> bytes:
    surface = skia.Surface(size, size)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorTRANSPARENT)
    canvas.scale(size / 256, size / 256)
    paint = skia.Paint(AntiAlias=True)
    paint.setShader(
        skia.GradientShader.MakeLinear(
            [skia.Point(0, 0), skia.Point(256, 256)],
            [skia.Color(0x1B, 0x14, 0x40), skia.Color(0x5B, 0x5B, 0xF7)],
        )
    )
    canvas.drawRoundRect(skia.Rect.MakeXYWH(8, 8, 240, 240), 56, 56, paint)

    stroke = skia.Paint(
        AntiAlias=True,
        Color=skia.Color(0xE6, 0xE9, 0xF2),
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=18,
        StrokeCap=skia.Paint.kRound_Cap,
        StrokeJoin=skia.Paint.kRound_Join,
    )
    chevron = skia.Path()
    chevron.moveTo(92, 80)
    chevron.lineTo(52, 128)
    chevron.lineTo(92, 176)
    canvas.drawPath(chevron, stroke)

    play = skia.Path()
    play.addPoly([skia.Point(122, 78), skia.Point(206, 128), skia.Point(122, 178)], True)
    fill = skia.Paint(AntiAlias=True, Color=skia.Color(0xC0, 0x4B, 0xD8))
    corners = skia.Paint(AntiAlias=True, Color=skia.Color(0xC0, 0x4B, 0xD8),
                         Style=skia.Paint.kStroke_Style, StrokeWidth=14,
                         StrokeJoin=skia.Paint.kRound_Join)  # fmt: skip
    canvas.drawPath(play, fill)
    canvas.drawPath(play, corners)
    data = surface.makeImageSnapshot().encodeToData(skia.kPNG, 100)
    return bytes(data)


def ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256 in the directory entry
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + entries + blobs


def main() -> None:
    images = [(size, draw(size)) for size in SIZES]
    PNG_OUT.write_bytes(images[0][1])
    ICO_OUT.write_bytes(ico(images))
    print(f"wrote {PNG_OUT} and {ICO_OUT}")


if __name__ == "__main__":
    main()
