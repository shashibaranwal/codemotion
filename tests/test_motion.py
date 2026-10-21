"""M5 motion breadth: groups, lines, outlines, terminal, stagger, scroll, zoom, themes, export."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pytest
from conftest import EXAMPLES_DIR, read_source
from test_elements import bright, center_of, frame_at, scene
from typer.testing import CliRunner

from codemotion import (
    Code,
    Group,
    Line,
    ScriptError,
    Shape,
    Terminal,
    Text,
    Video,
    config,
    stagger,
)
from codemotion.cli import app
from codemotion.errors import UserFacingError
from codemotion.geometry import Rect
from codemotion.layout.terminal import layout_terminal
from codemotion.models import Aspect, parse_request
from codemotion.pipeline import prepare_scene, scene_plan
from codemotion.render.encoder import RenderCancelledError, export_command, export_video
from codemotion.render.renderer import CancelToken, render_video
from codemotion.scenes.code_block import Scroller, follow, scroll_target
from codemotion.scenes.compose import build_scene
from codemotion.script import api
from codemotion.script.doc import MAX_ELEMENTS, SceneDoc, TerminalElement, walk
from codemotion.script.showcase import showcase_doc
from codemotion.themes.loader import list_themes, load_theme

runner = CliRunner()


def video_doc(build: object) -> SceneDoc:
    """Run ``build(video)`` against a fresh 1:1 video and return its document."""
    api._registry.clear()
    video = Video(duration=4, aspect="1:1", background="#000000", fade_in=0)
    build(video)  # type: ignore[operator]
    return video.to_doc()


# --------------------------------------------------------------------------- geometry


def test_rect_union_and_transformed() -> None:
    assert Rect(0, 0, 10, 10).union(Rect(5, -5, 10, 10)) == Rect(0, -5, 15, 15)
    box = Rect(-10, -5, 20, 10).transformed(100, 50, 2.0, 90)
    assert box.x == pytest.approx(90) and box.y == pytest.approx(30)
    assert box.w == pytest.approx(20) and box.h == pytest.approx(40)


# --------------------------------------------------------------------------- API


def test_shape_outlines_and_validation() -> None:
    doc = video_doc(lambda v: v.add(Shape.rect(100, 50, color=None, stroke="#FF0000")))
    shape = doc.elements[0]
    assert shape.type == "shape" and shape.color is None and shape.stroke == "#FF0000"
    with pytest.raises(ScriptError, match="fill colour, a stroke colour, or both"):
        Shape.circle(10, color=None)
    with pytest.raises(ScriptError, match="not a colour"):
        Shape.rect(10, 10, stroke="nope")


def test_line_anchors_at_its_midpoint_and_draws_on() -> None:
    line = Line((0.1, 0.2), (0.5, 0.6), width=6, arrow=True).draw_on(1.0, 0.5)
    doc = video_doc(lambda v: v.add(line))
    element = doc.elements[0]
    assert (element.x, element.y) == pytest.approx((0.3, 0.4))
    assert (element.x1, element.y2, element.arrow) == (0.1, 0.6, True)
    assert element.anims[0].prop == "reveal" and element.anims[0].from_ == 0.0
    with pytest.raises(ScriptError, match="pair of fractions"):
        Line((0.1,), (0.5, 0.6))  # type: ignore[arg-type]


def test_group_children_get_ids_and_nest() -> None:
    def build(v: Video) -> None:
        group = v.add(Group(Text("a"), at=(0.3, 0.3)))
        inner = group.add(Group())
        inner.add(Shape.circle(5), start=1, end=2)

    doc = video_doc(build)
    assert [e.id for e in walk(doc.elements)] == ["group1", "text1", "group2", "shape1"]
    shape = doc.elements[0].children[1].children[0]
    assert (shape.start, shape.end) == (1, 2)


def test_elements_can_only_be_placed_once() -> None:
    text = Text("x")
    Group(text)
    with pytest.raises(ScriptError, match="already added"):
        Group(text)
    with pytest.raises(ScriptError, match=r"end .* must be after start"):
        Group().add(Text("y"), start=2, end=1)


def test_element_limit_counts_group_members() -> None:
    def build(v: Video) -> None:
        group = v.add(Group())
        for _ in range(MAX_ELEMENTS):
            group.add(Text("x"))

    with pytest.raises(ScriptError, match=f"at most {MAX_ELEMENTS} elements"):
        video_doc(build)


def test_stagger_with_method_names_and_functions() -> None:
    items = [Text(str(i)) for i in range(3)]
    assert stagger(items, "fade_in", start=1.0, every=0.25, length=0.2) == items
    assert [t._anims[0]["start"] for t in items] == [1.0, 1.25, 1.5]
    assert all(t._anims[0]["length"] == 0.2 for t in items)
    seen: list[float] = []
    stagger(items, lambda element, t: seen.append(t), start=2, every=0.5)
    assert seen == [2.0, 2.5, 3.0]
    with pytest.raises(ScriptError, match="has no animation 'wiggle'"):
        stagger(items, "wiggle")
    with pytest.raises(ScriptError, match="has no animation"):
        stagger(items, "_to_doc")


def test_highlight_zoom_option() -> None:
    code = Code("x = 1\ny = 2").highlight("2", start=1, zoom=1.2).highlight("1", start=2, zoom=1)
    assert [h["zoom"] for h in code._highlights] == [1.2, 1]
    with pytest.raises(ScriptError, match="zoom"):
        Code("x = 1").highlight("1", zoom=0.5)


def test_terminal_options_are_validated() -> None:
    Terminal("ok", command=None, prompt=">>>")
    with pytest.raises(ScriptError, match="type_speed"):
        Terminal("ok", type_speed=0)


# --------------------------------------------------------------------------- terminal layout


def _terminal(**fields: object) -> TerminalElement:
    return TerminalElement.model_validate({"id": "t", **fields})


def test_terminal_schedule_types_the_command_then_prints() -> None:
    element = _terminal(output="a\nb\n\n", command="run", start=1.0, type_speed=10,
                        line_interval=0.1)  # fmt: skip
    lay, warnings = layout_terminal(element, Aspect.landscape, 8)
    assert warnings == []
    assert lay.output == ("a", "b")  # trailing blank lines dropped
    assert lay.reveal_start == pytest.approx(1.3)
    assert lay.type_end == pytest.approx(1.6)
    assert lay.line_times == pytest.approx((1.95, 2.05))
    assert lay.end == pytest.approx(2.15)
    assert [lay.typed(t) for t in (1.0, 1.45, 1.7)] == [0, 1, 3]
    assert lay.line_count == 4


def test_terminal_without_command_prints_from_the_start() -> None:
    lay, _ = layout_terminal(_terminal(output="x", command=None, reveal_start=0.5),
                             Aspect.portrait, 8)  # fmt: skip
    assert lay.line_times == (0.5,) and lay.line_count == 1


def test_terminal_shrinks_then_wraps_long_lines() -> None:
    lay, warnings = layout_terminal(_terminal(output="x" * 70, width=1000), Aspect.landscape, 8)
    assert lay.font_size < 28 and "smaller" in warnings[0]
    assert [len(text) for text, _ in lay.output_rows] == [70]
    lay, warnings = layout_terminal(_terminal(output="y" * 300, width=600), Aspect.landscape, 8)
    assert "wrap" in warnings[0]
    assert lay.font_size == pytest.approx(28 * 0.6)
    texts = [text for text, _ in lay.output_rows]
    assert "".join(texts) == "y" * 300 and max(map(len, texts)) == lay.columns
    assert len({at for _, at in lay.output_rows}) == 1  # one line: its rows print together
    with pytest.raises(UserFacingError, match="too narrow"):
        layout_terminal(_terminal(output="x", width=150), Aspect.landscape, 8)


def test_terminal_scrolls_once_full() -> None:
    element = _terminal(output="\n".join(f"line {i}" for i in range(500)), line_interval=0.01)
    lay, warnings = layout_terminal(element, Aspect.portrait, 8)
    assert any("scroll up" in w for w in warnings)
    assert lay.visible_rows < lay.line_count == 502
    assert lay.height <= config.frame_size("9:16")[1] * (1 - 0.12 - 0.18) + 1e-6
    assert lay.first_row(0.0) == 0
    assert lay.first_row(lay.end) == lay.line_count - lay.visible_rows
    _, warnings = layout_terminal(_terminal(output="x\n" * 20, line_interval=1), Aspect.portrait, 8)
    assert "still printing" in warnings[0]


def test_terminal_long_command_wraps_and_types_across_rows() -> None:
    command = "python -m pip install " + " ".join(f"package{i}" for i in range(12))
    lay, _ = layout_terminal(_terminal(command=command, output="ok", width=700), Aspect.portrait, 8)
    assert lay.command_rows == (len(command) + 2) // lay.columns + 1 > 1
    doc = scene({"id": "t", "type": "terminal", "command": command, "output": "ok", "width": 700,
                 "type_speed": 400}, aspect="9:16")  # fmt: skip
    # Once typed, the command's last row has text on it.
    prepared = prepare_scene(doc, preview=False)
    term = prepared.terminals["t"]
    frame = frame_at(doc, term.type_end + 0.05)
    row_y = (prepared.height - term.height) / 2 + term.title_height + term.padding
    row_y += (term.command_rows - 1 + 0.5) * term.line_height
    band = frame[round(row_y * 0.5) - 3 : round(row_y * 0.5) + 3]  # stills are 540p previews
    assert (band.max(axis=2) > 150).sum() > 20


# --------------------------------------------------------------------------- rendering


def test_outline_shape_leaves_its_middle_empty() -> None:
    doc = scene({"id": "r", "type": "shape", "shape": "rect", "width": 400, "height": 400,
                 "color": None, "stroke": "#FFFFFF", "stroke_width": 10})  # fmt: skip
    px = frame_at(doc, 1)
    assert bright(px[270:271, 270:271]).sum() == 0  # middle
    assert bright(px[270:271, 170:174]).any()  # left edge: 540/2 - 400*0.5/2 = 170


def test_line_draws_up_to_its_reveal() -> None:
    line = {"id": "l", "type": "line", "x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5,
            "width": 10, "color": "#FFFFFF",
            "anims": [{"prop": "reveal", "start": 1, "length": 2, "from": 0, "to": 1,
                       "ease": "linear"}]}  # fmt: skip
    row = 270
    assert not bright(frame_at(scene(line), 0.5)[row - 2 : row + 3]).any()
    half = bright(frame_at(scene(line), 2.0)[row - 2 : row + 3]).any(axis=0)
    xs = np.nonzero(half)[0]
    assert xs.min() < 60 and 260 < xs.max() < 290  # half of 54..486
    full = bright(frame_at(scene(line), 3.5)[row - 2 : row + 3]).any(axis=0)
    assert np.nonzero(full)[0].max() > 480


def test_arrowhead_is_wider_than_the_line() -> None:
    line = {"id": "l", "type": "line", "x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5,
            "width": 8, "color": "#FFFFFF", "arrow": True}  # fmt: skip
    px = bright(frame_at(scene(line), 1))
    assert px[:, 476].sum() > 1.8 * px[:, 300].sum()  # inside the head vs mid-line
    assert not px[:, 490:].any()  # nothing past the tip


def test_group_moves_and_scales_its_children_about_its_anchor() -> None:
    dot = {"id": "d", "type": "shape", "shape": "circle", "width": 40, "height": 40,
           "color": "#FFFFFF", "x": 0.75, "y": 0.5}  # fmt: skip
    group = {"id": "g", "type": "group", "x": 0.5, "y": 0.5, "children": [dot],
             "anims": [{"prop": "scale", "start": 0, "length": 0, "to": 0.5}]}  # fmt: skip
    cx, cy = center_of(bright(frame_at(scene(group), 1)))
    assert (cx, cy) == pytest.approx((270 + 135 / 2, 270), abs=1.5)  # halfway to the anchor
    group["anims"] = [{"prop": "ox", "start": 0, "length": 0, "to": -200}]
    cx, _ = center_of(bright(frame_at(scene(group), 1)))
    assert cx == pytest.approx(405 - 100, abs=1.5)  # 200 design px = 100 px at 540p


def test_translucent_group_fades_as_one_object() -> None:
    """Two overlapping squares: a translucent group shows only the top one where they
    overlap; per-element opacity would let the bottom one show through."""
    red = {"id": "r", "type": "shape", "shape": "rect", "width": 300, "height": 300,
           "color": "#FF0000", "x": 0.45, "y": 0.5}  # fmt: skip
    blue = dict(red, id="b", color="#0000FF", x=0.55)
    group = {"id": "g", "type": "group", "opacity": 0.5, "children": [red, blue]}
    overlap = frame_at(scene(group), 1)[270, 270]
    assert overlap[0] < 10 and overlap[2] == pytest.approx(128, abs=4)
    separate = frame_at(scene(dict(red, opacity=0.5), dict(blue, opacity=0.5)), 1)[270, 270]
    assert separate[0] > 30  # red shows through without a group


def test_group_lifetime_hides_children() -> None:
    dot = {"id": "d", "type": "shape", "shape": "circle", "width": 60, "height": 60,
           "color": "#FFFFFF"}  # fmt: skip
    group = {"id": "g", "type": "group", "start": 1, "end": 2, "children": [dot]}
    assert not bright(frame_at(scene(group), 0.5)).any()
    assert bright(frame_at(scene(group), 1.5)).any()
    assert not bright(frame_at(scene(group), 2.5)).any()


def test_code_shadow_follows_its_group() -> None:
    code = {"id": "c", "type": "code", "code": "x = 1", "reveal": "none"}
    group = {"id": "g", "type": "group", "children": [code],
             "anims": [{"prop": "ox", "start": 0, "length": 0, "to": 300},
                       {"prop": "opacity", "start": 0, "length": 0, "to": 0.5}]}  # fmt: skip
    prepared = prepare_scene(scene(group), preview=True)
    background = build_scene(prepared).nodes[0]
    shadow = background.shadows[0]  # type: ignore[attr-defined]
    x, _, s, opacity = shadow.state(5)
    card = prepared.codes["c"].layout.card_rect
    assert x == pytest.approx(card.x + card.w / 2 + 150) and s == 1 and opacity == 0.5


def test_terminal_prints_output_over_time() -> None:
    terminal = {"id": "t", "type": "terminal", "output": "HELLO\nWORLD", "command": "go",
                "reveal_start": 0.5, "type_speed": 20, "line_interval": 0.5}  # fmt: skip
    doc = scene(terminal, background="#FFFFFF")
    lay = prepare_scene(doc, preview=True).terminals["t"]
    early, late = frame_at(doc, 0.6), frame_at(doc, lay.end + 0.1)
    # Output rows are lighter than the panel once printed.
    assert (late.max(axis=2) > 150).sum() > (early.max(axis=2) > 150).sum() + 50


def test_plan_lists_group_members_and_terminals() -> None:
    terminal = {"id": "t", "type": "terminal", "output": "x"}
    group = {"id": "g", "type": "group", "children": [terminal]}
    plan = scene_plan(prepare_scene(scene(group), preview=True))
    assert [(e.id, e.parent) for e in plan.elements] == [("g", None), ("t", "g")]
    assert plan.elements[1].reveal == "terminal"


# --------------------------------------------------------------------------- scroll & zoom


def test_follow_settles_without_overshoot() -> None:
    values = follow([0.0] * 5 + [100.0] * 60, fps=30)
    assert values[:5] == [0.0] * 5
    assert max(values) <= 100.0 and values[-1] == pytest.approx(100.0, abs=0.5)
    assert all(b >= a for a, b in itertools.pairwise(values))
    assert Scroller().offset(10) == 0.0 and Scroller([1.0, 2.0]).offset(99) == 2.0


def test_long_code_scrolls_to_the_end_and_to_highlights() -> None:
    code = read_source(EXAMPLES_DIR / "long_script.py")
    prepared = prepare_scene(showcase_doc(parse_request({"code": code, "duration": 12}))[0], True)
    lay = next(iter(prepared.codes.values())).layout
    assert lay.scroll
    most = lay.content_height - lay.code_rect.h
    assert scroll_target(lay, 54, 54) == pytest.approx(most)
    assert scroll_target(lay, 0, 0) == 0.0
    middle = scroll_target(lay, 25, 28, center=True)
    assert 0 < middle < most and middle % lay.line_height == pytest.approx(0, abs=1e-6)
    block = build_scene(prepared).nodes[1].drawable.parts[1]  # type: ignore[attr-defined]
    assert block.scroller.offset(prepared.total_frames - 1) == pytest.approx(most, abs=1)
    assert block.scroller.offset(0) == 0.0


def test_thousands_of_lines_scroll_to_the_end() -> None:
    source = "\n".join(f"total_{i} = compute({i}, step={i % 7})" for i in range(3_000))
    code = {"id": "c", "type": "code", "code": source, "reveal": "line_cascade",
            "highlights": [{"lines": "2990-2995", "start": 3.5, "fade": 0.1}]}  # fmt: skip
    prepared = prepare_scene(scene(code, duration=6), preview=True)
    lay = prepared.codes["c"].layout
    assert lay.scroll and len(lay.lines) == 3_000 and lay.gutter_digits == 4
    block = build_scene(prepared).nodes[1].drawable.parts[1]  # type: ignore[attr-defined]
    centred = scroll_target(lay, 2989, 2994, center=True)
    assert block.scroller.offset(prepared.total_frames - 1) == pytest.approx(centred, abs=1)
    # The last frame shows text (the lines near the end), not an empty card.
    code_area = frame_at(scene(code, duration=6), 5.9)[
        round(lay.code_rect.y) : round(lay.code_rect.bottom),
        round(lay.code_rect.x) : round(lay.code_rect.right),
    ]
    assert bright(code_area).sum() > 500


def test_wrapped_line_is_typed_and_highlighted_as_one_line() -> None:
    long_line = "total = combine(" + ", ".join(f"value_{i}" for i in range(12)) + ")"
    highlight = {"lines": "2", "start": 3.2, "fade": 0.2}
    code = {"id": "c", "type": "code", "code": f"a = 1\n{long_line}\nb = 2", "reveal": "typewriter",
            "reveal_length": 3, "highlights": [highlight]}  # fmt: skip
    prepared = prepare_scene(scene(code, aspect="9:16"), preview=True)
    lay = prepared.codes["c"].layout
    first, last = lay.rows_of(2)
    assert last > first
    drawable = build_scene(prepared).nodes[1].drawable  # type: ignore[attr-defined]
    _, _, pivot_y = drawable.zoom(round(3.6 * prepared.fps))
    middle = (lay.lines[first].top + lay.lines[last].top + lay.line_height) / 2
    assert pivot_y == pytest.approx(middle)
    reveal = drawable.parts[1].reveal
    end = round(3.1 * prepared.fps)
    assert all(reveal.line_view(end, i).columns is None for i in range(len(lay.lines)))


def test_highlight_zoom_scales_the_card_about_the_lines() -> None:
    code = {"id": "c", "type": "code", "code": "a = 1\nb = 2\nc = 3", "reveal": "none",
            "highlights": [{"lines": "3", "start": 1, "fade": 0.2, "zoom": 1.1}]}  # fmt: skip
    prepared = prepare_scene(scene(code), preview=True)
    drawable = build_scene(prepared).nodes[1].drawable  # type: ignore[attr-defined]
    assert drawable.zoom(0)[0] == 1.0
    factor, _, pivot_y = drawable.zoom(round(1.5 * prepared.fps))
    lay = prepared.codes["c"].layout
    assert factor == pytest.approx(1.1)
    assert pivot_y == pytest.approx(lay.lines[2].top + lay.line_height / 2)

    def card_width(doc: SceneDoc) -> int:
        row = frame_at(doc, 2)[round(pivot_y)]
        columns = np.nonzero(row.max(axis=1) > 25)[0]  # card fill vs the black backdrop
        return int(columns.max() - columns.min())

    plain = card_width(scene(dict(code, highlights=[])))
    assert card_width(scene(code)) == pytest.approx(plain * 1.1, rel=0.02)


# --------------------------------------------------------------------------- themes


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hex(color: object) -> str:
    r, g, b = color[:3]  # type: ignore[index]
    return f"#{r:02X}{g:02X}{b:02X}"


def test_four_bundled_themes_two_dark_one_light_one_high_contrast() -> None:
    ids = {theme.id for theme in list_themes()}
    assert {"midnight", "slate", "daylight", "contrast"} <= ids
    light = [t.id for t in list_themes() if _luminance(_hex(t.card.fill)) > 0.5]
    assert light == ["daylight"]


@pytest.mark.parametrize("name", ["midnight", "slate", "daylight", "contrast"])
def test_theme_text_is_readable(name: str) -> None:
    theme = load_theme(name)
    card = _hex(theme.card.fill)
    minimum = 7.0 if name == "contrast" else 4.5
    assert _contrast(_hex(theme.text.default), card) >= minimum
    for kind, color in theme.tokens.items():
        floor = minimum if name == "contrast" else 3.0  # comments may be quieter
        assert _contrast(_hex(color), card) >= floor, kind
    assert _contrast(_hex(theme.terminal.text), _hex(theme.terminal.fill)) >= 4.5


# --------------------------------------------------------------------------- export


@pytest.fixture(scope="module")
def short_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    prepared = prepare_scene(showcase_doc(parse_request({"code": "x = 1", "duration": 3,
                                                        "aspect": "16:9"}))[0], True)  # fmt: skip
    return render_video(prepared, tmp_path_factory.mktemp("export") / "clip.mp4")


def test_export_command_scales_gifs_to_the_short_edge(tmp_path: Path) -> None:
    command = export_command("ffmpeg", tmp_path / "a.mp4", tmp_path / "a.gif", "gif", (1920, 1080))
    graph = command[command.index("-filter_complex") + 1]
    assert "scale=960:540" in graph and "fps=15" in graph and "palettegen" in graph
    webm = export_command("ffmpeg", tmp_path / "a.mp4", tmp_path / "a.webm", "webm", (1080, 1920))
    assert "libvpx-vp9" in webm


@pytest.mark.parametrize("fmt", ["gif", "webm"])
def test_export_writes_the_format(short_mp4: Path, fmt: str) -> None:
    seen: list[float] = []
    out = export_video(short_mp4, fmt, (960, 540), 3.0, seen.append)  # type: ignore[arg-type]
    assert out == short_mp4.with_suffix(f".{fmt}") and out.stat().st_size > 1000
    frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(out))
    assert seconds == pytest.approx(3.0, abs=0.2)
    assert frames == 45  # 3 s from a 15 fps preview source
    assert seen[-1] == 1.0
    out.unlink()


def test_export_cancel_leaves_nothing(short_mp4: Path) -> None:
    with pytest.raises(RenderCancelledError):
        export_video(short_mp4, "webm", (960, 540), 3.0, cancelled=lambda: True)
    assert not list(short_mp4.parent.glob("*.webm"))
    assert not list(short_mp4.parent.glob(".*partial*"))


def test_render_video_exports_and_records_them(tmp_path: Path) -> None:
    prepared = prepare_scene(showcase_doc(parse_request({"code": "x = 1", "duration": 3}))[0], True)
    stages: list[str] = []
    video = render_video(prepared, tmp_path / "v.mp4", lambda p: stages.append(p.stage),
                         formats=["gif", "webm", "gif"])  # fmt: skip
    assert (tmp_path / "v.gif").is_file() and (tmp_path / "v.webm").is_file()
    sidecar = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["exports"] == ["v.gif", "v.webm"]
    assert stages[0] == "frames" and stages[-1] == "webm" and "gif" in stages


def test_cancel_during_export_removes_everything(tmp_path: Path) -> None:
    prepared = prepare_scene(showcase_doc(parse_request({"code": "x = 1", "duration": 3}))[0], True)
    token = CancelToken()

    def progress(p: object) -> None:
        if getattr(p, "stage", "") == "gif":
            token.cancel()

    with pytest.raises(RenderCancelledError):
        render_video(prepared, tmp_path / "v.mp4", progress, token, formats=["gif"])
    assert list(tmp_path.iterdir()) == []


def test_cli_render_gif_and_plan_indents_groups(tmp_path: Path) -> None:
    script = tmp_path / "video.py"
    script.write_text(
        "from codemotion import Group, Text, Video\n"
        "video = Video(duration=3)\n"
        "video.add(Group(Text('hi')))\n",
        encoding="utf-8",
    )
    out = tmp_path / "v.mp4"
    result = runner.invoke(app, ["render", str(script), "--preview", "--gif", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.with_suffix(".gif").is_file()
    plan = runner.invoke(app, ["plan", str(script)])
    assert "  text1" in plan.output
