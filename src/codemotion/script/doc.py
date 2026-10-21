"""Scene document: the validated, JSON-serializable result of running a video script.

A script builds this through the API in ``codemotion.script.api``; the renderer consumes
only this document, so rendering is deterministic and never re-runs user code.

Units: positions (``x``, ``y``) are fractions of the frame (0-1, from the top-left);
distances and sizes (offsets, font size, width, radius) are design pixels on a 1080 px short
edge; times are seconds from the start of the video.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from codemotion import config
from codemotion.geometry import Color
from codemotion.models import Aspect
from codemotion.timeline.easing import EASINGS

MAX_ELEMENTS = 60
MAX_ANIMS = 80

AnimProp = Literal["x", "y", "ox", "oy", "opacity", "scale", "rotation", "reveal"]
THEME_COLORS = ("text", "accent", "muted", "card", "gutter", "background")
_SPRING = re.compile(r"^spring:(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$")
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,39}$")


def check_ease(name: str) -> str:
    match = _SPRING.match(name)
    if name in EASINGS or (match and float(match[1]) > 0 and float(match[2]) > 0):
        return name
    known = ", ".join(EASINGS)
    raise ValueError(f"unknown easing '{name}'; use one of {known} or ease.spring(...)")


def check_color(value: str) -> str:
    if value in THEME_COLORS:
        return value
    try:
        Color.from_hex(value)
    except ValueError:
        names = ", ".join(THEME_COLORS)
        raise ValueError(f"'{value}' is not a colour; use #RRGGBB or one of {names}") from None
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Anim(_Model):
    """Animate ``prop`` from its value at ``start`` (or ``from_``) to ``to`` (or value + ``by``)."""

    prop: AnimProp
    start: float = Field(ge=0)
    length: float = Field(ge=0)
    to: float | None = None
    by: float | None = None
    from_: float | None = Field(None, alias="from")
    ease: str = "easeInOutCubic"

    @field_validator("ease")
    @classmethod
    def _ease(cls, v: str) -> str:
        return check_ease(v)

    @model_validator(mode="after")
    def _target(self) -> Anim:
        if (self.to is None) == (self.by is None):
            raise ValueError("an animation needs exactly one of 'to' or 'by'")
        return self


class Highlight(_Model):
    lines: str  # e.g. "3-5,9"; validated against the code's line count when prepared
    start: float = Field(ge=0)
    end: float | None = None
    fade: float = Field(0.4, ge=0)
    zoom: float = Field(config.HIGHLIGHT_ZOOM, ge=1, le=config.HIGHLIGHT_ZOOM_MAX)


class _Element(_Model):
    id: str
    start: float = Field(0.0, ge=0)
    end: float | None = None
    x: float | None = None  # None: the element's natural position (centre; code: safe area)
    y: float | None = None
    opacity: float = Field(1.0, ge=0, le=1)
    scale: float = Field(1.0, ge=0)
    rotation: float = 0.0
    anims: list[Anim] = Field(default_factory=list, max_length=MAX_ANIMS)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("names must start with a letter and use letters, digits, _ or -")
        return v

    @model_validator(mode="after")
    def _times(self) -> _Element:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"'{self.id}' ends ({self.end:g} s) before it starts")
        return self


class TextElement(_Element):
    type: Literal["text"] = "text"
    text: str
    size: float = Field(48.0, gt=0, le=400)
    font: Literal["sans", "sans_bold", "mono", "mono_bold"] = "sans"
    color: str = "text"
    align: Literal["left", "center", "right"] = "center"
    line_height: float = Field(1.2, gt=0, le=4)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return check_color(v)


class CodeElement(_Element):
    type: Literal["code"] = "code"
    code: str
    filename: str = Field(config.DEFAULT_FILENAME, max_length=40)
    reveal: Literal["typewriter", "line_cascade", "fade_in", "none"] = "typewriter"
    reveal_start: float | None = Field(None, ge=0)  # default: the element's start
    reveal_length: float | None = Field(None, ge=0)  # default: automatic
    region: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 fractions
    highlights: list[Highlight] = Field(default_factory=list, max_length=20)

    @field_validator("region")
    @classmethod
    def _region(
        cls, v: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if v is not None:
            x0, y0, x1, y1 = v
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("region must be (x0, y0, x1, y1) with 0 <= x0 < x1 <= 1, ...")
        return v


class ImageElement(_Element):
    type: Literal["image"] = "image"
    path: str
    width: float = Field(200.0, gt=0, le=4000)


class ShapeElement(_Element):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "circle"]
    width: float = Field(100.0, gt=0, le=4000)  # circle: diameter
    height: float = Field(100.0, gt=0, le=4000)
    radius: float = Field(0.0, ge=0)  # rect corner radius
    color: str | None = "accent"  # fill; None: outline only
    stroke: str | None = None  # outline colour
    stroke_width: float = Field(2.0, gt=0, le=200)

    @field_validator("color", "stroke")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return None if v is None else check_color(v)

    @model_validator(mode="after")
    def _visible(self) -> ShapeElement:
        if self.color is None and self.stroke is None:
            raise ValueError("a shape needs a fill colour, a stroke colour, or both")
        return self


class LineElement(_Element):
    """A straight line from (x1, y1) to (x2, y2), frame fractions; anchored at its midpoint.

    ``reveal`` (0-1) is how much of it is drawn, from the first point.
    """

    type: Literal["line"] = "line"
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = Field(4.0, gt=0, le=200)
    color: str = "accent"
    arrow: bool = False  # arrowhead at (x2, y2)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return check_color(v)


class TerminalElement(_Element):
    """A terminal panel: a prompt and command typed, then output lines printed."""

    type: Literal["terminal"] = "terminal"
    output: str = ""  # any length: long lines wrap, older lines scroll up
    command: str | None = "python main.py"
    prompt: str = Field("$", max_length=8)
    title: str = Field("Terminal", max_length=40)
    size: float = Field(config.TERMINAL_FONT_SIZE, gt=0, le=120)
    width: float | None = Field(None, gt=0, le=4000)  # default: the safe area's width
    reveal_start: float | None = Field(None, ge=0)  # default: shortly after the start
    type_speed: float = Field(config.TERMINAL_TYPE_CPS, gt=0, le=1000)  # chars per second
    line_interval: float = Field(config.TERMINAL_LINE_INTERVAL, ge=0, le=5)  # seconds


class GroupElement(_Element):
    """Children drawn and animated together; the group's transform pivots on its anchor."""

    type: Literal["group"] = "group"
    children: list[Element] = Field(default_factory=list)


Element = Annotated[
    TextElement
    | CodeElement
    | ImageElement
    | ShapeElement
    | LineElement
    | TerminalElement
    | GroupElement,
    Field(discriminator="type"),
]
GroupElement.model_rebuild()
AnyElement = (
    TextElement
    | CodeElement
    | ImageElement
    | ShapeElement
    | LineElement
    | TerminalElement
    | GroupElement
)


def walk(elements: list[AnyElement]) -> Iterator[AnyElement]:
    """Every element, groups before their children, in drawing order."""
    for element in elements:
        yield element
        if isinstance(element, GroupElement):
            yield from walk(element.children)


SOUND_PRESETS = ("whoosh", "chime", "pop", "click", "riser")


class SoundCue(_Model):
    """A sound effect starting at ``at`` seconds: a built-in preset or an audio file."""

    sound: str  # a preset name, or an absolute path to an audio file
    at: float = Field(0.0, ge=0)
    volume: float = Field(1.0, ge=0, le=4)

    @property
    def is_preset(self) -> bool:
        return self.sound in SOUND_PRESETS


class SceneDoc(_Model):
    duration: float = Field(
        ge=config.DURATION_MIN, le=config.DURATION_MAX, multiple_of=config.DURATION_STEP
    )
    aspect: Aspect = Aspect.portrait
    fps: Literal[30, 60] = 30
    theme: str = config.DEFAULT_THEME
    background: str = "theme"  # "theme" (gradient + glows) or a solid #RRGGBB
    fade_in: float = Field(0.4, ge=0, le=3)  # backdrop fades in from black
    fade_out: float = Field(0.0, ge=0, le=3)  # whole frame fades to black at the end
    elements: list[Element] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    sounds: list[SoundCue] = Field(default_factory=list, max_length=config.MAX_SOUNDS)

    @field_validator("background")
    @classmethod
    def _background(cls, v: str) -> str:
        if v != "theme":
            Color.from_hex(v)
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> SceneDoc:
        seen: set[str] = set()
        for element in walk(self.elements):
            if element.id in seen:
                raise ValueError(f"two elements are named '{element.id}'")
            seen.add(element.id)
        if len(seen) > MAX_ELEMENTS:
            raise ValueError(f"a video can have at most {MAX_ELEMENTS} elements (groups count)")
        return self
