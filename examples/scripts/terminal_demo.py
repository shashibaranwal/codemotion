"""A code card types a small program, then a terminal runs it (Slate theme)."""

from codemotion import Code, Terminal, Text, Video

video = Video(duration=10, aspect="9:16", theme="slate", fade_out=0.3)

title = video.add(Text("Write it. Run it.", 60, bold=True, at=(0.5, 0.1)))
title.slide_in("down", start=0.2)

program = '''def greet(name):
    return f"Hello, {name}!"

for name in ["Ada", "Linus", "Grace"]:
    print(greet(name))
'''
code = video.add(Code(program, filename="greet.py", region=(0.06, 0.15, 0.94, 0.5)), start=0.5)
code.pop_in(start=0.5)
code.highlight("4-5", start=4.4, end=8.5)

output = "Hello, Ada!\nHello, Linus!\nHello, Grace!"
terminal = Terminal(output, command="python greet.py", at=(0.5, 0.64), reveal_start=4.8)
video.add(terminal, start=4.2).slide_in("up", start=4.2)
