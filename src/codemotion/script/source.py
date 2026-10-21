"""Turn source text into a scene document: run it as a script, or show it as a code card.

A third kind, video programs (Pillow/OpenCV scripts that write their own video), has no
scene document: ``script.program`` runs them.

Shared by the CLI and the desktop app, so both treat a file the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from codemotion.errors import UserFacingError
from codemotion.models import parse_request
from codemotion.script.doc import SceneDoc
from codemotion.script.program import is_program
from codemotion.script.runner import is_script, run_script, run_script_source
from codemotion.script.showcase import showcase_doc


class SourceMode(StrEnum):
    auto = "auto"  # scripts import codemotion; programs write a video; the rest is code
    script = "script"
    program = "program"
    code = "code"


class SourceKind(StrEnum):
    script = "script"  # a CodeMotion video script: describes a scene
    program = "program"  # a video program: renders and writes its own video
    code = "code"  # anything else: shown as a code card


PROGRAM_NEEDS_RENDER = (
    "This is a video program: it draws its own frames and writes a video. "
    "Render it to run it and get its video."
)


@dataclass(frozen=True)
class Loaded:
    doc: SceneDoc
    warnings: list[str]
    is_script: bool
    output: str = ""  # what a script printed


def source_kind(text: str, mode: SourceMode = SourceMode.auto) -> SourceKind:
    if mode is not SourceMode.auto:
        return SourceKind(mode.value)
    if is_script(text):
        return SourceKind.script
    return SourceKind.program if is_program(text) else SourceKind.code


def treat_as_script(text: str, mode: SourceMode = SourceMode.auto) -> bool:
    return source_kind(text, mode) is SourceKind.script


def load_text(
    text: str,
    base_dir: Path,
    mode: SourceMode = SourceMode.auto,
    code_options: dict[str, Any] | None = None,
    script_path: Path | None = None,
) -> Loaded:
    """Build the scene document for ``text``.

    Scripts run in a child process with ``base_dir`` as the working directory (or from
    ``script_path`` when the text is exactly that file's contents). Code files become the
    code-card scene; ``code_options`` are its settings and must include ``duration``.
    Video programs have no scene document (``UserFacingError``): see ``script.program``.
    """
    kind = source_kind(text, mode)
    if kind is SourceKind.program:
        raise UserFacingError(PROGRAM_NEEDS_RENDER, field="script")
    if kind is SourceKind.script:
        if script_path is not None:
            result = run_script(script_path)
        else:
            result = run_script_source(text, base_dir)
        return Loaded(result.doc, [], True, result.output)
    request = parse_request({"code": text, **(code_options or {})})
    doc, warnings = showcase_doc(request)
    return Loaded(doc, warnings, False)
