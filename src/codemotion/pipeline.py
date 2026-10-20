"""Pure front half of the pipeline (SPEC 6.1).

Two entry points:

- ``prepare_scene(doc)`` - a video script's ``SceneDoc``: validate, analyze each code
  element, lay it out and resolve reveal timing. This is what the renderer draws.
- ``prepare(request)`` + ``plan_for`` - a plain code file shown as a code card; the
  showcase builder (``script/showcase.py``) turns that plan into a ``SceneDoc``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from codemotion import config
from codemotion.analysis.normalize import normalize
from codemotion.analysis.ranges import parse_line_ranges
from codemotion.analysis.tokenize import Line, tokenize
from codemotion.analysis.validate import check_syntax
from codemotion.errors import UserFacingError
from codemotion.geometry import Rect
from codemotion.layout.layout import Layout, compute_layout, safe_rect
from codemotion.layout.terminal import TerminalLayout, layout_terminal
from codemotion.models import (
    CodeWarning,
    ElementPlan,
    OutputMode,
    Plan,
    RenderRequest,
    ScenePlan,
)
from codemotion.render.canvas import decode_image
from codemotion.script.doc import (
    AnyElement,
    CodeElement,
    GroupElement,
    Highlight,
    ImageElement,
    SceneDoc,
    TerminalElement,
    walk,
)
from codemotion.themes.loader import Theme, load_theme
from codemotion.timeline.planner import cascade_timing, make_plan

# --------------------------------------------------------------------------- code files


@dataclass(frozen=True)
class Prepared:
    request: RenderRequest
    code: str  # normalized
    lines: list[Line]
    code_warnings: list[CodeWarning]
    layout: Layout
    theme: Theme
    fps: int
    total_frames: int

    @property
    def warnings(self) -> list[str]:
        return [w.message for w in self.code_warnings] + list(self.layout.warnings)

    @property
    def typeable_chars(self) -> int:
        """Characters a typewriter types: everything except leading indentation."""
        return typeable_chars(self.layout)


def typeable_chars(layout: Layout) -> int:
    return sum(len(line.text.lstrip()) for line in layout.lines)


def plan_for(prepared: Prepared) -> Plan:
    req = prepared.request
    return make_plan(
        duration=req.duration,
        fps=prepared.fps,
        style=req.style,
        chars=prepared.typeable_chars,
        lines=len(prepared.layout.lines),
        focus=req.focus is not None,
        output=req.output_mode is not OutputMode.off,
        font_size=prepared.layout.font_size,
        scroll=prepared.layout.scroll,
        warnings=prepared.layout.warnings,
    )


def prepare(request: RenderRequest) -> Prepared:
    code = normalize(request.code)
    lines = tokenize(code)
    layout = compute_layout(lines, request.aspect, request.preview)
    theme = load_theme(request.theme)
    fps = config.PREVIEW_FPS if request.preview else request.fps
    return Prepared(
        request=request,
        code=code,
        lines=lines,
        code_warnings=check_syntax(code),
        layout=layout,
        theme=theme,
        fps=fps,
        total_frames=round(request.duration * fps),
    )


# --------------------------------------------------------------------------- scene documents


@dataclass(frozen=True)
class PreparedCode:
    element: CodeElement
    code: str  # normalized
    layout: Layout
    style: str  # effective reveal style
    reveal_start: float  # seconds
    reveal_length: float  # seconds
    highlights: tuple[tuple[frozenset[int], Highlight], ...]


@dataclass(frozen=True)
class PreparedScene:
    doc: SceneDoc
    theme: Theme
    fps: int
    total_frames: int
    width: int
    height: int
    unit: float  # px per design px
    preview: bool
    codes: dict[str, PreparedCode] = field(default_factory=dict)
    images: dict[str, npt.NDArray[np.uint8]] = field(default_factory=dict)
    terminals: dict[str, TerminalLayout] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def resolve_reveal(
    element: CodeElement, chars: int, lines: int, duration: float
) -> tuple[str, float, float, list[str]]:
    """(style, start, length, warnings) for a code element's reveal, in seconds."""
    start = element.start if element.reveal_start is None else element.reveal_start
    limit = (element.end if element.end is not None else duration) - start
    style = element.reveal
    warnings: list[str] = []
    if style == "none":
        return style, start, 0.0, warnings
    if limit <= 0:
        warnings.append(f"'{element.id}' reveal starts too late; the code is shown at once.")
        return "none", start, 0.0, warnings
    wanted = element.reveal_length
    if wanted is not None and wanted > limit:
        warnings.append(f"'{element.id}' reveal runs past the end; it was shortened.")
    if style == "typewriter":
        length = min(limit, wanted if wanted is not None else chars / config.TYPEWRITER_DEFAULT_CPS)
        length = max(length, min(limit, config.REVEAL_MIN_SECONDS))
        if chars / length > config.TYPEWRITER_MAX_CPS:
            warnings.append(
                f"'{element.id}': too much code to type in {length:.1f}s; "
                "using line cascade instead."
            )
            style, wanted = "line_cascade", length
    if style == "line_cascade":
        window = min(limit, wanted if wanted is not None else limit)
        stagger, line_seconds = cascade_timing(window, lines)
        return style, start, min(window, stagger * (max(1, lines) - 1) + line_seconds), warnings
    if style == "fade_in":
        return (
            style,
            start,
            min(limit, wanted if wanted is not None else config.FADE_IN_SECONDS),
            warnings,
        )
    return style, start, length, warnings


def _region(element: CodeElement, doc: SceneDoc, preview: bool) -> Rect:
    if element.region is None:
        return safe_rect(doc.aspect, preview)
    width, height = config.frame_size(doc.aspect.value, preview)
    x0, y0, x1, y1 = element.region
    return Rect(x0 * width, y0 * height, (x1 - x0) * width, (y1 - y0) * height)


def _prepare_code(
    element: CodeElement, doc: SceneDoc, preview: bool, label: str
) -> tuple[PreparedCode, list[str]]:
    try:
        code = normalize(element.code)
        layout = compute_layout(tokenize(code), doc.aspect, preview, _region(element, doc, preview))
        highlights = tuple(
            (parse_line_ranges(h.lines, len(layout.lines), "Highlight lines"), h)
            for h in element.highlights
        )
    except UserFacingError as exc:
        raise UserFacingError(f"{label}{exc.message}", exc.field) from None
    style, start, length, warnings = resolve_reveal(
        element, typeable_chars(layout), len(layout.lines), doc.duration
    )
    notes = [w.message for w in check_syntax(code)] + list(layout.warnings)
    warnings = [f"{label}{w}" for w in notes] + warnings
    return PreparedCode(element, code, layout, style, start, length, highlights), warnings


def _load_image(element: ImageElement) -> npt.NDArray[np.uint8]:
    try:
        return decode_image(Path(element.path))
    except ValueError:
        raise UserFacingError(
            f"Image '{element.id}': '{element.path}' could not be read as PNG or JPEG.", "image"
        ) from None


def prepare_scene(doc: SceneDoc, preview: bool = False) -> PreparedScene:
    """Validate a scene document against its content and resolve layout and timing."""
    theme = load_theme(doc.theme)
    fps = config.PREVIEW_FPS if preview else doc.fps
    width, height = config.frame_size(doc.aspect.value, preview)
    every = list(walk(doc.elements))
    code_elements = [e for e in every if isinstance(e, CodeElement)]
    warnings: list[str] = []
    codes: dict[str, PreparedCode] = {}
    for code in code_elements:
        label = f"Code '{code.id}': " if len(code_elements) > 1 else ""
        codes[code.id], notes = _prepare_code(code, doc, preview, label)
        warnings += notes
    images = {e.id: _load_image(e) for e in every if isinstance(e, ImageElement)}
    terminals: dict[str, TerminalLayout] = {}
    for element in every:
        if isinstance(element, TerminalElement):
            terminals[element.id], notes = layout_terminal(
                element, doc.aspect, doc.duration, preview
            )
            warnings += notes
    for element in every:
        if element.start >= doc.duration:
            warnings.append(f"'{element.id}' starts after the video ends, so it never shows.")
        late = [a for a in element.anims if a.start >= doc.duration]
        if late:
            warnings.append(f"'{element.id}' has animations that start after the video ends.")
    return PreparedScene(
        doc=doc,
        theme=theme,
        fps=fps,
        total_frames=round(doc.duration * fps),
        width=width,
        height=height,
        unit=min(width, height) / config.REFERENCE_SHORT_EDGE,
        preview=preview,
        codes=codes,
        images=images,
        terminals=terminals,
        warnings=tuple(dict.fromkeys(warnings)),  # de-duplicated, in order
    )


def scene_plan(prepared: PreparedScene, extra_warnings: list[str] | None = None) -> ScenePlan:
    """Summary of the resolved scene: element timings, code layout and all warnings."""
    doc = prepared.doc
    elements: list[ElementPlan] = []

    def add(items: list[AnyElement], parent: str | None) -> None:
        for element in items:
            plan = ElementPlan(
                id=element.id,
                type=element.type,
                start=element.start,
                end=element.end if element.end is not None else doc.duration,
                animations=len(element.anims),
                parent=parent,
            )
            code = prepared.codes.get(element.id)
            if code is not None:
                plan.reveal = code.style
                plan.reveal_start = round(code.reveal_start, 3)
                plan.reveal_end = round(code.reveal_start + code.reveal_length, 3)
                plan.font_size = round(code.layout.font_size / prepared.unit, 2)
                plan.scroll = code.layout.scroll
            terminal = prepared.terminals.get(element.id)
            if terminal is not None:
                plan.reveal = "terminal"
                plan.reveal_start = round(terminal.reveal_start, 3)
                plan.reveal_end = round(terminal.end, 3)
                plan.font_size = round(terminal.font_size / prepared.unit, 2)
            elements.append(plan)
            if isinstance(element, GroupElement):
                add(element.children, element.id)

    add(doc.elements, None)
    warnings = list(dict.fromkeys([*(extra_warnings or []), *prepared.warnings]))
    return ScenePlan(
        duration=doc.duration,
        fps=prepared.fps,
        total_frames=prepared.total_frames,
        width=prepared.width,
        height=prepared.height,
        elements=elements,
        warnings=warnings,
    )
