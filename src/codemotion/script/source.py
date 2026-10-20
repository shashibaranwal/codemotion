"""Turn source text into a scene document: run it as a script, or show it as a code card.

Shared by the CLI and the desktop app, so both treat a file the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from codemotion.models import parse_request
from codemotion.script.doc import SceneDoc
from codemotion.script.runner import is_script, run_script, run_script_source
from codemotion.script.showcase import showcase_doc


class SourceMode(StrEnum):
    auto = "auto"  # scripts import codemotion; anything else is shown as code
    script = "script"
    code = "code"


@dataclass(frozen=True)
class Loaded:
    doc: SceneDoc
    warnings: list[str]
    is_script: bool
    output: str = ""  # what a script printed


def treat_as_script(text: str, mode: SourceMode = SourceMode.auto) -> bool:
    return mode is SourceMode.script or (mode is SourceMode.auto and is_script(text))


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
    """
    if treat_as_script(text, mode):
        if script_path is not None:
            result = run_script(script_path)
        else:
            result = run_script_source(text, base_dir)
        return Loaded(result.doc, [], True, result.output)
    request = parse_request({"code": text, **(code_options or {})})
    doc, warnings = showcase_doc(request)
    return Loaded(doc, warnings, False)
