"""Build a ``Scene`` from a prepared scene document.

This is the only module that turns times into frames for nodes: it compiles element
animations into tracks and code reveals into schedules.
"""

from __future__ import annotations

import hashlib

from codemotion import config
from codemotion.geometry import Color
from codemotion.pipeline import PreparedCode, PreparedScene
from codemotion.scenes.background import Background, ShadowSpec
from codemotion.scenes.base import FadeOverlay, Node, Scene, SceneContext
from codemotion.scenes.code_block import (
    CascadeReveal,
    CodeBlock,
    FadeReveal,
    Reveal,
    Revealed,
    Scroller,
    Spotlight,
    TypewriterReveal,
    build_typing_schedule,
    follow,
    scroll_target,
)
from codemotion.scenes.cursor import Cursor
from codemotion.scenes.elements import (
    Animated,
    CodeDrawable,
    Drawable,
    ElementNode,
    GroupNode,
    ImageDrawable,
    LineDrawable,
    Pivot,
    SceneNode,
    ShapeDrawable,
    TextDrawable,
    Zoom,
)
from codemotion.scenes.terminal import TerminalDrawable
from codemotion.scenes.window import Window
from codemotion.script.doc import (
    AnyElement,
    CodeElement,
    GroupElement,
    ImageElement,
    LineElement,
    SceneDoc,
    ShapeElement,
    TerminalElement,
    TextElement,
)
from codemotion.timeline.easing import ease_in_out_cubic, ease_out_cubic
from codemotion.timeline.keyframes import Keyframe, Track
from codemotion.timeline.planner import cascade_timing

BLACK = Color(0, 0, 0)


def seed_for(text: str) -> int:
    """Deterministic seed derived from the input (SPEC N4)."""
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _reveal(code: PreparedCode, fps: int, seed: int) -> Reveal:
    start, length = code.reveal_start * fps, code.reveal_length * fps
    lay = code.layout
    if code.style == "typewriter":
        lines = [line.text for line in lay.lines]
        return TypewriterReveal(build_typing_schedule(lines, start, start + length, fps, seed))
    if code.style == "line_cascade":
        stagger, line_seconds = cascade_timing(code.reveal_length, len(lay.lines))
        return CascadeReveal(
            start, stagger * fps, line_seconds * fps, config.CASCADE_OFFSET_Y * lay.unit
        )
    if code.style == "fade_in":
        return FadeReveal(Track.of((start, 0.0), (start + max(1.0, length), 1.0, ease_out_cubic)))
    return Revealed()


def _spotlights(code: PreparedCode, fps: int, end_of_video: float) -> list[Spotlight]:
    result = []
    for lines, h in code.highlights:
        fade = h.fade * fps
        start = h.start * fps
        keys = [Keyframe(start, 0.0), Keyframe(start + fade, 1.0, ease_in_out_cubic)]
        if h.end is not None and h.end * fps < end_of_video:
            end = max(h.end * fps, start + fade)
            keys += [Keyframe(end, 1.0), Keyframe(end + fade, 0.0, ease_in_out_cubic)]
        result.append(Spotlight(lines, Track(tuple(keys))))
    return result


def _revealing_line(reveal: Reveal, frame: int, count: int) -> int:
    """Index of the line being revealed at ``frame`` (0 before the reveal starts)."""
    if isinstance(reveal, TypewriterReveal):
        cursor = reveal.cursor(frame)
        return cursor[0] if cursor is not None else 0
    if isinstance(reveal, CascadeReveal) and frame >= reveal.start:
        return min(count - 1, int((frame - reveal.start) / max(reveal.stagger, 1e-9)))
    return 0


def _scroller(
    code: PreparedCode, reveal: Reveal, spotlights: list[Spotlight], total: int, fps: int
) -> Scroller | None:
    """Camera scroll for code taller than its card: follow the line being revealed, or
    centre the strongest active highlight."""
    lay = code.layout
    if not lay.scroll:
        return None
    targets = []
    for frame in range(total):
        amounts = [(a, spot) for spot in spotlights if (a := spot.amount.at(frame)) > 0]
        if amounts:
            spot = max(amounts, key=lambda pair: pair[0])[1]
            first, last = lay.rows_of(min(spot.lines))[0], lay.rows_of(max(spot.lines))[1]
            targets.append(scroll_target(lay, first, last, True))
        else:
            line = _revealing_line(reveal, frame, len(lay.lines))
            targets.append(scroll_target(lay, line, line))
    return Scroller(follow(targets, fps))


def _zoom(code: PreparedCode, spotlights: list[Spotlight], scroller: Scroller) -> Zoom | None:
    """Highlight zoom: the card scales about the middle of the strongest highlight."""
    zooms = [
        (spot, h.zoom)
        for spot, (_, h) in zip(spotlights, code.highlights, strict=True)
        if h.zoom > 1
    ]
    if not zooms:
        return None
    lay = code.layout
    pivot_x = lay.card_rect.x + lay.card_rect.w / 2

    def zoom(frame: int) -> tuple[float, float, float]:
        best, pivot_y = 1.0, 0.0
        for spot, factor in zooms:
            value = 1 + (factor - 1) * spot.amount.at(frame)
            if value > best:
                first, last = lay.rows_of(min(spot.lines))[0], lay.rows_of(max(spot.lines))[1]
                top, bottom = lay.lines[first].top, lay.lines[last].top + lay.line_height
                pivot_y = (top + bottom) / 2 - scroller.offset(frame)
                best = value
        pivot_y = min(max(pivot_y, lay.code_rect.y), lay.code_rect.bottom)
        return best, pivot_x, pivot_y

    return zoom


def _code_drawable(prepared: PreparedScene, element: CodeElement, seed: int) -> CodeDrawable:
    theme, fps = prepared.theme, prepared.fps
    code = prepared.codes[element.id]
    reveal = _reveal(code, fps, seed)
    spotlights = _spotlights(code, fps, prepared.total_frames)
    scroller = _scroller(code, reveal, spotlights, prepared.total_frames, fps) or Scroller()
    parts: list[Node] = [
        Window(theme, code.layout, element.filename),
        CodeBlock(theme, code.layout, reveal, spotlights, scroller),
    ]
    if isinstance(reveal, TypewriterReveal):
        parts.append(Cursor(theme, code.layout, reveal, scroller))
    max_zoom = max((h.zoom for _, h in code.highlights), default=1.0)
    return CodeDrawable(code.layout, parts, _zoom(code, spotlights, scroller), max_zoom)


def _drawable(
    prepared: PreparedScene, element: AnyElement, seed: int, ctx: SceneContext
) -> Drawable:
    theme, unit = prepared.theme, prepared.unit
    if isinstance(element, TextElement):
        return TextDrawable(element, theme, unit)
    if isinstance(element, ShapeElement):
        return ShapeDrawable(element, theme, unit)
    if isinstance(element, ImageElement):
        return ImageDrawable(prepared.images[element.id], element.width, unit)
    if isinstance(element, LineElement):
        return LineDrawable(element, theme, ctx)
    if isinstance(element, TerminalElement):
        return TerminalDrawable(prepared.terminals[element.id], theme, element.title)
    assert isinstance(element, CodeElement)
    return _code_drawable(prepared, element, seed)


def _base(prepared: PreparedScene, element: AnyElement) -> dict[str, float]:
    x, y = element.x, element.y
    if isinstance(element, CodeElement) and (x is None or y is None):
        card = prepared.codes[element.id].layout.card_rect
        x = (card.x + card.w / 2) / prepared.width if x is None else x
        y = (card.y + card.h / 2) / prepared.height if y is None else y
    if isinstance(element, LineElement):
        x = (element.x1 + element.x2) / 2 if x is None else x
        y = (element.y1 + element.y2) / 2 if y is None else y
    return {
        "x": 0.5 if x is None else x,
        "y": 0.5 if y is None else y,
        "ox": 0.0,
        "oy": 0.0,
        "opacity": element.opacity,
        "scale": element.scale,
        "rotation": element.rotation,
        "reveal": 1.0,
    }


def _build(
    prepared: PreparedScene,
    elements: list[AnyElement],
    ctx: SceneContext,
    seed: int,
    parent: Pivot | None,
    shadows: list[ShadowSpec],
) -> list[SceneNode]:
    nodes: list[SceneNode] = []
    for element in elements:
        base = _base(prepared, element)
        animated = Animated(base, element.anims, element.start, element.end, ctx, parent)
        if isinstance(element, GroupElement):
            anchor = (base["x"] * ctx.width, base["y"] * ctx.height)
            group = GroupNode(animated, anchor)
            pivot = Pivot(animated, anchor)
            group.children = _build(prepared, element.children, ctx, seed, pivot, shadows)
            nodes.append(group)
            continue
        nodes.append(ElementNode(animated, _drawable(prepared, element, seed, ctx)))
        if isinstance(element, CodeElement):
            card = prepared.codes[element.id].layout.card_rect
            shadows.append(
                ShadowSpec(
                    width=card.w,
                    height=card.h,
                    blur=config.CARD_SHADOW_BLUR * prepared.unit,
                    dy=config.CARD_SHADOW_DY * prepared.unit,
                    color=prepared.theme.card.shadow,
                    state=animated.shadow_state,
                )
            )
    return nodes


def _doc_seed(doc: SceneDoc) -> int:
    # Defaults left out, so adding a schema field with a default keeps existing seeds.
    return seed_for(doc.model_dump_json(exclude_defaults=True))


def build_scene(prepared: PreparedScene) -> Scene:
    doc, theme, fps = prepared.doc, prepared.theme, prepared.fps
    ctx = SceneContext(prepared.width, prepared.height, prepared.unit, fps, prepared.total_frames)
    seed = _doc_seed(doc)
    shadows: list[ShadowSpec] = []
    element_nodes: list[Node] = list(_build(prepared, doc.elements, ctx, seed, None, shadows))

    if doc.fade_in > 0:
        bg_opacity = Track.of((0, 0.0), (doc.fade_in * fps, 1.0, ease_out_cubic))
    else:
        bg_opacity = Track.constant(1.0)
    solid = None if doc.background == "theme" else Color.from_hex(doc.background)
    nodes: list[Node] = [Background(theme, seed, bg_opacity, shadows, solid), *element_nodes]
    if doc.fade_out > 0:
        end = prepared.total_frames - 1
        nodes.append(FadeOverlay(BLACK, Track.of((end - doc.fade_out * fps, 0.0), (end, 1.0))))
    return Scene(ctx=ctx, clear_color=BLACK, nodes=nodes)
