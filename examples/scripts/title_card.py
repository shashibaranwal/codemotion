"""A title card: accent bar, headline and a typed-on subtitle."""

from codemotion import Shape, Text, Video, ease

video = Video(duration=6, aspect="9:16")

bar = video.add(Shape.rect(120, 8, radius=4, at=(0.5, 0.38)))
bar.animate("scale", from_=0.0, to=1.0, start=0.3, length=0.7, ease=ease.out_expo)

title = video.add(Text("Python tips", 104, bold=True, at=(0.5, 0.45)))
title.slide_in("up", start=0.5)

subtitle = video.add(Text("#1  list comprehensions", 40, color="muted", at=(0.5, 0.52)))
subtitle.type_on(start=1.1, length=1.2)

for element in (bar, title, subtitle):
    element.fade_out(start=5.2, length=0.6)
