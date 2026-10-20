"""Code too long for the card: the camera scrolls with the reveal, then to a highlight."""

from codemotion import Code, Text, Video

video = Video(duration=12, aspect="9:16", fade_out=0.3)

title = video.add(Text("Reading a long file", 56, bold=True, at=(0.5, 0.08)))
title.fade_in(0.2).move_by(dy=-20, start=0.2)

code = Code.file(
    "../long_script.py",
    reveal="line_cascade",
    reveal_length=6.5,
    region=(0.06, 0.13, 0.94, 0.84),
)
video.add(code, start=0.4).fade_in(0.4)
code.highlight("9-16", start=8.0, end=11.0)
