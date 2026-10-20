"""Bright-gradient Instagram Reel, built from motion_graphic_frame_plan.py.

Script: "We look at the constraints. Budget. Timeline. Technology. Integrations.
All of that, before we start building."

Render:  codemotion render bright_reel_video.py -o output/bright_reel.mp4

The plan's coordinates are 1080x1920 pixels, which are exactly CodeMotion's design pixels
for a 9:16 video. The plan shows one card at a time, so a single card group re-enters for
each constraint (its content changes) - same look, and it keeps the video within the
60-element limit.
"""

import struct
import zlib
from pathlib import Path

import numpy as np

from codemotion import Group, Image, Line, Shape, Text, Video, ease

W, H = 1080, 1920
INK = "#202338"
MUTED = "#62677D"
BUDGET = "#E6A700"
TIMELINE = "#2585D8"
TECHNOLOGY = "#7955D9"
INTEGRATIONS = "#159B7A"
WHITE = "#FFFFFF"
FAINT = "#2023381C"  # ink at 11 %: tracks and inactive parts
SMOOTH = ease.in_out_cubic  # closest built-in curve to the plan's smoothstep

video = Video(duration=12, aspect="9:16", fps=30, background="#FFE3D6", fade_in=0)


def px(x, y):
    """Plan pixels -> frame fractions."""
    return (x / W, y / H)


# -- background: 135-degree four-stop pastel gradient with soft white glows ------------------


def write_background(path, scale=1.2):
    """A PNG larger than the frame (so it can drift), written with numpy + zlib."""
    w, h = round(W * scale), round(H * scale)
    stops = [(0.0, "#FFF1C9"), (0.38, "#FFD6E7"), (0.72, "#CDEBFF"), (1.0, "#DCCFFF")]
    pos = np.array([p for p, _ in stops])
    rgb = np.array([[int(c[i : i + 2], 16) for i in (1, 3, 5)] for _, c in stops], float)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (xs / w + ys / h) / 2  # top-left -> bottom-right
    image = np.stack([np.interp(t, pos, rgb[:, c]) for c in range(3)], axis=-1)
    for cx, cy, radius in ((0.28, 0.3, 0.32), (0.74, 0.7, 0.36)):  # "white glow blobs"
        d2 = ((xs / w - cx) * w / h) ** 2 + (ys / h - cy) ** 2
        glow = 0.32 * np.exp(-d2 / (2 * (radius / 2.2) ** 2))[..., None]
        image = image * (1 - glow) + 255 * glow
    pixels = np.dstack([image.round().clip(0, 255).astype(np.uint8),
                        np.full((h, w), 255, np.uint8)])  # fmt: skip
    raw = b"".join(b"\x00" + row.tobytes() for row in pixels)

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


BACKGROUND = Path("assets/bright_reel_background.png")
if not BACKGROUND.is_file():
    write_background(BACKGROUND)
backdrop = video.add(Image(BACKGROUND, width=W * 1.2))
backdrop.animate("ox", from_=-70, to=70, start=0, length=12, ease=ease.linear)  # slow drift
backdrop.animate("oy", from_=-110, to=110, start=0, length=12, ease=ease.linear)

# -- 0.0-2.0 "We look at the constraints." ---------------------------------------------------

THE_W, LINE_W = 185.6, 810.5  # Inter SemiBold at 82 px: "THE " and "THE CONSTRAINTS."
left = W / 2 - LINE_W / 2
line_gap = 82 * 1.08
for text, x, y, align, color in (
    ("WE LOOK AT", W / 2, 430 - line_gap / 2, "center", INK),
    ("THE", left, 430 + line_gap / 2, "left", INK),
    ("CONSTRAINTS.", left + THE_W, 430 + line_gap / 2, "left", TECHNOLOGY),
):
    intro = video.add(Text(text, 82, bold=True, color=color, align=align, at=px(x, y)))
    intro.fade_in(0.0, 0.55, ease=SMOOTH)
    intro.animate("oy", from_=40, to=0, start=0.0, length=0.55, ease=SMOOTH)
    intro.fade_out(1.8, 0.3, ease=SMOOTH)

# -- 2.0-9.0: one large frosted card at a time ----------------------------------------------

CARD_X, CARD_Y = 540, 930
# The plan keeps the last card until 9.2 s; it ends at 9.0 s so the closing starts clean.
SLOTS = {"budget": (2.0, 4.0), "timeline": (4.0, 5.5), "technology": (5.5, 7.0),
         "integrations": (7.0, 9.0)}  # fmt: skip

card = video.add(Group(at=px(CARD_X, CARD_Y)), start=2.0, end=9.0)
card.add(Shape.rect(820, 440, radius=42, color="#FFFFFF9E", stroke="#FFFFFFE6",
                    stroke_width=3, at=px(CARD_X, CARD_Y)))  # fmt: skip
for start, end in SLOTS.values():
    # Enter: fade, grow 88 % -> 100 %, rise 50 px. Leave: fade, drift up, shrink a little.
    card.animate("opacity", from_=0.0, to=1.0, start=start, length=0.42, ease=SMOOTH)
    card.animate("scale", from_=0.88, to=1.0, start=start, length=0.42, ease=SMOOTH)
    card.animate("oy", from_=50, to=0.0, start=start, length=0.42, ease=SMOOTH)
    card.animate("opacity", to=0.0, start=end - 0.3, length=0.3, ease=SMOOTH)
    card.animate("oy", to=-40, start=end - 0.3, length=0.3, ease=SMOOTH)
    card.animate("scale", to=0.96, start=end - 0.3, length=0.3, ease=SMOOTH)


def on_card(key, element):
    """Show ``element`` on the card while ``key`` is the current constraint."""
    start, end = SLOTS[key]
    return card.add(element, start=start, end=end)


for key, label in (("budget", "BUDGET"), ("timeline", "TIMELINE"),
                   ("technology", "TECHNOLOGY"), ("integrations", "INTEGRATIONS")):  # fmt: skip
    on_card(key, Text(label, 60, bold=True, color=INK, at=px(CARD_X, 1080)))

# BUDGET: currency symbols drift in and settle; the cost meter fills left to right.
for i, symbol in enumerate(("₹", "$", "€")):
    coin = on_card("budget", Text(symbol, 110, bold=True, color=BUDGET, at=px(380 + 160 * i, 815)))
    coin.fade_in(2.15 + 0.15 * i, 0.4)
    coin.animate("oy", from_=-70, to=0, start=2.15 + 0.15 * i, length=0.6, ease=ease.out_cubic)
    coin.animate("oy", to=-8, start=3.0, length=1.0, ease=SMOOTH)  # floats gently
on_card("budget", Text("COST", 30, bold=True, color=MUTED, align="left", at=px(190, 965)))
on_card("budget", Line(px(310, 965), px(880, 965), width=16, color=FAINT))
meter = on_card("budget", Line(px(310, 965), px(880, 965), width=16, color=BUDGET))
meter.draw_on(2.25, 1.15, ease=ease.linear)

# TIMELINE: a clock whose hands turn a little, "30 DAYS ->", milestones light in sequence.
on_card("timeline", Shape.circle(46, color=None, stroke=TIMELINE, stroke_width=7,
                                 at=px(330, 815)))  # fmt: skip
on_card("timeline", Line(px(330, 815), px(313, 815), width=7, color=TIMELINE))  # hour: 9
minute = on_card("timeline", Group(at=px(330, 815)))  # turns about the clock's centre
minute.add(Line(px(330, 815), px(330, 786), width=7, color=TIMELINE))
minute.rotate_by(60, start=4.0, length=1.5, ease=SMOOTH)
on_card("timeline", Text("30 DAYS →", 72, bold=True, color=TIMELINE, align="left",
                         at=px(405, 815)))  # fmt: skip
on_card("timeline", Line(px(270, 935), px(810, 935), width=4, color=FAINT))
for i, (label, lit_at) in enumerate((("PLAN", 4.35), ("BUILD", 4.75), ("LAUNCH", 5.15))):
    x = 270 + 270 * i
    dot = on_card("timeline", Shape.circle(16, color=TIMELINE, opacity=0.25, at=px(x, 935)))
    dot.animate("opacity", to=1.0, start=lit_at, length=0.15, ease=ease.out_cubic)
    dot.animate("scale", from_=1.6, to=1.0, start=lit_at, length=0.35, ease=ease.out_back)
    name = on_card("timeline", Text(label, 26, bold=True, color=MUTED, at=px(x, 985)))
    name.animate("opacity", from_=0.5, to=1.0, start=lit_at, length=0.2)

# TECHNOLOGY: three rounded blocks slide together and connect.
for i, (label, at) in enumerate((("UI", 5.65), ("API", 5.85), ("DB", 6.05))):
    x = 330 + 210 * i
    for part in (Shape.rect(180, 120, radius=26, color=TECHNOLOGY, at=px(x, 870)),
                 Text(label, 40, bold=True, color=WHITE, at=px(x, 870))):  # fmt: skip
        block = on_card("technology", part)
        block.fade_in(at, 0.3)
        block.animate("ox", from_=(i - 1) * 110, to=0, start=at, length=0.5, ease=ease.out_cubic)
        block.animate("oy", from_=30, to=0, start=at, length=0.5, ease=ease.out_cubic)
for i, at in enumerate((6.25, 6.4)):
    x = 330 + 210 * i
    link = on_card("technology", Line(px(x + 90, 870), px(x + 120, 870), width=8,
                                      color=TECHNOLOGY))  # fmt: skip
    link.draw_on(at, 0.2, ease=ease.linear)

# INTEGRATIONS: nodes pop in; connector lines draw between them (drawn under the nodes).
NODES = {"APP": (370, 800), "API": (710, 800), "DB": (540, 960)}
for a, b, at in (("APP", "API", 7.5), ("API", "DB", 7.75), ("DB", "APP", 8.0)):
    wire = on_card("integrations", Line(px(*NODES[a]), px(*NODES[b]), width=6,
                                        color=INTEGRATIONS))  # fmt: skip
    wire.draw_on(at, 0.3)
for (label, (x, y)), at in zip(NODES.items(), (7.15, 7.35, 7.55), strict=True):
    for part in (Shape.rect(170, 76, radius=38, color=WHITE, stroke=INTEGRATIONS,
                            stroke_width=4, at=px(x, y)),
                 Text(label, 30, bold=True, color=INTEGRATIONS, at=px(x, y))):  # fmt: skip
        on_card("integrations", part).pop_in(at, 0.45)

# -- 9.0-12.0 "All of that, before we start building." ---------------------------------------

gap = 78 * 1.12
for text, y, color in (("ALL OF THAT.", 950 - gap, INK), ("BEFORE WE START", 950, INK),
                       ("BUILDING.", 950 + gap, INTEGRATIONS)):  # fmt: skip
    line = video.add(Text(text, 78, bold=True, color=color, at=px(W / 2, y)), start=8.9)
    line.fade_in(9.0, 0.5, ease=SMOOTH)
    line.animate("scale", from_=0.94, to=1.0, start=9.0, length=0.5, ease=SMOOTH)
    line.animate("oy", from_=36, to=0, start=9.0, length=0.5, ease=SMOOTH)

# The four symbols converge into one outlined plan icon.
ICON_X, ICON_Y = 540, 620
frame = video.add(Shape.rect(260, 260, radius=48, color=None, stroke=INK, stroke_width=6,
                             at=px(ICON_X, ICON_Y)), start=8.9)  # fmt: skip
frame.fade_in(9.0, 0.5, ease=SMOOTH)
frame.animate("scale", from_=1.15, to=1.0, start=9.0, length=0.8, ease=SMOOTH)

TL, TR = (ICON_X - 62, ICON_Y - 62), (ICON_X + 62, ICON_Y - 62)
BL, BR = (ICON_X - 62, ICON_Y + 62), (ICON_X + 62, ICON_Y + 62)
pieces = [
    (Text("$", 64, bold=True, color=BUDGET, at=px(*TL)), (-1, -1)),
    (Shape.circle(30, color=None, stroke=TIMELINE, stroke_width=6, at=px(*TR)), (1, -1)),
    (Line(px(*TR), px(TR[0], TR[1] - 17), width=6, color=TIMELINE), (1, -1)),
    (Line(px(*TR), px(TR[0] + 13, TR[1]), width=6, color=TIMELINE), (1, -1)),
    (Shape.rect(60, 18, radius=6, color=TECHNOLOGY, at=px(BL[0], BL[1] - 12)), (-1, 1)),
    (Shape.rect(60, 18, radius=6, color=TECHNOLOGY, at=px(BL[0], BL[1] + 12)), (-1, 1)),
    (
        Line(px(BR[0] - 20, BR[1] + 14), px(BR[0] + 20, BR[1] - 14), width=5, color=INTEGRATIONS),
        (1, 1),
    ),
    (Shape.circle(10, color=INTEGRATIONS, at=px(BR[0] - 20, BR[1] + 14)), (1, 1)),
    (Shape.circle(10, color=INTEGRATIONS, at=px(BR[0] + 20, BR[1] - 14)), (1, 1)),
]
for piece, (sx, sy) in pieces:
    video.add(piece, start=8.9)
    piece.fade_in(9.0, 0.4, ease=SMOOTH)
    # Each symbol starts out at its corner of the screen and settles into the icon.
    piece.animate("ox", from_=sx * 200, to=0, start=9.0, length=0.8, ease=SMOOTH)
    piece.animate("oy", from_=sy * 160, to=0, start=9.0, length=0.8, ease=SMOOTH)
