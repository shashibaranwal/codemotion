"""Run a video script in a separate Python process and return its ``SceneDoc``.

Isolation, not a sandbox: the script is ordinary Python with the user's permissions (like
running ``python script.py``). The separate process keeps a crashing or hanging script
from taking CodeMotion down, enforces a timeout, and gives every run a clean state.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.script.doc import SceneDoc

_IMPORTS_CODEMOTION = re.compile(
    r"^\s*(?:from\s+codemotion[\w.]*\s+import|import\s+codemotion)\b", re.M
)
_OUTPUT_LIMIT = 4000


class ScriptRunError(UserFacingError):
    """The script raised an error; ``line`` is where (1-based), when known."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message, field="script")
        self.line = line


@dataclass(frozen=True)
class ScriptResult:
    doc: SceneDoc
    output: str  # what the script printed (truncated)


def is_script(source: str) -> bool:
    """True if ``source`` is a video script (it imports codemotion) rather than code to show."""
    return bool(_IMPORTS_CODEMOTION.search(source))


def _failure(data: dict[str, object]) -> ScriptRunError:
    line = data.get("line")
    line_no = line if isinstance(line, int) else None
    message = f"Line {line_no}: {data['error']}" if line_no else f"{data['error']}"
    source_line = data.get("source_line")
    if isinstance(source_line, str) and source_line.strip():
        message += f"\n    {source_line.strip()}"
    return ScriptRunError(message, line_no)


def run_script(path: Path, timeout: float = config.SCRIPT_TIMEOUT_SECONDS) -> ScriptResult:
    """Run the script file at ``path`` (working directory: its folder)."""
    if not path.is_file():
        raise UserFacingError(f"File not found: {path}", field="script")
    return _run(path.resolve(), path.resolve().parent, timeout)


def run_script_source(
    source: str, base_dir: Path, timeout: float = config.SCRIPT_TIMEOUT_SECONDS
) -> ScriptResult:
    """Run unsaved script text; relative paths in it resolve against ``base_dir``."""
    with tempfile.TemporaryDirectory(prefix="codemotion-") as tmp:
        script = Path(tmp) / "script.py"
        script.write_text(source, encoding="utf-8")
        return _run(script, base_dir.resolve(), timeout)


def child_command(script: Path, out: Path) -> list[str]:
    """The command that runs ``script`` in a child process and writes ``out``.

    In the packaged app there is no ``python -m``: the console executable runs the child
    (see ``launcher.py``); the windowed one has no output streams to capture.
    """
    if getattr(sys, "frozen", False):
        from codemotion.launcher import CHILD_FLAG

        exe = Path(sys.executable)
        name = "codemotion-cli.exe" if sys.platform == "win32" else "codemotion-cli"
        console = exe.with_name(name)
        program = console if console.is_file() else exe
        return [str(program), CHILD_FLAG, str(script), str(out)]
    return [sys.executable, "-m", "codemotion.script.child", str(script), str(out)]


def _run(script: Path, cwd: Path, timeout: float) -> ScriptResult:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with tempfile.TemporaryDirectory(prefix="codemotion-doc-") as tmp:
        out = Path(tmp) / "doc.json"
        try:
            proc = subprocess.run(
                child_command(script, out),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            raise ScriptRunError(
                f"The script took longer than {timeout:g} seconds to run; "
                "check for endless loops or slow work."
            ) from None
        printed = proc.stdout.decode("utf-8", "replace")[:_OUTPUT_LIMIT]
        if not out.is_file():
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
            raise ScriptRunError("The script runner failed:\n" + "\n".join(tail))
        data = json.loads(out.read_text(encoding="utf-8"))
    if "error" in data:
        raise _failure(data)
    return ScriptResult(SceneDoc.model_validate(data["doc"]), printed)
