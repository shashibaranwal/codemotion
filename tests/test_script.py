from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import EXAMPLES_DIR, SCRIPTS, SCRIPTS_DIR, read_source
from pydantic import ValidationError
from typer.testing import CliRunner

from codemotion import Code, Image, ScriptError, Shape, Text, Video, config, ease
from codemotion.analysis.ranges import contiguous_runs, parse_line_ranges
from codemotion.cli import app
from codemotion.errors import UserFacingError
from codemotion.layout.layout import SCROLL_WARNING
from codemotion.models import parse_request
from codemotion.pipeline import prepare_scene, resolve_reveal, scene_plan
from codemotion.script import api
from codemotion.script.doc import Anim, CodeElement, SceneDoc, TextElement, check_ease
from codemotion.script.runner import (
    ScriptRunError,
    is_script,
    run_script,
    run_script_source,
)
from codemotion.script.showcase import showcase_doc
from codemotion.script.source import SourceMode, load_text, treat_as_script

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    api._registry.clear()


# --------------------------------------------------------------------------- API


def test_api_builds_a_scene_doc() -> None:
    video = Video(duration=8, aspect="1:1", fade_out=0.3)
    title = video.add(Text("Hi", 64, at=(0.5, 0.2), bold=True))
    assert title.fade_in(0.2).move_by(dy=-40, start=0.2) is title
    video.add(Code("print(1)", filename="a.py"), start=1.0, end=6.0).highlight("1", start=3)
    video.add(Shape.circle(30, at=(0.8, 0.8), name="dot")).pop_in(2)

    doc = video.to_doc()
    assert [e.id for e in doc.elements] == ["text1", "code1", "dot"]
    text, code, dot = doc.elements
    assert isinstance(text, TextElement) and text.font == "sans_bold" and text.y == 0.2
    assert [(a.prop, a.from_, a.to, a.by) for a in text.anims] == [
        ("opacity", 0.0, 1.0, None),
        ("oy", None, None, -40.0),
    ]
    assert isinstance(code, CodeElement)
    assert (code.start, code.end, code.x, code.filename) == (1.0, 6.0, None, "a.py")
    assert code.highlights[0].lines == "1"
    assert dot.type == "shape" and dot.width == 60
    assert doc.aspect.value == "1:1" and doc.fade_out == 0.3


def test_doc_round_trips_through_json() -> None:
    video = Video(duration=6)
    video.add(Text("x")).fade_in(1)
    doc = video.to_doc()
    data = json.loads(doc.model_dump_json(by_alias=True))
    assert data["elements"][0]["anims"][0]["from"] == 0.0
    assert SceneDoc.model_validate(data) == doc


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: Video(duration=20), "Video 'duration': must be 3-12 seconds"),
        (lambda: Video(duration=6, aspect="4:3"), "Video 'aspect'"),
        (lambda: Video(duration=6, background="blue"), "not a colour"),
        (lambda: Text("x", color="nope"), "'nope' is not a colour"),
        (lambda: Text("x", font="serif"), "font must be 'sans' or 'mono'"),
        (lambda: Text("x", at=(0.5,)), "at must be an (x, y) pair"),
        (lambda: Text("x").fade_in(ease="bouncy"), "unknown easing 'bouncy'"),
        (lambda: Text("x").fade_in(start=-1), "'start'"),
        (lambda: Text("x").slide_in("sideways"), "direction must be one of"),
        (lambda: Text("x").animate("size", to=3), "'prop'"),
        (lambda: Code("x", reveal="zap"), "Code 'reveal'"),
        (lambda: Code("x", region=(0.5, 0, 0.2, 1)), "region must be"),
        (lambda: Shape.rect(-1, 10), "Shape 'width'"),
        (lambda: Image("logo.gif"), "only PNG and JPEG"),
        (lambda: Image("missing.png"), "not found"),
        (lambda: Code.file("missing.py"), "not found"),
    ],
)
def test_api_errors_are_friendly(build: object, message: str) -> None:
    assert callable(build)
    with pytest.raises(ScriptError) as info:
        build()
    assert message in str(info.value)


def test_add_validation() -> None:
    video = Video(duration=6)
    text = Text("x")
    with pytest.raises(ScriptError, match="between 0 and 6 s"):
        video.add(text, start=6)
    with pytest.raises(ScriptError, match="must be after start"):
        video.add(text, start=2, end=1)
    video.add(text)
    with pytest.raises(ScriptError, match="already added"):
        video.add(text)
    with pytest.raises(
        ScriptError, match="takes Text, Code, Image, Shape, Line, Terminal or Group"
    ):
        video.add("hello")  # type: ignore[type-var]


def test_duplicate_names_rejected() -> None:
    video = Video(duration=6)
    video.add(Text("a", name="t"))
    video.add(Text("b", name="t"))
    with pytest.raises(ScriptError, match="two elements are named 't'"):
        video.to_doc()


def test_ease_names() -> None:
    assert ease.out_cubic == "easeOutCubic"
    assert ease.spring(200, 15) == "spring:200:15"
    assert check_ease("spring:1.5:2") == "spring:1.5:2"
    with pytest.raises(ValueError):
        check_ease("spring:0:1")


def test_code_file_and_image_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo.py").write_bytes(b"x = 1\n")
    logo = SCRIPTS_DIR / "assets" / "logo.png"
    (tmp_path / "logo.png").write_bytes(logo.read_bytes())
    video = Video(duration=6)
    video.add(Code.file("demo.py"))
    video.add(Image("logo.png", width=80))
    code, image = video.to_doc().elements
    assert isinstance(code, CodeElement) and code.code == "x = 1\n" and code.filename == "demo.py"
    assert image.type == "image" and Path(image.path).is_absolute()


def test_anim_needs_exactly_one_target() -> None:
    with pytest.raises(ValidationError):
        Anim(prop="x", start=0, length=1)
    with pytest.raises(ValidationError):
        Anim(prop="x", start=0, length=1, to=1, by=1)


# --------------------------------------------------------------------------- runner


def test_is_script_detection() -> None:
    assert is_script("from codemotion import Video\n")
    assert is_script("import codemotion\n")
    assert is_script("  from codemotion.script.api import Text\n")
    assert not is_script("print('codemotion')\n")
    assert not is_script(read_source(EXAMPLES_DIR / "fib.py"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: Path(p).name)
def test_example_scripts_run(path: Path) -> None:
    result = run_script(path)
    assert result.doc.elements
    expected = (SCROLL_WARNING,) if path.name == "long_code.py" else ()  # scrolling on purpose
    assert prepare_scene(result.doc, preview=True).warnings == expected


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "video.py"
    path.write_text("from codemotion import Video, Text\n" + body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "line", "message"),
    [
        ("video = Video(duration=6)\nvideo.add(Txt('x'))\n", 3, "NameError: name 'Txt'"),
        ("video = Video(duration=6)\nText('x', color='nope')\n", 3, "'nope' is not a colour"),
        ("video = Video(duration=6\n", 2, "Syntax error"),
        ("x = 1\n", None, "didn't create a video"),
        ("Video(duration=6)\nVideo(duration=6)\n", None, "created 2 videos"),
        ("raise SystemExit(3)\n", None, "called exit(3)"),
        ("Video(duration=6)\n1 / 0\n", 3, "ZeroDivisionError"),
    ],
)
def test_script_errors_point_at_the_line(
    tmp_path: Path, body: str, line: int | None, message: str
) -> None:
    with pytest.raises(ScriptRunError) as info:
        run_script(_script(tmp_path, body))
    assert info.value.line == line
    assert message in info.value.message
    if line:
        assert info.value.message.startswith(f"Line {line}:")


def test_script_timeout(tmp_path: Path) -> None:
    with pytest.raises(ScriptRunError, match="longer than 1 seconds"):
        run_script(_script(tmp_path, "while True:\n    pass\n"), timeout=1)


def test_script_output_and_randomness_are_captured(tmp_path: Path) -> None:
    body = (
        "import random\nvideo = Video(duration=6)\nprint('hello from the script')\n"
        "video.add(Text('x', at=(random.random(), 0.5)))\n"
    )
    first = run_script(_script(tmp_path, body))
    second = run_script(_script(tmp_path, body))
    assert "hello from the script" in first.output
    assert first.doc == second.doc  # random is seeded


def test_run_source_resolves_paths_against_base_dir(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_bytes(b"y = 2\n")
    source = (
        "from codemotion import Video, Code\nv = Video(duration=6)\nv.add(Code.file('demo.py'))\n"
    )
    doc = run_script_source(source, tmp_path).doc
    assert isinstance(doc.elements[0], CodeElement) and doc.elements[0].code == "y = 2\n"


def test_missing_script_file(tmp_path: Path) -> None:
    with pytest.raises(UserFacingError, match="File not found"):
        run_script(tmp_path / "nope.py")


# --------------------------------------------------------------------------- prepare


def _code_doc(code: str = "x = 1\ny = 2\n", **fields: object) -> SceneDoc:
    element = {"id": "c", "type": "code", "code": code, **fields}
    return SceneDoc.model_validate({"duration": 6, "elements": [element]})


def test_auto_typewriter_length_follows_default_speed() -> None:
    code = "\n".join(f"value_{i} = {i}" for i in range(6))  # 6 lines
    prepared = prepare_scene(_code_doc(code, start=1.0))
    c = prepared.codes["c"]
    chars = sum(len(line.strip()) for line in code.splitlines())
    assert c.style == "typewriter"
    assert c.reveal_start == 1.0
    assert c.reveal_length == pytest.approx(chars / config.TYPEWRITER_DEFAULT_CPS)


def test_too_much_code_switches_to_cascade() -> None:
    code = "\n".join(f"value_{i} = {i} * {i}" for i in range(30))
    prepared = prepare_scene(_code_doc(code, reveal_length=2.0))
    assert prepared.codes["c"].style == "line_cascade"
    assert any("using line cascade instead" in w for w in prepared.warnings)


@pytest.mark.parametrize(
    ("fields", "style", "length", "warning"),
    [
        ({"reveal": "none"}, "none", 0.0, None),
        ({"reveal": "fade_in"}, "fade_in", config.FADE_IN_SECONDS, None),
        ({"reveal_start": 7.0}, "none", 0.0, "starts too late"),
        ({"reveal_length": 9.0}, "typewriter", 6.0, "runs past the end"),
    ],
)
def test_reveal_resolution(
    fields: dict[str, object], style: str, length: float, warning: str | None
) -> None:
    element = CodeElement.model_validate({"id": "c", "code": "x", **fields})
    got_style, _, got_length, warnings = resolve_reveal(element, 40, 2, 6.0)
    assert (got_style, got_length) == (style, pytest.approx(length))
    assert (warning is None) == (not warnings)
    if warning:
        assert warning in warnings[0]


def test_highlight_out_of_range_names_the_element() -> None:
    doc = SceneDoc.model_validate(
        {
            "duration": 6,
            "elements": [
                {"id": "a", "type": "code", "code": "x = 1"},
                {
                    "id": "b",
                    "type": "code",
                    "code": "y = 2",
                    "highlights": [{"lines": "4", "start": 1}],
                },
            ],
        }
    )
    with pytest.raises(UserFacingError, match="Code 'b': Highlight lines '4' is out of range"):
        prepare_scene(doc)


def test_code_errors_are_labelled_only_with_several_code_elements() -> None:
    with pytest.raises(UserFacingError, match=r"^Paste some Python code first"):
        prepare_scene(_code_doc("   "))


def test_unreadable_image_is_friendly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    doc = SceneDoc.model_validate(
        {"duration": 6, "elements": [{"id": "logo", "type": "image", "path": str(bad)}]}
    )
    with pytest.raises(UserFacingError, match="Image 'logo'"):
        prepare_scene(doc)


def test_scene_plan_summarizes_elements() -> None:
    doc = SceneDoc.model_validate(
        {
            "duration": 6,
            "elements": [
                {"id": "t", "type": "text", "text": "hi", "end": 4, "anims": [
                    {"prop": "opacity", "start": 0, "length": 1, "to": 1, "from": 0},
                    {"prop": "x", "start": 7, "length": 1, "to": 0.2},
                ]},
                {"id": "c", "type": "code", "code": "x = 1", "start": 1},
            ],
        }
    )  # fmt: skip
    plan = scene_plan(prepare_scene(doc), ["extra"])
    t, c = plan.elements
    assert (t.start, t.end, t.animations, t.reveal) == (0, 4, 2, None)
    assert c.reveal == "typewriter" and c.reveal_start == 1 and c.font_size is not None
    assert plan.warnings[0] == "extra"
    assert any("start after the video ends" in w for w in plan.warnings)


# --------------------------------------------------------------------------- showcase


def test_showcase_doc_matches_the_planner() -> None:
    request = parse_request({"code": read_source(EXAMPLES_DIR / "fib.py"), "duration": 8,
                             "outro": "fade"})  # fmt: skip
    doc, warnings = showcase_doc(request)
    [code] = doc.elements
    assert isinstance(code, CodeElement)
    assert (code.reveal, code.reveal_start, code.reveal_length) == ("typewriter", 0.8, 6.2)
    assert {a.prop for a in code.anims} == {"scale", "opacity"}
    assert doc.fade_in == config.INTRO_BG_FADE_SECONDS
    assert doc.fade_out == config.OUTRO_FADE_SECONDS
    assert warnings == []


def test_showcase_reports_style_fallback() -> None:
    request = parse_request({"code": read_source(EXAMPLES_DIR / "long_script.py"), "duration": 4})
    doc, warnings = showcase_doc(request)
    assert doc.elements[0].reveal == "line_cascade"  # type: ignore[union-attr]
    assert any("line cascade" in w for w in warnings)


# --------------------------------------------------------------------------- ranges


def test_line_ranges() -> None:
    assert parse_line_ranges("3-5, 9", 10) == {3, 4, 5, 9}
    assert parse_line_ranges("5-3", 10) == {3, 4, 5}
    assert contiguous_runs(frozenset({1, 2, 3, 7, 9, 10})) == [(1, 3), (7, 7), (9, 10)]
    for bad in ("", "a", "1-", "0", "11"):
        with pytest.raises(UserFacingError):
            parse_line_ranges(bad, 10)


# --------------------------------------------------------------------------- CLI


def test_cli_renders_a_script(tmp_path: Path) -> None:
    out = tmp_path / "s.mp4"
    result = runner.invoke(
        app, ["render", str(SCRIPTS_DIR / "title_card.py"), "--preview", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "90 frames" in result.output  # 6 s at 15 fps
    assert out.is_file()


def test_cli_warns_about_ignored_options(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["still", str(SCRIPTS_DIR / "title_card.py"), "--duration", "4", "--preview",
         "-o", str(tmp_path / "f.png")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "--duration ignored" in result.output


def test_cli_script_error_exits_with_line(tmp_path: Path) -> None:
    path = _script(tmp_path, "video = Video(duration=6)\nvideo.add(Txt('x'))\n")
    result = runner.invoke(app, ["plan", str(path)])
    assert result.exit_code == 1
    assert "Line 3: NameError" in result.output


def test_cli_plan_script_json() -> None:
    result = runner.invoke(app, ["plan", str(SCRIPTS_DIR / "code_explainer.py"), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [e["type"] for e in data["plan"]["elements"]] == ["text", "code", "text"]
    assert data["scene"]["duration"] == 10


def test_cli_mode_code_shows_a_script_as_code(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "plan",
            str(SCRIPTS_DIR / "title_card.py"),
            "--mode",
            "code",
            "--duration",
            "6",
            "--aspect",
            "16:9",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("code file")


def test_cli_script_from_stdin(tmp_path: Path) -> None:
    source = "from codemotion import Video, Text\nv = Video(duration=4)\nv.add(Text('hi'))\n"
    result = runner.invoke(app, ["plan", "-"], input=source)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("script")


def test_cli_new_creates_a_runnable_starter(tmp_path: Path) -> None:
    path = tmp_path / "mine.py"
    result = runner.invoke(app, ["new", str(path)])
    assert result.exit_code == 0, result.output
    assert is_script(path.read_text(encoding="utf-8"))
    assert run_script(path).doc.elements
    again = runner.invoke(app, ["new", str(path)])
    assert again.exit_code == 1 and "already exists" in again.output
    assert runner.invoke(app, ["new", str(path), "--force"]).exit_code == 0


# --------------------------------------------------------------------------- shared loader


def test_load_text_runs_scripts_and_captures_output(tmp_path: Path) -> None:
    text = 'from codemotion import Video\nprint("hi")\nvideo = Video(duration=4)\n'
    loaded = load_text(text, tmp_path)
    assert loaded.is_script and loaded.doc.duration == 4
    assert loaded.output.strip() == "hi"


def test_load_text_builds_the_code_card(tmp_path: Path) -> None:
    loaded = load_text("x = 1\n", tmp_path, code_options={"duration": 5, "aspect": "1:1"})
    assert not loaded.is_script
    assert loaded.doc.aspect.value == "1:1" and loaded.doc.elements[0].type == "code"
    with pytest.raises(UserFacingError, match="Duration is required"):
        load_text("x = 1\n", tmp_path)


def test_load_text_modes_override_detection(tmp_path: Path) -> None:
    script_text = "from codemotion import Video\nvideo = Video(duration=4)\n"
    assert treat_as_script(script_text)
    assert not treat_as_script(script_text, SourceMode.code)
    assert treat_as_script("x = 1", SourceMode.script)
    loaded = load_text(script_text, tmp_path, SourceMode.code, {"duration": 4})
    assert not loaded.is_script  # shown as code, not run
