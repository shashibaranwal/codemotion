"""A small diagram: grouped boxes, arrows that draw on, and a staggered entrance (Daylight)."""

from codemotion import Group, Line, Shape, Text, Video, ease, stagger

video = Video(duration=9, aspect="16:9", theme="daylight", fade_out=0.3)

title = video.add(Text("How a request flows", 64, bold=True, at=(0.5, 0.15)))
title.slide_in("down", start=0.2)

STEPS = [("Browser", "GET /orders"), ("API", "checks auth"), ("Database", "returns 42 rows")]
XS = (0.2, 0.5, 0.8)
Y = 0.48

# Everything in one group, so the whole system can move and fade as one object.
system = video.add(Group(at=(0.5, Y)))

# A request travels along the arrows; added first, so it passes behind the boxes.
packet = system.add(Shape.circle(13, color="#F08A24", at=(XS[0] + 0.115, Y)), start=2.9)
packet.fade_in(2.9, 0.2).move_to(XS[2] - 0.115, Y, start=3.0, length=1.6, ease=ease.in_out_cubic)
packet.fade_out(4.6, 0.3)

boxes = []
for (name, note), x in zip(STEPS, XS, strict=True):
    box = system.add(Group(at=(x, Y)))
    box.add(Shape.rect(400, 230, radius=28, color="card", stroke="accent", stroke_width=3,
                       at=(x, Y)))  # fmt: skip
    box.add(Text(name, 46, bold=True, at=(x, Y - 0.035)))
    box.add(Text(note, 30, color="muted", at=(x, Y + 0.045)))
    boxes.append(box)
stagger(boxes, "pop_in", start=0.6, every=0.3)

arrows = [
    system.add(Line((XS[i] + 0.115, Y), (XS[i + 1] - 0.115, Y), width=5, arrow=True))
    for i in range(2)
]
stagger(arrows, "draw_on", start=1.8, every=0.45, length=0.45)

# Step back and sum it up.
system.scale_to(0.86, start=5.2, length=0.8, ease=ease.in_out_cubic)
system.move_by(dy=-40, start=5.2, length=0.8, ease=ease.in_out_cubic)
caption = video.add(Text("~120 ms, end to end", 52, bold=True, color="accent", at=(0.5, 0.8)))
caption.slide_in("up", start=5.6)
