"""Title, a code card that types itself, a highlight and a caption."""

from codemotion import Code, Text, Video

video = Video(duration=10, aspect="9:16", fade_out=0.3)

title = video.add(Text("Memoized Fibonacci", 56, bold=True, at=(0.5, 0.155)))
title.slide_in("down", start=0.2)

code = video.add(Code.file("../fib.py", region=(0.06, 0.2, 0.94, 0.74)), start=0.6)
code.pop_in(start=0.6)
code.highlight("4-7", start=6.2)

caption = Text("@lru_cache turns O(2^n) into O(n)", 36, color="muted", at=(0.5, 0.78))
video.add(caption, start=6.6).slide_in("up", start=6.6)
