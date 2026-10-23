"""Run a video program: a Python script that renders and writes its own video.

Video scripts (which import codemotion) describe a scene for CodeMotion to render. A video
program instead draws its frames itself, with Pillow, OpenCV, imageio and so on, and writes
a video file, like ``motion_graphic.py``. CodeMotion runs it as ``python program.py`` in a
child process, streams what it prints, finds the video it wrote and imports it (re-encoded
as an H.264 MP4) so it can be played and listed with the other renders.

Isolation, not a sandbox, exactly like ``runner.py``.
"""

from __future__ import annotations

import ast
import contextlib
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.render.encoder import (
    ExportFormat,
    RenderCancelledError,
    VideoInfo,
    export_video,
    import_video,
)
from codemotion.render.renderer import (
    CancelToken,
    Progress,
    ProgressCallback,
    write_program_sidecar,
)
from codemotion.script.runner import ScriptRunError, is_script

_VIDEO_LIBRARIES = r"(?:PIL|cv2|imageio|imageio_ffmpeg|moviepy|skvideo|av)"
_IMPORTS_VIDEO_LIBRARY = re.compile(
    rf"^\s*(?:from\s+{_VIDEO_LIBRARIES}[\w.]*\s+import|import\s+{_VIDEO_LIBRARIES}\b)", re.M
)
_FRAMES_DONE = re.compile(r"(\d+)\s*/\s*(\d+)")  # "Rendered 60/257 frames"
_TRACEBACK_LINE = re.compile(r'File "(.+?)", line (\d+)')
_SKIP_DIRS = {"__pycache__", "node_modules", "site-packages", "venv", "env"}
# SyntaxError messages that mean the text stops in the middle of a statement.
_CUT_OFF_ERRORS = ("was never closed", "unexpected EOF", "expected an indented block",
                   "unterminated triple-quoted string")  # fmt: skip
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def is_program(source: str) -> bool:
    """True if ``source`` uses an imaging/video library (and isn't a video script).

    The import alone decides: the call that writes the video usually comes last, so it is
    the part missing from a truncated paste, and libraries write videos in many ways.
    """
    return not is_script(source) and bool(_IMPORTS_VIDEO_LIBRARY.search(source))


def incomplete(source: str) -> tuple[str, int] | None:
    """(message, line) if ``source`` looks cut off, e.g. a paste that stopped early.

    Two signs: it ends in the middle of a statement, or it defines functions but never
    calls any (no ``main()`` and no ``if __name__ == "__main__":``), so running it would
    do nothing. Other syntax errors are left to the run, which reports them.
    """
    lines = source.rstrip().splitlines()
    if not lines:
        return None
    last_no, last = len(lines), lines[-1].strip()
    shown = last if len(last) <= 60 else last[:57] + "…"
    ending = f'It ends at line {last_no}: "{shown}"'
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        near_end = (exc.lineno or 0) >= last_no - 1
        if near_end and any(text in (exc.msg or "") for text in _CUT_OFF_ERRORS):
            return (f"The code stops in the middle of a statement, so it looks cut off. "
                    f"{ending}. Paste the whole script.", last_no)  # fmt: skip
        return None
    defined = {node.name for node in tree.body if isinstance(node, _DEFINITIONS)}
    if not defined:
        return None
    used = {
        node.id
        for statement in tree.body
        if not isinstance(statement, _DEFINITIONS)
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for statement in tree.body
        if not isinstance(statement, _DEFINITIONS)
        for node in ast.walk(statement)
        if isinstance(node, ast.Attribute)
    }
    if defined & used:
        return None
    return (
        "This code defines functions but never calls any of them (there's no main() call "
        'or `if __name__ == "__main__":` block), so running it would do nothing. It may be '
        f"cut off. {ending}.",
        last_no,
    )


def progress_in(line: str) -> float | None:
    """The fraction done that a line like "Rendered 60/257 frames" reports, if any."""
    matches = [(int(a), int(b)) for a, b in _FRAMES_DONE.findall(line)]
    for done, total in reversed(matches):
        if total > 0 and 0 <= done <= total:
            return done / total
    return None


@dataclass(frozen=True)
class ProgramResult:
    video: Path  # the imported MP4
    written: Path  # the file the program wrote (may be gone if it ran in a temp folder)
    info: VideoInfo
    output: str  # what the program printed (its tail)
    seconds: float  # how long the program ran
    exports: tuple[Path, ...] = ()  # GIF / WebM copies


def _same_text(a: str, b: str) -> bool:
    """Equal apart from line endings."""
    return a.replace("\r\n", "\n") == b.replace("\r\n", "\n")


def program_command(script: Path) -> list[str]:
    """The command that runs ``script`` like ``python script.py``.

    The packaged app has no Python executable; its console program runs the file instead
    (see ``launcher.py``), with the same bundled libraries.
    """
    if getattr(sys, "frozen", False):
        from codemotion.launcher import PROGRAM_FLAG

        exe = Path(sys.executable)
        name = "codemotion-cli.exe" if sys.platform == "win32" else "codemotion-cli"
        console = exe.with_name(name)
        return [str(console if console.is_file() else exe), PROGRAM_FLAG, str(script)]
    return [sys.executable, str(script)]


def video_files(folder: Path, depth: int = config.PROGRAM_SCAN_DEPTH) -> dict[Path, int]:
    """Video files under ``folder`` (``depth`` levels deep) with their modification times."""
    found: dict[Path, int] = {}
    root_depth = len(folder.parts)
    for current, dirs, files in os.walk(folder):
        here = Path(current)
        if len(here.parts) - root_depth >= depth:
            dirs.clear()
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in files:
            if name.lower().endswith(config.VIDEO_SUFFIXES) and ".partial" not in name:
                path = here / name
                try:
                    found[path] = path.stat().st_mtime_ns
                except OSError:
                    continue
    return found


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Stop the program and anything it started (e.g. an ffmpeg it pipes frames to)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)  # fmt: skip
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def _failure(output: str, script: Path, code: int) -> ScriptRunError:
    """A readable error from a failed program's output (its traceback, usually)."""
    lines = [line for line in output.splitlines() if line.strip()]
    line_no = None
    for path, number in _TRACEBACK_LINE.findall(output):
        if Path(path).name == script.name:
            line_no = int(number)
    last = lines[-1].strip() if lines else f"The program stopped with exit code {code}."
    message = f"Line {line_no}: {last}" if line_no else last
    if "ModuleNotFoundError" in last:
        message += "\nVideo programs can use Pillow, OpenCV, NumPy and the standard library."
    return ScriptRunError(message, line_no)


def run_program(
    script: Path,
    workdir: Path,
    on_output: Callable[[str], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    timeout: float = config.PROGRAM_TIMEOUT_SECONDS,
) -> tuple[Path, str, float]:
    """Run ``script`` in ``workdir``; returns (the video it wrote, its output, seconds).

    Raises ``ScriptRunError`` if it fails or times out, ``RenderCancelledError`` if
    ``cancelled()`` turns true, and ``UserFacingError`` if it wrote no video.
    """
    before = video_files(workdir)
    # The working folder goes on the path too: an edited file runs from a temp copy.
    path = os.pathsep.join(p for p in (str(workdir), os.environ.get("PYTHONPATH")) if p)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": path}  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            program_command(script),
            cwd=workdir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except OSError as exc:
        raise ScriptRunError(f"The program could not be started: {exc.strerror}.") from None
    stdout = proc.stdout
    assert stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for raw in stdout:
            lines.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        lines.put(None)

    threading.Thread(target=read, name="codemotion-program-output", daemon=True).start()
    output: list[str] = []
    size = 0
    finished = False
    while not finished:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            line = ""
        else:
            if line is None:
                finished = True
            else:
                output.append(line)
                size += len(line) + 1
                while size > config.PROGRAM_OUTPUT_LIMIT and len(output) > 1:
                    size -= len(output.pop(0)) + 1
                if on_output is not None:
                    on_output(line)
                fraction = progress_in(line)
                if fraction is not None and on_progress is not None:
                    on_progress(fraction)
        if cancelled():
            _kill(proc)
            raise RenderCancelledError
        if time.monotonic() - started > timeout:
            _kill(proc)
            raise ScriptRunError(
                f"The program took longer than {timeout / 60:g} minutes; "
                "check for endless loops or slow work."
            )
    code = proc.wait()
    seconds = time.monotonic() - started
    text = "\n".join(output)
    if code != 0:
        raise _failure(text, script, code)
    after = video_files(workdir)
    written = [path for path, mtime in after.items() if before.get(path) != mtime]
    if not written:
        kinds = ", ".join(config.VIDEO_SUFFIXES)
        raise UserFacingError(
            f"The program finished but didn't write a video ({kinds}) in {workdir}. "
            "Write it with a relative path, like 'output.mp4'.",
            field="script",
        )
    return max(written, key=lambda path: after[path]), text, seconds


@contextlib.contextmanager
def program_files(source: str, script_path: Path | None) -> Iterator[tuple[Path, Path]]:
    """(the script to run, its working folder) for the program ``source``.

    A saved file (``script_path``) runs in its own folder, so its relative paths work;
    when ``source`` differs from the file on disk, the edited text runs from a temp copy
    in that folder's place. Unsaved programs run in a temporary folder.
    """
    with tempfile.TemporaryDirectory(prefix="codemotion-program-") as tmp:
        saved = (
            script_path is not None
            and script_path.is_file()
            and _same_text(script_path.read_text(encoding="utf-8-sig"), source)
        )
        if saved and script_path is not None:
            script = script_path.resolve()
        else:
            script = Path(tmp) / (script_path.name if script_path else "program.py")
            script.write_text(source, encoding="utf-8")
        if script_path is not None:
            workdir = script_path.resolve().parent
        else:
            workdir = Path(tmp) / "work"
            workdir.mkdir()
        yield script, workdir


def stage_reporter(progress: ProgressCallback | None) -> Callable[[str], Callable[[float], None]]:
    """``report(stage)(fraction)`` sends ``Progress`` out of 1000 for that stage."""
    started = time.monotonic()

    def report(stage: str) -> Callable[[float], None]:
        def update(fraction: float) -> None:
            if progress is not None:
                progress(Progress(round(fraction * 1000), 1000, time.monotonic() - started,
                                  None, stage))  # fmt: skip

        return update

    return report


def make_video(
    source: str,
    output: Path,
    script_path: Path | None = None,
    formats: tuple[ExportFormat, ...] = (),
    on_output: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    timeout: float = config.PROGRAM_TIMEOUT_SECONDS,
) -> ProgramResult:
    """Run the program ``source``, import the video it writes as the MP4 ``output`` (with a
    sidecar, like a render) and export ``formats`` next to it. ``program_files`` says
    where it runs. ``progress`` stages: "program" (from what it prints, e.g. "60/257"),
    "import", then "gif" / "webm"; ``done`` is out of 1000.
    """
    problem = incomplete(source)
    if problem is not None:
        raise ScriptRunError(*problem)
    token = cancel or CancelToken()
    report = stage_reporter(progress)
    with program_files(source, script_path) as (script, workdir):
        written, text, seconds = run_program(
            script, workdir, on_output, report("program"), lambda: token.cancelled, timeout
        )
        info = import_video(written, output, report("import"), lambda: token.cancelled)
    exports = []
    try:
        for fmt in formats:
            exports.append(export_video(output, fmt, (info.width, info.height), info.duration,
                                        report(fmt), lambda: token.cancelled))  # fmt: skip
    except BaseException:
        for path in (output, *exports):
            path.unlink(missing_ok=True)
        raise
    source_name = str(script_path) if script_path is not None else None
    write_program_sidecar(output, info, source_name, exports)
    return ProgramResult(output, written, info, text, seconds, tuple(exports))
