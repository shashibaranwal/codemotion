"""The starter script written by ``codemotion new``."""

STARTER = '''"""My CodeMotion video. Render it with:  codemotion render {name}"""

from codemotion import Code, Shape, Text, Video, ease

video = Video(duration=8, aspect="9:16")

title = video.add(Text("Hello, CodeMotion", 64, bold=True, at=(0.5, 0.18)))
title.slide_in("up", start=0.2)

code = video.add(Code('print("Hello, world!")'), start=0.8)
code.pop_in(start=0.8)

dot = video.add(Shape.circle(12, at=(0.5, 0.72)), start=2.5)
dot.pop_in(start=2.5).move_by(dy=-30, start=3.2, ease=ease.spring())
'''
