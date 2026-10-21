"""Minimalist "Create with AutoAE" motion graphic, recreated from motion_graphic.py.

Render:  codemotion render motion_graphic_video.py -o output/motion_graphic.mp4

motion_graphic.py paints every frame with Pillow at 720x1280. This script rebuilds it as
CodeMotion elements at 1080x1920: positions are given in the original's pixels (``px``)
and sizes are scaled by ``S``. The static artwork (the graduated arc, the template cards,
the people icons, the glow) is drawn once into PNGs with Skia, which keeps the video well
inside the 60-element limit; everything that moves is an element. Motions are traced
frame by frame from the original's own formulas (``trace``), so the timing matches.

Differences from the original:
- 8.5 s instead of 8.567 s (video durations are multiples of 0.5 s).
- Text fades and the translucent "A" on the cards behave as its code intends. Its Pillow
  drawing drops their alpha, so in its output they pop in at full opacity.
- The third template card reads "No.3" (the original repeats "No.2").
- Inter replaces DejaVu Sans.
"""

import math
from itertools import pairwise
from pathlib import Path

import skia

from codemotion import Group, Image, Line, Shape, Text, Video, config, ease

W, H = 720, 1280  # the original's frame, in its pixels
S = 1080 / W  # original px -> design px
FPS = 30
ART = Path("assets/motion_graphic")  # delete this folder to redraw the artwork
DENSITY = 2  # PNG pixels per design pixel for small, detailed artwork

BLACK = "#0A0A0C"
BLUE = "#247CFF"
BRACKET_BLUE = "#2D7DFF"
QUESTION_GREEN = "#AAFFB4"

# Scenes cut hard, as in the original.
S1, S2, S3, S4 = 0.0, 2.55, 4.85, 6.85

video = Video(duration=8.5, aspect="9:16", fps=FPS, background="#FFFFFF", fade_in=0)
# The backdrop adds faint film grain even to a solid colour; the original is flat white.
video.add(Shape.rect(W * S, H * S, color="#FFFFFF"))


# -- the original's curves --------------------------------------------------------------------


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def smooth(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def mix(a, b, p):
    return a + (b - a) * p


def px(x, y):
    """Original pixels -> frame fractions."""
    return (x / W, y / H)


def trace(element, prop, fn, t0, t1, step=1 / FPS):
    """Animate ``prop`` along ``fn(t)`` from t0 to t1 in straight steps (one per frame)."""
    n = max(1, round((t1 - t0) / step))
    times = [t0 + (t1 - t0) * i / n for i in range(n + 1)]
    for i, (a, b) in enumerate(pairwise(times)):
        element.animate(prop, from_=fn(a) if i == 0 else None, to=fn(b), start=a,
                        length=b - a, ease=ease.linear)  # fmt: skip
    return element


# -- artwork (drawn in the original's pixels) ----------------------------------------------------

REGULAR = skia.Typeface.MakeFromFile(str(config.SANS_FONT_FILE))
BOLD = skia.Typeface.MakeFromFile(str(config.SANS_SEMIBOLD_FONT_FILE))  # Text(bold=True)


def color(rgb, alpha=255):
    return skia.ColorSetARGB(alpha, *rgb)


def paint(rgb, alpha=255, stroke=None):
    p = skia.Paint(AntiAlias=True, Color=color(rgb, alpha))
    if stroke is not None:
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(stroke)
    return p


def png(name, w, h, draw, density=DENSITY):
    """Draw a w x h layer (original px) with ``draw(canvas)`` into ART/name, once."""
    path = ART / name
    if not path.is_file():
        surface = skia.Surface(math.ceil(w * S * density), math.ceil(h * S * density))
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.scale(S * density, S * density)
        draw(canvas)
        path.parent.mkdir(parents=True, exist_ok=True)
        surface.makeImageSnapshot().save(str(path), skia.kPNG)
    return path


def polygon(canvas, points, p):
    path = skia.Path()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.close()
    canvas.drawPath(path, p)


def centered_text(canvas, x, y, text, size, rgb, bold=False):
    """Pillow's anchor "mm": centred on x and on the middle of ascender and descender."""
    font = skia.Font(BOLD if bold else REGULAR, size)
    metrics = font.getMetrics()
    lines = text.split("\n")
    step = size * 1.35  # Pillow's multiline spacing at these sizes
    for i, line in enumerate(lines):
        baseline = y + (i - (len(lines) - 1) / 2) * step - (metrics.fAscent + metrics.fDescent) / 2
        canvas.drawString(line, x - font.measureText(line) / 2, baseline, font, paint(rgb))


def draw_glow(canvas):
    p = paint((208, 225, 255), 80)
    p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 150 * 0.55))
    canvas.drawCircle(400, 400, 150, p)


def draw_sparkle(canvas):
    """The "✦" four-pointed star, centred in a 12 x 12 box."""
    c, r, k = 6.0, 5.0, 0.8
    path = skia.Path()
    path.moveTo(c, c - r)
    for (x1, y1), (x2, y2) in (((k, -k), (r, 0)), ((k, k), (0, r)), ((-k, k), (-r, 0)),
                               ((-k, -k), (0, -r))):  # fmt: skip
        path.quadTo(c + x1, c + y1, c + x2, c + y2)
    canvas.drawPath(path, paint((36, 124, 255)))


ARC_TOP = -20  # the arc layer covers frame rows -20..300 (it runs off the top edge)


def draw_arc(canvas):
    canvas.translate(0, -ARC_TOP)
    cx, cy, radius = 230, 460, 470
    start_deg, end_deg = 205, 337
    oval = skia.Rect.MakeLTRB(cx - radius, cy - radius, cx + radius, cy + radius)
    canvas.drawArc(oval, start_deg, end_deg - start_deg, False, paint((38, 95, 225), stroke=2))
    for i in range(76):  # graduations; every fifth is long and dark
        a = math.radians(start_deg + (end_deg - start_deg) * i / 75)
        r1, r2 = radius - (12 if i % 5 else 23), radius + 2
        rgb = (190, 211, 248) if i % 5 else (80, 100, 130)
        x1, y1 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
        x2, y2 = cx + r2 * math.cos(a), cy + r2 * math.sin(a)
        canvas.drawLine(x1, y1, x2, y2, paint(rgb, stroke=1))
    for deg in (218, 244, 270, 300, 326):
        a = math.radians(deg)
        x, y = cx + radius * math.cos(a), cy + radius * math.sin(a)
        canvas.drawCircle(x, y, 3, paint((255, 255, 255)))
        canvas.drawCircle(x, y, 2.5, paint((40, 80, 170), stroke=1))


def draw_diamond(canvas):
    polygon(canvas, [(13, 4), (22, 23), (13, 42), (4, 23)], paint((10, 10, 12), stroke=3))


def person(main, rgb):
    """A head and a round body; the layer's centre is the point the icon scales about."""
    head_r, body_w, body_h, head_y = (19, 34, 28, -24) if main else (12, 22, 17, -15)
    w, h = (70, 116) if main else (46, 72)

    def draw(canvas):
        canvas.translate(w / 2, h / 2)
        canvas.drawCircle(0, head_y, head_r, paint(rgb))
        canvas.drawOval(skia.Rect.MakeLTRB(-body_w, 1, body_w, 1 + body_h * 2), paint(rgb))

    return png(f"person_{'main' if main else 'side'}.png", w, h, draw), w


def draw_brackets(canvas):
    """Four L-shaped corner marks around (154, 490)-(568, 650)."""
    canvas.translate(-152.5, -488.5)
    x1, y1, x2, y2, length = 154.5, 490.5, 567.5, 649.5, 30
    for x, y, sx, sy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        canvas.drawLine(x, y, x + sx * length, y, paint((45, 125, 255), stroke=1))
        canvas.drawLine(x, y, x, y + sy * length, paint((45, 125, 255), stroke=1))


def card(name, w, h, title):
    """A stylised outdoor-video thumbnail with rounded corners."""

    def draw(canvas):
        radius = max(8, int(w * 0.08))
        canvas.clipRRect(skia.RRect.MakeRectXY(skia.Rect.MakeWH(w, h), radius, radius), True)
        sky = skia.Paint(AntiAlias=True)
        sky.setShader(skia.GradientShader.MakeLinear(
            [skia.Point(0, 0), skia.Point(0, h)], [color((102, 129, 154)), color((45, 66, 47))]
        ))  # fmt: skip
        canvas.drawRect(skia.Rect.MakeWH(w, h), sky)
        polygon(canvas, [(0, h * .50), (w * .20, h * .22), (w * .36, h * .48), (w * .55, h * .16),
                         (w * .78, h * .49), (w, h * .28), (w, h * .76), (0, h * .76)],
                paint((64, 83, 70)))  # fmt: skip
        polygon(canvas, [(0, h * .69), (w * .24, h * .48), (w * .45, h * .70), (w * .67, h * .42),
                         (w, h * .68), (w, h), (0, h)], paint((39, 57, 43)))  # fmt: skip
        polygon(canvas, [(w * .50, h * .25), (w * .72, h * .77), (w * .60, h * .77),
                         (w * .50, h * .53), (w * .40, h * .77), (w * .28, h * .77)],
                paint((235, 235, 235), 60))  # fmt: skip
        centered_text(canvas, w / 2, int(h * .09), "AutoAE Demo", max(6, int(w * .045)),
                      (245, 245, 245))  # fmt: skip
        centered_text(canvas, w / 2, int(h * .53), title, max(9, int(w * .095)), (255, 255, 255),
                      bold=True)  # fmt: skip
        centered_text(canvas, w / 2, int(h * .92), "Add media here", max(5, int(w * .035)),
                      (230, 230, 230))  # fmt: skip

    return png(name, w, h, draw, density=3)


# -- scene 1 (0-2.55 s): "Create with AutoAE" ----------------------------------------------------

glow = video.add(Image(png("glow.png", 800, 800, draw_glow, density=0.5), width=800 * S,
                       at=px(390, 350)), end=S2)  # fmt: skip
trace(glow, "opacity", lambda t: smooth((t - 0.65) / 0.85), 0.65, 1.5)
trace(glow, "scale", lambda t: (105 + 45 * smooth((t - 0.65) / 0.85)) / 150, 0.65, 1.5)

# "Create" enters large, then shrinks into the phrase.
create = video.add(Text("Create", 48 * S, bold=True, color=BLACK, at=px(360, 360)), end=0.55)
trace(create, "scale", lambda t: mix(48, 28, ease_out(t / 0.55)) / 48, 0.0, 0.55)
video.add(Text("Create with", 21 * S, bold=True, color=BLACK, at=px(360, 360)), start=0.55, end=S2)

# "AutoAE" follows "Create with" after a slightly wide word space. (The original's gap is
# about one em, which looks like a stray gap with Inter's narrower letters.)
phrase_right = 360 + skia.Font(BOLD, 21).measureText("Create with") / 2
auto_left = phrase_right + 9
auto_width = skia.Font(BOLD, 21).measureText("AutoAE")
video.add(Text("AutoAE", 21 * S, bold=True, color=BLUE, align="left", at=px(auto_left, 360)),
          start=0.85, end=S2)  # fmt: skip
sparkle_x = auto_left + auto_width * 0.47  # above the "to", where the original's sits
sparkle = video.add(Image(png("sparkle.png", 12, 12, draw_sparkle, density=4), width=12 * S,
                          at=px(sparkle_x, 331)), start=0.85, end=S2)  # fmt: skip
trace(sparkle, "y", lambda t: (mix(324, 305, smooth((t - 0.85) / 0.7)) + 7) / H, 0.85, 1.55)

# -- scene 2 (2.55-4.85 s): pendulum over a curved scale --------------------------------------

video.add(Image(png("arc.png", W, 320, draw_arc, density=1), width=W * S,
                at=px(W / 2, ARC_TOP + 160)), start=S2, end=S3)  # fmt: skip


def swing(t):
    """The pendulum's angle in degrees (positive swings the bob to the right)."""
    return -9 + 27 * math.sin((t - 2.48) / 2.2 * math.pi * 1.05)


PIVOT = (205, 410)
pendulum = video.add(Group(at=px(*PIVOT)), start=S2, end=S3)
pendulum.add(Line(px(*PIVOT), px(PIVOT[0], PIVOT[1] + 260), width=5 * S, color=BLACK))
pendulum.add(Image(png("diamond.png", 26, 46, draw_diamond), width=26 * S,
                   at=px(PIVOT[0], PIVOT[1] + 66)))  # fmt: skip
pendulum.add(Shape.circle(8 * S, color=BLUE, stroke=BLACK, stroke_width=3 * S,
                          at=px(PIVOT[0], PIVOT[1] + 260)))  # fmt: skip
trace(pendulum, "rotation", lambda t: -swing(t), S2, S3)  # rotation turns clockwise
# A small blue mark that stays put below the pivot.
video.add(Line(px(PIVOT[0] - 4, PIVOT[1] + 72), px(PIVOT[0] + 4, PIVOT[1] + 72), width=4 * S,
               color=BLUE), start=S2, end=S3)  # fmt: skip

for text, size, rgb, y in (("Make", 15, "#282828", 800), ("motion", 29, "#2896FF", 825)):
    word = video.add(Text(text, size * S, bold=True, color=rgb, align="left", at=px(90, y)),
                     start=S2, end=S3)  # fmt: skip
    trace(word, "opacity", lambda t: smooth((t - 3.2) / 0.6), 3.2, 3.8)

# Corner brackets grow out of their corners just before the cut.
for x, y, sx, sy in ((585, 300, 1, 1), (705, 300, -1, 1), (585, 395, 1, -1), (705, 395, -1, -1)):
    for end in ((x + sx * 28, y), (x, y + sy * 28)):
        arm = video.add(Line(px(x, y), px(*end), width=S, color=BRACKET_BLUE), start=4.5,
                        end=S3)  # fmt: skip
        trace(arm, "reveal", lambda t: smooth((t - 4.5) / 0.25), 4.5, 4.75)

# -- scene 3 (4.85-6.85 s): "Your ideas come alive" ---------------------------------------------

brackets = png("brackets.png", 416, 162, draw_brackets, density=3)
for element in (
    Text("Your ideas", 23 * S, bold=True, color=BLACK, at=px(360, 535)),
    Text("come alive", 23 * S, bold=True, color=BLUE, at=px(360, 562)),
    Image(brackets, width=416 * S, at=px(360.5, 569.5)),
):
    video.add(element, start=S3, end=S4)
    trace(element, "oy", lambda t: (1 - smooth((t - 4.8) / 0.45)) * 24 * S, S3, 5.25)

# People: four black icons, then the blue one in front; all grow in together.
side, side_w = person(False, (10, 10, 12))
main, main_w = person(True, (36, 124, 255))
for path, w, dx, size, rise in [(side, side_w, dx, sc, 22) for dx, sc in
                                ((-110, 0.75), (-70, 0.9), (70, 0.9), (110, 0.75))] + [
                               (main, main_w, 0, 1.15, 20)]:  # fmt: skip
    icon = video.add(Image(path, width=w * S, at=px(360 + dx, 670), scale=0), start=S3, end=S4)
    trace(icon, "scale", lambda t, size=size: size * smooth((t - 5.05) / 0.55), 5.05, 5.6)
    trace(icon, "oy", lambda t, rise=rise: rise * S * (1 - smooth((t - 5.05) / 0.55)), 5.05, 5.6)

caption = video.add(Text("Pick a template. Add your story.\nCreate your video with AutoAE.",
                         8 * S, bold=True, color="#141414", line_height=13 / 8,
                         at=px(360, 761.5), opacity=0), start=S3, end=S4)  # fmt: skip
trace(caption, "opacity", lambda t: smooth((t - 5.35) / 0.45), 5.35, 5.8)

# -- scene 4 (6.85-8.5 s): "Make it yours" with floating template cards -----------------------

for x, y, size in ((130, 760, 100), (215, 740, 115), (300, 760, 100)):
    mark = video.add(Text("?", size * S, color=QUESTION_GREEN, at=px(x, y)), start=S4)
    trace(mark, "opacity", lambda t: 220 / 255 * smooth((t - 6.55) / 0.55), S4, 7.1)

CARDS = [
    # (title, size, enter at, x from -> to, y from -> to, entry length, drift px, drift rate)
    ("Your Footage\nNo.1", (102, 170), 6.75, (430, 510), (800, 700), 0.55, 5, 2.0),
    ("Your Footage\nNo.2", (92, 154), 7.05, (80, 65), (1040, 890), 0.6, 4, 1.7),
    ("Your Footage\nNo.3", (102, 170), 7.3, (510, 470), (1100, 1000), 0.6, 0, 0.0),
]
for i, (title, (w, h), at, xs, ys, length, drift, rate) in enumerate(CARDS, start=1):

    def left(t, at=at, xs=xs, length=length, drift=drift, rate=rate):
        return mix(*xs, ease_out((t - at) / length)) + math.sin((t - at) * rate) * drift

    def top(t, at=at, ys=ys, length=length):
        return mix(*ys, ease_out((t - at) / length))

    shown = max(at, S4)
    centre = px(left(shown) + w / 2, top(shown) + h / 2)
    thumb = video.add(Image(card(f"card{i}.png", w, h, title), width=w * S, at=centre), start=shown)
    trace(thumb, "x", lambda t, left=left, w=w: (left(t) + w / 2) / W, shown,
          video.duration if drift else at + length)  # fmt: skip
    trace(thumb, "y", lambda t, top=top, h=h: (top(t) + h / 2) / H, shown, at + length)

video.add(Text("Make it yours", 20 * S, bold=True, color=BLACK, at=px(360, 760)), start=S4)
