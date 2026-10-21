from __future__ import annotations

import pytest

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.models import Aspect, RenderRequest, Style, parse_request
from codemotion.script.doc import TextElement


@pytest.mark.parametrize(
    ("aspect", "full", "preview"),
    [
        ("9:16", (1080, 1920), (540, 960)),
        ("1:1", (1080, 1080), (540, 540)),
        ("16:9", (1920, 1080), (960, 540)),
    ],
)
def test_frame_size(aspect: str, full: tuple[int, int], preview: tuple[int, int]) -> None:
    assert config.frame_size(aspect) == full
    assert config.frame_size(aspect, preview=True) == preview


def test_every_aspect_enum_has_a_preset() -> None:
    assert {a.value for a in Aspect} == set(config.ASPECT_PRESETS)


def test_defaults() -> None:
    req = parse_request({"code": "x = 1", "duration": 8})
    assert req.aspect is Aspect.portrait
    assert req.fps == 30
    assert req.style is Style.typewriter
    assert req.theme == "midnight"
    assert req.filename == "main.py"
    assert req.formats == {"mp4"}


@pytest.mark.parametrize("duration", [3.0, 3.5, 8, 11.5, 12.0])
def test_valid_durations(duration: float) -> None:
    assert parse_request({"code": "x", "duration": duration}).duration == duration


@pytest.mark.parametrize("duration", [2.5, 12.5, 0, -1, 7.3, 8.25])
def test_invalid_durations_are_friendly(duration: float) -> None:
    with pytest.raises(UserFacingError) as info:
        parse_request({"code": "x", "duration": duration})
    assert info.value.field == "duration"
    assert "between 3 and 12 seconds" in info.value.message


def test_duration_is_required() -> None:
    with pytest.raises(UserFacingError) as info:
        parse_request({"code": "x"})
    assert info.value.field == "duration"
    assert "required" in info.value.message


@pytest.mark.parametrize(
    ("field", "value", "snippet"),
    [
        ("fps", 45, "30 or 60"),
        ("aspect", "4:3", "9:16, 1:1, 16:9"),
        ("style", "spin", "typewriter"),
        ("filename", "", "1 to 40 characters"),
        ("bogus", 1, "Unknown setting"),
    ],
)
def test_field_errors_are_friendly(field: str, value: object, snippet: str) -> None:
    with pytest.raises(UserFacingError) as info:
        parse_request({"code": "x", "duration": 8, field: value})
    assert info.value.field == field
    assert snippet in info.value.message


def test_text_fields_have_no_length_limit() -> None:
    long = "x" * 5_000
    req = parse_request({"code": "x", "duration": 8, "title": long, "caption": long,
                         "output_text": long})  # fmt: skip
    assert req.title == req.caption == req.output_text == long
    assert TextElement.model_validate({"id": "t", "text": long}).text == long


def test_user_facing_error_to_dict() -> None:
    assert UserFacingError("Nope.", "duration").to_dict() == {"error": "Nope.", "field": "duration"}


def test_request_round_trips_through_json() -> None:
    req = parse_request({"code": "x", "duration": 5.5, "aspect": "16:9", "fps": 60})
    assert RenderRequest.model_validate_json(req.model_dump_json()) == req
