"""Request/plan schema (pydantic v2): the single source of truth for CLI, API and presets."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from codemotion import config
from codemotion.errors import UserFacingError


class Aspect(StrEnum):
    portrait = "9:16"
    square = "1:1"
    landscape = "16:9"


class Style(StrEnum):
    typewriter = "typewriter"
    line_cascade = "line_cascade"
    fade_in = "fade_in"


class OutputMode(StrEnum):
    off = "off"
    text = "text"
    run = "run"


OutputFormat = Literal["mp4", "gif", "webm"]


def _default_formats() -> set[OutputFormat]:
    return {"mp4"}


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    duration: float = Field(
        ge=config.DURATION_MIN, le=config.DURATION_MAX, multiple_of=config.DURATION_STEP
    )
    aspect: Aspect = Aspect.portrait
    fps: Literal[30, 60] = 30
    theme: str = config.DEFAULT_THEME
    style: Style = Style.typewriter
    focus: str | None = None
    filename: str = Field(config.DEFAULT_FILENAME, min_length=1, max_length=40)
    title: str | None = Field(None, max_length=config.TITLE_MAX_LENGTH)
    caption: str | None = Field(None, max_length=config.CAPTION_MAX_LENGTH)
    watermark_path: Path | None = None
    output_mode: OutputMode = OutputMode.off
    output_text: str | None = Field(None, max_length=config.OUTPUT_TEXT_MAX_LENGTH)
    outro: Literal["fade", "hold"] = "hold"
    formats: set[OutputFormat] = Field(default_factory=_default_formats)
    preview: bool = False


class CodeWarning(BaseModel):
    """A non-blocking problem with the user's code (e.g. a syntax error in a partial snippet)."""

    line: int | None = None
    column: int | None = None
    message: str


class Phase(BaseModel):
    name: str
    start: int  # frame, inclusive
    end: int  # frame, exclusive


class Plan(BaseModel):
    total_frames: int
    fps: int
    effective_style: Style
    font_size: float
    scroll: bool
    phases: list[Phase]
    warnings: list[str]


_FIELD_MESSAGES: dict[str, str] = {
    "duration": (
        f"Duration must be between {config.DURATION_MIN:g} and {config.DURATION_MAX:g} seconds, "
        f"in steps of {config.DURATION_STEP:g} s."
    ),
    "aspect": "Format must be one of " + ", ".join(a.value for a in Aspect) + ".",
    "fps": "FPS must be " + " or ".join(str(f) for f in config.FPS_OPTIONS) + ".",
    "style": "Style must be one of " + ", ".join(s.value for s in Style) + ".",
    "output_mode": "Output panel must be one of " + ", ".join(m.value for m in OutputMode) + ".",
    "title": f"The title can be at most {config.TITLE_MAX_LENGTH} characters.",
    "caption": f"The caption can be at most {config.CAPTION_MAX_LENGTH} characters.",
    "output_text": f"The output text can be at most {config.OUTPUT_TEXT_MAX_LENGTH} characters.",
    "filename": "The file name must be 1 to 40 characters.",
    "outro": "Outro must be 'fade' or 'hold'.",
    "formats": "Formats must be any of mp4, gif, webm.",
}


def parse_request(data: dict[str, Any]) -> RenderRequest:
    """Build a ``RenderRequest``, turning validation errors into a friendly ``UserFacingError``."""
    try:
        return RenderRequest.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = str(first["loc"][0]) if first["loc"] else None
        if first["type"] == "missing" and field == "duration":
            message = (
                f"Duration is required ({config.DURATION_MIN:g}-{config.DURATION_MAX:g} seconds)."
            )
        elif first["type"] == "missing" and field == "code":
            message = "Paste some Python code first."
        elif first["type"] == "extra_forbidden":
            message = f"Unknown setting '{field}'."
        elif field in _FIELD_MESSAGES:
            message = _FIELD_MESSAGES[field]
        else:
            message = f"Invalid value for '{field}': {first['msg']}."
        raise UserFacingError(message, field=field) from None


class ElementPlan(BaseModel):
    """What one element of a scene will do (for ``plan`` and the desktop app)."""

    id: str
    type: str
    start: float
    end: float
    animations: int
    reveal: str | None = None  # code elements: effective reveal style
    reveal_start: float | None = None
    reveal_end: float | None = None
    font_size: float | None = None
    scroll: bool | None = None
    parent: str | None = None  # the group it belongs to


class ScenePlan(BaseModel):
    duration: float
    fps: int
    total_frames: int
    width: int
    height: int
    elements: list[ElementPlan]
    warnings: list[str]
