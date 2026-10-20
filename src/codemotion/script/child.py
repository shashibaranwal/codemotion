"""Subprocess entry point: run one video script and write its scene document as JSON.

Usage: ``python -m codemotion.script.child SCRIPT OUT_JSON``. The parent (``runner.py``)
sets the working directory to the script's folder and enforces a timeout. Output is
``{"doc": {...}}`` on success or ``{"error": ..., "line": ..., "source_line": ...}``.
"""

from __future__ import annotations

import json
import random
import runpy
import sys
import traceback
from pathlib import Path
from typing import Any


def _script_frame(exc: BaseException, script: Path) -> traceback.FrameSummary | None:
    """The innermost traceback frame inside the user's script (where to point them)."""
    frames = traceback.extract_tb(exc.__traceback__)
    inside = [f for f in frames if Path(f.filename).resolve() == script]
    return inside[-1] if inside else None


def _describe(exc: BaseException, script: Path) -> dict[str, Any]:
    from codemotion.script.api import ScriptError

    if isinstance(exc, SyntaxError) and exc.filename and Path(exc.filename).resolve() == script:
        return {
            "error": f"Syntax error: {exc.msg}",
            "line": exc.lineno,
            "source_line": (exc.text or "").rstrip() or None,
        }
    name = "" if isinstance(exc, ScriptError) else f"{type(exc).__name__}: "
    frame = _script_frame(exc, script)
    return {
        "error": f"{name}{exc}",
        "line": frame.lineno if frame else None,
        "source_line": (frame.line or None) if frame else None,
    }


def run(script: Path, out: Path) -> int:
    from codemotion.script import api

    api._registry.clear()
    random.seed(0)  # scripts that use `random` stay deterministic
    sys.argv = [str(script)]
    sys.path[:0] = [str(script.parent), str(Path.cwd())]
    result: dict[str, Any]
    try:
        runpy.run_path(str(script), run_name="__main__")
        if not api._registry:
            raise api.ScriptError(
                "The script didn't create a video. Start with: video = Video(duration=8)"
            )
        if len(api._registry) > 1:
            raise api.ScriptError(
                f"The script created {len(api._registry)} videos; create only one."
            )
        doc = api._registry[0].to_doc()
        result = {"doc": doc.model_dump(mode="json", by_alias=True)}
    except SystemExit as exc:
        result = {"error": f"The script called exit({exc.code}).", "line": None}
    except BaseException as exc:  # report anything the script raises
        result = _describe(exc, script)
    out.write_text(json.dumps(result), encoding="utf-8")
    return 0 if "doc" in result else 1


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1]).resolve(), Path(sys.argv[2])))
