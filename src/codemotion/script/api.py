"""The CodeMotion script API: what a video script imports.

    from codemotion import Video, Text, Code, Image, Shape, Line, Terminal, Group, ease, stagger

    video = Video(duration=8, aspect="9:16")
    title = video.add(Text("Hello", size=72, at=(0.5, 0.2)))
    title.fade_in(0.2).move_by(dy=-40, start=0.2)

Positions (``at``, ``move_to``) are fractions of the frame from the top-left; distances and
sizes (``move_by``, ``size``, ``width``, ``radius``) are pixels on a 1080 px short edge;
times are seconds from the start of the video. Animation methods return the element, so
they chain.

Building a script only records data (a ``SceneDoc``); nothing is drawn until CodeMotion
renders the document. This module must stay light: no Skia or numpy imports.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codemotion import config
from codemotion.script import doc as d

_registry: list[Video] = []  # every Video created while a script runs, in order


class ScriptError(ValueError):
    """A mistake in how a script uses the API; the message says how to fix it."""


def _check(model: type[BaseModel], data: dict[str, Any], what: str) -> BaseModel:
    """Validate now, so errors point at the script line that caused them."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"] if p != "from")
        message = str(first["msg"]).removeprefix("Value error, ")
        if field == "duration":
            message = (
                f"must be {config.DURATION_MIN:g}-{config.DURATION_MAX:g} seconds "
                f"in steps of {config.DURATION_STEP:g}"
            )
        where = f" '{field}'" if field else ""
        raise ScriptError(f"{what}{where}: {message}") from None


def _number(value: float, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise ScriptError(f"{name} must be a number, got {value!r}")
    return float(value)


class _Ease:
    """Easing curve names for the ``ease=`` argument of animation methods."""

    linear = "linear"
    in_out_cubic = "easeInOutCubic"
    out_cubic = "easeOutCubic"
    out_back = "easeOutBack"  # overshoots slightly, then settles
    out_expo = "easeOutExpo"

    @staticmethod
    def spring(stiffness: float = 180.0, damping: float = 20.0) -> str:
        """A physical spring; lower damping bounces more."""
        name = f"spring:{_number(stiffness, 'stiffness'):g}:{_number(damping, 'damping'):g}"
        return d.check_ease(name)


ease = _Ease()


class Element:
    """Base class of everything a video can show."""

    kind = "element"

    def __init__(
        self,
        *,
        at: tuple[float, float] | None = (0.5, 0.5),
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        if at is not None:
            if not isinstance(at, tuple | list) or len(at) != 2:
                raise ScriptError(f"at must be an (x, y) pair of fractions, got {at!r}")
            at = (_number(at[0], "at x"), _number(at[1], "at y"))
        self.name = name
        self._fields: dict[str, Any] = {
            "x": None if at is None else at[0],
            "y": None if at is None else at[1],
            "opacity": opacity,
            "scale": scale,
            "rotation": rotation,
        }
        self._anims: list[dict[str, Any]] = []
        self._start = 0.0
        self._end: float | None = None
        self._added = False

    # -- generic animation -----------------------------------------------------------------

    def animate(
        self,
        prop: str,
        *,
        to: float | None = None,
        by: float | None = None,
        start: float = 0.0,
        length: float = 0.5,
        ease: str = _Ease.in_out_cubic,
        from_: float | None = None,
    ) -> Any:
        """Animate a property: x, y, ox, oy, opacity, scale, rotation or reveal."""
        anim: dict[str, Any] = {"prop": prop, "start": start, "length": length, "ease": ease}
        for key, value in (("to", to), ("by", by), ("from", from_)):
            if value is not None:
                anim[key] = _number(value, key)
        _check(d.Anim, anim, f"{prop} animation")
        self._anims.append(anim)
        return self

    def set(self, prop: str, value: float, at: float = 0.0) -> Any:
        """Jump a property to ``value`` at time ``at``."""
        return self.animate(prop, to=value, start=at, length=0.0, ease=_Ease.linear)

    # -- named animations ------------------------------------------------------------------

    def fade_in(self, start: float = 0.0, length: float = 0.5, ease: str = _Ease.out_cubic) -> Any:
        return self.animate("opacity", from_=0.0, to=1.0, start=start, length=length, ease=ease)

    def fade_out(
        self, start: float = 0.0, length: float = 0.5, ease: str = _Ease.in_out_cubic
    ) -> Any:
        return self.animate("opacity", to=0.0, start=start, length=length, ease=ease)

    def move_to(
        self,
        x: float,
        y: float,
        start: float = 0.0,
        length: float = 0.6,
        ease: str = _Ease.out_cubic,
    ) -> Any:
        """Move the anchor to (x, y), fractions of the frame."""
        self.animate("x", to=x, start=start, length=length, ease=ease)
        return self.animate("y", to=y, start=start, length=length, ease=ease)

    def move_by(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        start: float = 0.0,
        length: float = 0.6,
        ease: str = _Ease.out_cubic,
    ) -> Any:
        """Shift by (dx, dy) pixels (1080 px short edge); negative dy moves up."""
        if dx:
            self.animate("ox", by=dx, start=start, length=length, ease=ease)
        if dy:
            self.animate("oy", by=dy, start=start, length=length, ease=ease)
        return self

    def scale_to(
        self, s: float, start: float = 0.0, length: float = 0.5, ease: str = _Ease.out_cubic
    ) -> Any:
        return self.animate("scale", to=s, start=start, length=length, ease=ease)

    def rotate_to(
        self, degrees: float, start: float = 0.0, length: float = 0.6, ease: str = _Ease.out_cubic
    ) -> Any:
        return self.animate("rotation", to=degrees, start=start, length=length, ease=ease)

    def rotate_by(
        self, degrees: float, start: float = 0.0, length: float = 0.6, ease: str = _Ease.out_cubic
    ) -> Any:
        return self.animate("rotation", by=degrees, start=start, length=length, ease=ease)

    def pop_in(self, start: float = 0.0, length: float = 0.5) -> Any:
        """Grow from 60 % with a slight overshoot while fading in."""
        self.animate("opacity", from_=0.0, to=1.0, start=start, length=length * 0.5,
                     ease=_Ease.out_cubic)  # fmt: skip
        return self.animate("scale", from_=0.6, to=1.0, start=start, length=length,
                            ease=_Ease.out_back)  # fmt: skip

    def slide_in(
        self,
        direction: str = "up",
        start: float = 0.0,
        length: float = 0.6,
        distance: float = 60.0,
        ease: str = _Ease.out_cubic,
    ) -> Any:
        """Fade in while sliding ``distance`` px into place (direction of travel)."""
        prop, sign = self._direction(direction)
        self.fade_in(start, length * 0.7)
        return self.animate(prop, from_=-sign * distance, to=0.0, start=start, length=length,
                            ease=ease)  # fmt: skip

    def slide_out(
        self,
        direction: str = "down",
        start: float = 0.0,
        length: float = 0.5,
        distance: float = 60.0,
        ease: str = _Ease.in_out_cubic,
    ) -> Any:
        prop, sign = self._direction(direction)
        self.fade_out(start, length)
        return self.animate(prop, by=sign * distance, start=start, length=length, ease=ease)

    @staticmethod
    def _direction(direction: str) -> tuple[str, float]:
        table = {
            "up": ("oy", -1.0),
            "down": ("oy", 1.0),
            "left": ("ox", -1.0),
            "right": ("ox", 1.0),
        }
        if direction not in table:
            raise ScriptError(f"direction must be one of {', '.join(table)}, got {direction!r}")
        return table[direction]

    # -- document --------------------------------------------------------------------------

    def _payload(self) -> dict[str, Any]:
        return {}

    def _to_doc(self, element_id: str) -> dict[str, Any]:
        data = {"id": element_id, "type": self.kind, "start": self._start, "end": self._end}
        data.update(self._fields)
        data["anims"] = list(self._anims)
        data.update(self._payload())
        return data


class Text(Element):
    kind = "text"

    def __init__(
        self,
        text: str,
        size: float = 48.0,
        *,
        at: tuple[float, float] = (0.5, 0.5),
        font: str = "sans",
        bold: bool = False,
        color: str = "text",
        align: str = "center",
        line_height: float = 1.2,
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        super().__init__(at=at, opacity=opacity, scale=scale, rotation=rotation, name=name)
        if font not in ("sans", "mono"):
            raise ScriptError(f"font must be 'sans' or 'mono', got {font!r}")
        self._text = {
            "text": str(text),
            "size": size,
            "font": f"{font}_bold" if bold else font,
            "color": color,
            "align": align,
            "line_height": line_height,
        }
        _check(d.TextElement, {"id": "check", **self._text}, "Text")

    def type_on(self, start: float = 0.0, length: float = 1.0) -> Text:
        """Reveal the text character by character."""
        self.animate("reveal", from_=0.0, to=1.0, start=start, length=length, ease=_Ease.linear)
        return self

    def _payload(self) -> dict[str, Any]:
        return dict(self._text)


class Code(Element):
    """An editor card showing syntax-highlighted code, revealed like a typist would."""

    kind = "code"

    def __init__(
        self,
        code: str,
        *,
        filename: str = config.DEFAULT_FILENAME,
        reveal: str = "typewriter",
        reveal_start: float | None = None,
        reveal_length: float | None = None,
        region: tuple[float, float, float, float] | None = None,
        at: tuple[float, float] | None = None,
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        super().__init__(at=at, opacity=opacity, scale=scale, rotation=rotation, name=name)
        self._code: dict[str, Any] = {
            "code": str(code),
            "filename": filename,
            "reveal": reveal,
            "reveal_start": reveal_start,
            "reveal_length": reveal_length,
            "region": region,
        }
        self._highlights: list[dict[str, Any]] = []
        _check(d.CodeElement, {"id": "check", **self._code}, "Code")

    @classmethod
    def file(cls, path: str | Path, **options: Any) -> Code:
        """Show the contents of a file (relative to the script's folder)."""
        file = Path(path)
        try:
            text = file.read_bytes().decode("utf-8-sig")
        except FileNotFoundError:
            raise ScriptError(f"Code.file: '{path}' not found next to the script") from None
        except UnicodeDecodeError:
            raise ScriptError(f"Code.file: '{path}' is not UTF-8 text") from None
        options.setdefault("filename", file.name[:40])
        return cls(text, **options)

    def highlight(
        self,
        lines: str,
        start: float = 0.0,
        end: float | None = None,
        fade: float = 0.4,
        zoom: float = config.HIGHLIGHT_ZOOM,
    ) -> Code:
        """Spotlight lines like "3-5,9": other lines dim, an accent bar appears and the card
        zooms in slightly on them (``zoom=1`` keeps it still)."""
        data = {"lines": str(lines), "start": start, "end": end, "fade": fade, "zoom": zoom}
        _check(d.Highlight, data, "highlight")
        self._highlights.append(data)
        return self

    def _payload(self) -> dict[str, Any]:
        return {**self._code, "highlights": list(self._highlights)}


class Image(Element):
    kind = "image"

    def __init__(
        self,
        path: str | Path,
        width: float = 200.0,
        *,
        at: tuple[float, float] = (0.5, 0.5),
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        super().__init__(at=at, opacity=opacity, scale=scale, rotation=rotation, name=name)
        file = Path(path).resolve()
        if file.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ScriptError(f"Image: only PNG and JPEG files are supported, got '{path}'")
        if not file.is_file():
            raise ScriptError(f"Image: '{path}' not found next to the script")
        if file.stat().st_size > config.IMAGE_MAX_BYTES:
            raise ScriptError(f"Image: '{path}' is larger than 5 MB")
        self._image = {"path": str(file), "width": width}
        _check(d.ImageElement, {"id": "check", **self._image}, "Image")

    def _payload(self) -> dict[str, Any]:
        return dict(self._image)


class Shape(Element):
    """Rectangles and circles: ``Shape.rect(...)`` and ``Shape.circle(...)``.

    ``color`` fills the shape (``None``: outline only); ``stroke`` draws an outline
    ``stroke_width`` pixels wide.
    """

    kind = "shape"

    def __init__(
        self,
        shape: str,
        width: float,
        height: float,
        *,
        radius: float = 0.0,
        color: str | None = "accent",
        stroke: str | None = None,
        stroke_width: float = 2.0,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self._shape = {
            "shape": shape,
            "width": width,
            "height": height,
            "radius": radius,
            "color": color,
            "stroke": stroke,
            "stroke_width": stroke_width,
        }
        _check(d.ShapeElement, {"id": "check", **self._shape}, "Shape")

    @classmethod
    def rect(
        cls,
        width: float,
        height: float,
        *,
        radius: float = 0.0,
        color: str | None = "accent",
        stroke: str | None = None,
        stroke_width: float = 2.0,
        **options: Any,
    ) -> Shape:
        return cls("rect", width, height, radius=radius, color=color, stroke=stroke,
                   stroke_width=stroke_width, **options)  # fmt: skip

    @classmethod
    def circle(
        cls,
        radius: float,
        *,
        color: str | None = "accent",
        stroke: str | None = None,
        stroke_width: float = 2.0,
        **options: Any,
    ) -> Shape:
        return cls("circle", radius * 2, radius * 2, color=color, stroke=stroke,
                   stroke_width=stroke_width, **options)  # fmt: skip

    def _payload(self) -> dict[str, Any]:
        return dict(self._shape)


class Line(Element):
    """A straight line between two points (frame fractions), anchored at its midpoint.

    ``draw_on()`` draws it from the first point to the second; ``arrow=True`` adds an
    arrowhead at the second point.
    """

    kind = "line"

    def __init__(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
        *,
        width: float = 4.0,
        color: str = "accent",
        arrow: bool = False,
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        a, b = _point(a, "a"), _point(b, "b")
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        super().__init__(at=mid, opacity=opacity, scale=scale, rotation=rotation, name=name)
        self._line = {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "width": width,
                      "color": color, "arrow": bool(arrow)}  # fmt: skip
        _check(d.LineElement, {"id": "check", **self._line}, "Line")

    def draw_on(self, start: float = 0.0, length: float = 0.6, ease: str = _Ease.out_cubic) -> Line:
        """Draw the line from its first point to its second."""
        self.animate("reveal", from_=0.0, to=1.0, start=start, length=length, ease=ease)
        return self

    def _payload(self) -> dict[str, Any]:
        return dict(self._line)


class Terminal(Element):
    """A terminal panel: ``prompt command`` is typed, then ``output`` prints line by line.

    ``command=None`` prints only the output. The width defaults to the safe area's width.
    """

    kind = "terminal"

    def __init__(
        self,
        output: str = "",
        *,
        command: str | None = "python main.py",
        prompt: str = "$",
        title: str = "Terminal",
        size: float = config.TERMINAL_FONT_SIZE,
        width: float | None = None,
        reveal_start: float | None = None,
        type_speed: float = config.TERMINAL_TYPE_CPS,
        line_interval: float = config.TERMINAL_LINE_INTERVAL,
        at: tuple[float, float] = (0.5, 0.5),
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        super().__init__(at=at, opacity=opacity, scale=scale, rotation=rotation, name=name)
        self._terminal = {
            "output": str(output),
            "command": None if command is None else str(command),
            "prompt": str(prompt),
            "title": str(title),
            "size": size,
            "width": width,
            "reveal_start": reveal_start,
            "type_speed": type_speed,
            "line_interval": line_interval,
        }
        _check(d.TerminalElement, {"id": "check", **self._terminal}, "Terminal")

    def _payload(self) -> dict[str, Any]:
        return dict(self._terminal)


class Group(Element):
    """Elements drawn, moved, scaled, rotated and faded together.

    Children keep their own positions (frame fractions) and animations; the group's
    animations apply on top, pivoting on the group's anchor ``at``. A translucent group
    fades as one object: its children don't show through each other.
    """

    kind = "group"

    def __init__(
        self,
        *children: Element,
        at: tuple[float, float] = (0.5, 0.5),
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        name: str | None = None,
    ) -> None:
        super().__init__(at=at, opacity=opacity, scale=scale, rotation=rotation, name=name)
        self.children: list[Element] = []
        for child in children:
            self.add(child)

    def add(self, element: E, start: float = 0.0, end: float | None = None) -> E:
        """Add ``element`` to the group, shown from ``start`` until ``end`` (video times)."""
        _attach(element, start, end, None)
        self.children.append(element)
        return element


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, tuple | list) or len(value) != 2:
        raise ScriptError(f"{name} must be an (x, y) pair of fractions, got {value!r}")
    return (_number(value[0], f"{name} x"), _number(value[1], f"{name} y"))


def _attach(element: Any, start: float, end: float | None, duration: float | None) -> None:
    """Mark ``element`` as placed (in the video or a group) with its lifetime."""
    if not isinstance(element, Element):
        raise ScriptError(
            f"add() takes Text, Code, Image, Shape, Line, Terminal or Group, got {element!r}"
        )
    if element._added:
        raise ScriptError("this element was already added to the video or a group")
    start = _number(start, "start")
    if start < 0 or (duration is not None and start >= duration):
        limit = f"between 0 and {duration:g} s" if duration is not None else "0 or more"
        raise ScriptError(f"start must be {limit}, got {start:g}")
    if end is not None and not start < _number(end, "end"):
        raise ScriptError(f"end ({end:g} s) must be after start ({start:g} s)")
    element._start, element._end, element._added = start, end, True


E = TypeVar("E", bound=Element)


class Video:
    """The video a script describes. Create exactly one per script."""

    def __init__(
        self,
        duration: float,
        aspect: str = "9:16",
        fps: int = 30,
        theme: str = config.DEFAULT_THEME,
        background: str = "theme",
        fade_in: float = 0.4,
        fade_out: float = 0.0,
    ) -> None:
        self._settings = {
            "duration": duration,
            "aspect": aspect,
            "fps": fps,
            "theme": theme,
            "background": background,
            "fade_in": fade_in,
            "fade_out": fade_out,
        }
        _check(d.SceneDoc, self._settings, "Video")
        self.duration = float(duration)
        self.aspect = aspect
        self._elements: list[Element] = []
        self._sounds: list[dict[str, Any]] = []
        _registry.append(self)

    def add(self, element: E, start: float = 0.0, end: float | None = None) -> E:
        """Show ``element`` from ``start`` until ``end`` (default: the end of the video)."""
        _attach(element, start, end, self.duration)
        self._elements.append(element)
        return element

    def sound(self, sound: str, at: float = 0.0, volume: float = 1.0) -> Video:
        """Play a sound effect at ``at`` seconds.

        ``sound`` is a built-in effect (``"whoosh"``, ``"chime"``, ``"pop"``, ``"click"``,
        ``"riser"``) or an audio file next to the script (WAV, MP3, OGG, FLAC, M4A).
        ``volume`` 1 is the normal level; 0.5 is half as loud.
        """
        name = str(sound)
        if name not in d.SOUND_PRESETS:
            file = Path(name).resolve()
            if file.suffix.lower() not in config.SOUND_FILE_SUFFIXES:
                presets = ", ".join(d.SOUND_PRESETS)
                raise ScriptError(
                    f"sound: '{name}' is neither a built-in sound ({presets}) "
                    "nor an audio file (WAV, MP3, OGG, FLAC, M4A)"
                )
            if not file.is_file():
                raise ScriptError(f"sound: '{name}' not found next to the script")
            if file.stat().st_size > config.SOUND_FILE_MAX_BYTES:
                raise ScriptError(f"sound: '{name}' is larger than 20 MB")
            name = str(file)
        cue = {"sound": name, "at": _number(at, "at"), "volume": _number(volume, "volume")}
        _check(d.SoundCue, cue, "sound")
        if len(self._sounds) >= config.MAX_SOUNDS:
            raise ScriptError(f"a video can have at most {config.MAX_SOUNDS} sounds")
        self._sounds.append(cue)
        return self

    def to_doc(self) -> d.SceneDoc:
        counts: dict[str, int] = {}

        def docs(elements: list[Element]) -> list[dict[str, Any]]:
            result = []
            for element in elements:
                counts[element.kind] = counts.get(element.kind, 0) + 1
                data = element._to_doc(element.name or f"{element.kind}{counts[element.kind]}")
                if isinstance(element, Group):
                    data["children"] = docs(element.children)
                result.append(data)
            return result

        doc = {**self._settings, "elements": docs(self._elements), "sounds": list(self._sounds)}
        result = _check(d.SceneDoc, doc, "Video")
        assert isinstance(result, d.SceneDoc)
        return result


def stagger(
    elements: Iterable[E],
    action: str | Callable[[E, float], Any],
    *,
    start: float = 0.0,
    every: float = 0.1,
    **options: Any,
) -> list[E]:
    """Run the same animation on each element, ``every`` seconds apart.

        stagger(cards, "pop_in", start=1.0, every=0.15)
        stagger(dots, lambda dot, t: dot.fade_in(t).move_by(dy=-20, start=t), start=2)

    ``action`` is an animation method name (``options`` are passed to it) or a function
    called with each element and its start time. Returns the elements as a list.
    """
    items = list(elements)
    start, every = _number(start, "start"), _number(every, "every")
    for index, element in enumerate(items):
        at = start + index * every
        if callable(action):
            action(element, at)
            continue
        method = getattr(element, str(action), None)
        if not isinstance(element, Element) or not callable(method) or str(action)[:1] == "_":
            raise ScriptError(f"stagger: {type(element).__name__} has no animation {action!r}")
        method(start=at, **options)
    return items
