"""A staggered list built with a loop, plus a logo: scripts are just Python."""

from codemotion import Image, Shape, Text, Video

video = Video(duration=8, aspect="1:1", theme="midnight")
points = ["Readable", "Batteries included", "Huge ecosystem", "Runs everywhere"]

video.add(Text("Why Python?", 76, bold=True, at=(0.5, 0.17))).pop_in(start=0.2)

for i, point in enumerate(points):
    start = 0.9 + i * 0.45
    y = 0.35 + i * 0.13
    video.add(Shape.circle(9, at=(0.2, y))).pop_in(start=start)
    item = video.add(Text(point, 50, align="left", at=(0.25, y)))
    item.slide_in("right", start=start, distance=40)

logo = video.add(Image("assets/logo.png", width=110, at=(0.86, 0.86)), start=3.5)
logo.pop_in(start=3.5).rotate_by(360, start=3.5, length=1.2)
