"""
Recreation of the uploaded minimalist motion-graphics reference.

Renders an 8.57-second, 9:16 MP4 with:
- white background, black typography, electric-blue accents
- opening "Create with AutoAE" text reveal and blue glow
- swinging pendulum over a curved measurement scale
- "Your ideas come alive" with people icons and corner brackets
- floating rounded template cards and outlined question marks

Requirements:
    pip install pillow opencv-python numpy

Run:
    python recreate_motion_graphic.py

Output:
    recreated_motion_graphic.mp4

This is a procedural recreation of the visual style and sequence, not a
pixel-perfect copy of the original video's proprietary artwork/assets.
"""

import math
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 720, 1280
FPS = 30
DURATION = 8.5667
TOTAL_FRAMES = round(FPS * DURATION)
OUTPUT = "recreated_motion_graphic.mp4"

BLACK = (10, 10, 12)
BLUE = (36, 124, 255)
PALE_BLUE = (185, 220, 255)
BRACKET_BLUE = (45, 125, 255)
LIGHT_GREEN = (175, 255, 185)
GREY = (125, 125, 125)
WHITE = (255, 255, 255)

# Common Linux fonts; Pillow falls back if the preferred font is unavailable.
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(size, bold=False):
    path = FONT_BOLD if bold else FONT_REG
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


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


def text_center(draw, xy, text, fnt, fill, anchor="mm", stroke_width=0, stroke_fill=None):
    draw.text(xy, text, font=fnt, fill=fill, anchor=anchor,
              stroke_width=stroke_width, stroke_fill=stroke_fill)


def draw_glow(img, cx, cy, radius=150, color=(205, 225, 255), opacity=100):
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
               fill=(*color, opacity))
    glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.55))
    img.alpha_composite(glow)


def draw_brackets(draw, x1, y1, x2, y2, length=28, color=BRACKET_BLUE, width=1):
    # Four independent L-shaped corner marks.
    for x, y, sx, sy in [
        (x1, y1, 1, 1), (x2, y1, -1, 1),
        (x1, y2, 1, -1), (x2, y2, -1, -1)
    ]:
        draw.line((x, y, x + sx*length, y), fill=color, width=width)
        draw.line((x, y, x, y + sy*length), fill=color, width=width)


def draw_person(draw, cx, cy, scale=1.0, color=BLACK, main=False):
    head_r = int((19 if main else 12) * scale)
    body_w = int((34 if main else 22) * scale)
    body_h = int((28 if main else 17) * scale)
    head_y = cy - int((24 if main else 15) * scale)
    draw.ellipse((cx-head_r, head_y-head_r, cx+head_r, head_y+head_r), fill=color)
    top = cy + int(1 * scale)
    draw.ellipse((cx-body_w, top, cx+body_w, top+body_h*2), fill=color)


def draw_people_group(draw, cx, y, t):
    # Central person grows in first; surrounding people follow.
    p = smooth((t - 5.05) / 0.55)
    positions = [-110, -70, 70, 110]
    scales = [0.75, 0.9, 0.9, 0.75]
    for dx, sc in zip(positions, scales):
        draw_person(draw, int(cx+dx), int(y+22*(1-p)), sc*p, BLACK, False)
    draw_person(draw, cx, int(y+20*(1-p)), 1.15*p, BLUE, True)


def draw_pendulum(img, draw, t):
    # Reference-like curved arc and fine graduation marks.
    cx, cy = 230, 460
    radius = 470
    # Arc across the upper part of the frame.
    start_deg, end_deg = 205, 337
    pts = []
    for i in range(120):
        a = math.radians(start_deg + (end_deg-start_deg)*i/119)
        pts.append((cx + radius*math.cos(a), cy + radius*math.sin(a)))
    draw.line(pts, fill=(38, 95, 225), width=2)

    # Scale ticks following the arc.
    for i in range(76):
        a_deg = start_deg + (end_deg-start_deg)*i/75
        a = math.radians(a_deg)
        r1 = radius - (12 if i % 5 else 23)
        r2 = radius + 2
        p1 = (cx+r1*math.cos(a), cy+r1*math.sin(a))
        p2 = (cx+r2*math.cos(a), cy+r2*math.sin(a))
        col = (190, 211, 248) if i % 5 else (80, 100, 130)
        draw.line((p1, p2), fill=col, width=1)

    # Four small points on the curved track.
    for deg in (218, 244, 270, 300, 326):
        a = math.radians(deg)
        px, py = cx + radius*math.cos(a), cy + radius*math.sin(a)
        draw.ellipse((px-3, py-3, px+3, py+3), fill=WHITE, outline=(40, 80, 170), width=1)

    # Pendulum pivot and angle vary over time.
    phase = (t - 2.48) / 2.2
    angle = math.radians(-9 + 27 * math.sin(phase * math.pi * 1.05))
    pivot_x, pivot_y = 205, 410
    length = 260
    bob_x = pivot_x + math.sin(angle) * length
    bob_y = pivot_y + math.cos(angle) * length

    # Thin decorative suspension and stylized diamond joint.
    draw.line((pivot_x, pivot_y, bob_x, bob_y), fill=BLACK, width=5)
    jx, jy = pivot_x + math.sin(angle)*66, pivot_y + math.cos(angle)*66
    diamond = [(jx, jy-19), (jx+9, jy), (jx, jy+19), (jx-9, jy)]
    draw.line(diamond + [diamond[0]], fill=BLACK, width=3)
    draw.line((pivot_x-4, pivot_y+72, pivot_x+4, pivot_y+72), fill=BLUE, width=4)
    br = 8
    draw.ellipse((bob_x-br, bob_y-br, bob_x+br, bob_y+br), fill=BLUE, outline=BLACK, width=3)

    # Title fades in while the pendulum swings.
    p = smooth((t-3.2)/0.6)
    if p > 0:
        text_center(draw, (90, 800), "Make", font(15, True), (40, 40, 40, int(255*p)), anchor="lm")
        text_center(draw, (90, 825), "motion", font(29, True), (40, 150, 255, int(255*p)), anchor="lm")


def draw_landscape_card(img, x, y, w, h, title, phase=0.0, alpha=255):
    # Create a little stylized outdoor video thumbnail, clipped to rounded corners.
    card = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    d = ImageDraw.Draw(card)
    # Sky-to-forest gradient.
    for yy in range(h):
        p = yy / max(1, h-1)
        top = (102, 129, 154)
        bottom = (45, 66, 47)
        c = tuple(int(mix(top[i], bottom[i], p)) for i in range(3))
        d.line((0, yy, w, yy), fill=(*c, alpha))
    # Mountain silhouettes.
    d.polygon([(0, h*.50), (w*.20, h*.22), (w*.36, h*.48),
               (w*.55, h*.16), (w*.78, h*.49), (w, h*.28),
               (w, h*.76), (0, h*.76)], fill=(64, 83, 70, alpha))
    d.polygon([(0, h*.69), (w*.24, h*.48), (w*.45, h*.70),
               (w*.67, h*.42), (w, h*.68), (w, h), (0, h)],
              fill=(39, 57, 43, alpha))
    # Large translucent abstract A-like mark.
    d.polygon([(w*.50, h*.25), (w*.72, h*.77), (w*.60, h*.77),
               (w*.50, h*.53), (w*.40, h*.77), (w*.28, h*.77)],
              fill=(235, 235, 235, 60))
    text_center(d, (w//2, int(h*.09)), "AutoAE Demo", font(max(6, int(w*.045))), (245,245,245,alpha))
    text_center(d, (w//2, int(h*.53)), title, font(max(9, int(w*.095)), True), (255,255,255,alpha))
    text_center(d, (w//2, int(h*.92)), "Add media here", font(max(5, int(w*.035))), (230,230,230,alpha))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w-1, h-1), radius=max(8, int(w*.08)), fill=alpha)
    card.putalpha(mask)
    img.alpha_composite(card, (int(x), int(y)))


def draw_question_marks(draw, t):
    p = smooth((t-6.55)/0.55)
    if p <= 0:
        return
    # Draw thin outlined question marks behind title.
    for cx, cy, size in [(130, 760, 100), (215, 740, 115), (300, 760, 100)]:
        text_center(draw, (cx, cy), "?", font(size, False),
                    (170, 255, 180, int(220*p)),
                    stroke_width=1, stroke_fill=(170, 255, 180, int(220*p)))


def render_frame(t):
    img = Image.new("RGBA", (W, H), WHITE + (255,))
    draw = ImageDraw.Draw(img)

    # --- Scene 1: text reveal ---
    if t < 2.55:
        # Soft blue glow grows beneath the revealed product name.
        glow_p = smooth((t-0.65)/0.85)
        if glow_p > 0:
            draw_glow(img, int(390), int(350), int(105+45*glow_p),
                      (208, 225, 255), int(80*glow_p))
        draw = ImageDraw.Draw(img)
        p = smooth(t/0.5)
        # "Create" enters large, then scales down into the phrase.
        if t < 0.55:
            size = int(mix(48, 28, ease_out(t/0.55)))
            x = int(mix(360, 360, ease_out(t/0.55)))
            text_center(draw, (x, 360), "Create", font(size, True), BLACK)
        else:
            # Reveal "with" and then blue "AutoAE".
            text_center(draw, (360, 360), "Create with", font(21, True), BLACK)
            q = smooth((t-0.85)/0.7)
            if q > 0:
                # tiny blue sparkle above the incoming product word
                sx = 485
                sy = int(mix(324, 305, q))
                draw.text((sx, sy), "✦", font=font(12, True), fill=BLUE)
                text_center(draw, (493, 360), "AutoAE", font(21, True), BLUE)
        return img.convert("RGB")

    # --- Scene 2: swinging pendulum and scale ---
    if 2.45 <= t < 4.85:
        draw_pendulum(img, draw, t)
        # A faint bracket-like corner fragment at the scene transition.
        if t > 4.5:
            p = smooth((t-4.5)/0.25)
            draw_brackets(draw, 585, 300, 705, 395, int(28*p), BRACKET_BLUE, 1)
        return img.convert("RGB")

    # --- Scene 3: ideas, people, blue corner marks ---
    if 4.75 <= t < 6.85:
        p = smooth((t-4.8)/0.45)
        # Text sits slightly right of center, matching the reference's asymmetry.
        yshift = int((1-p)*24)
        text_center(draw, (360, 535+yshift), "Your ideas", font(23, True), BLACK)
        text_center(draw, (360, 562+yshift), "come alive", font(23, True), BLUE)
        draw_brackets(draw, 154, 490+yshift, 568, 650+yshift, 30, BRACKET_BLUE, 1)
        draw_people_group(draw, 360, 670, t)
        # tiny instructional caption beneath the people icons
        cap_alpha = int(255*smooth((t-5.35)/0.45))
        caption = "Pick a template. Add your story.\nCreate your video with AutoAE."
        for i, line in enumerate(caption.splitlines()):
            text_center(draw, (360, 755+i*13), line, font(8, True), (20,20,20,cap_alpha))
        # Fade the scene slightly toward the next card showcase.
        return img.convert("RGB")

    # --- Scene 4: template showcase and "Make it yours" ---
    draw_question_marks(draw, t)
    # Title in front of outlined question marks.
    p = smooth((t-6.7)/0.45)
    text_center(draw, (360, 760), "Make it yours", font(20, True), BLACK)

    # Floating template cards enter from off-screen and drift gently.
    if t >= 6.75:
        p1 = ease_out((t-6.75)/0.55)
        x1 = mix(430, 510, p1) + math.sin((t-6.75)*2.0)*5
        y1 = mix(800, 700, p1)
        draw_landscape_card(img, x1, y1, 102, 170, "Your Footage\nNo.1", t)
    if t >= 7.05:
        p2 = ease_out((t-7.05)/0.6)
        x2 = mix(80, 65, p2) + math.sin((t-7.05)*1.7)*4
        y2 = mix(1040, 890, p2)
        draw_landscape_card(img, x2, y2, 92, 154, "Your Footage\nNo.2", t)
    if t >= 7.3:
        p3 = ease_out((t-7.3)/0.6)
        x3 = mix(510, 470, p3)
        y3 = mix(1100, 1000, p3)
        draw_landscape_card(img, x3, y3, 102, 170, "Your Footage\nNo.2", t)
    # Tiny central product title remains stable as cards float.
    if t >= 7.0:
        text_center(draw, (360, 760), "Make it yours", font(20, True), BLACK)
    return img.convert("RGB")


def main():
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(OUTPUT, fourcc, FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("Could not open video writer. Check OpenCV/codec installation.")

    for frame_index in range(TOTAL_FRAMES):
        t = frame_index / FPS
        frame = render_frame(t)
        arr = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
        writer.write(arr)
        if frame_index % 60 == 0:
            print(f"Rendered {frame_index}/{TOTAL_FRAMES} frames")

    writer.release()
    print(f"Done: {OUTPUT} ({TOTAL_FRAMES} frames, {TOTAL_FRAMES/FPS:.2f}s)")


if __name__ == "__main__":
    main()
