"""Entry points of the packaged app (PyInstaller), which has no ``python -m``.

The bundle has two executables sharing one folder:

- ``CodeMotion.exe`` (windowed): the desktop app (``gui``).
- ``codemotion-cli.exe`` (console): the CLI (``cli``); the desktop app also starts it to
  run video scripts in a child process (``CHILD_FLAG``), since a windowed program has no
  console streams to capture a script's output from. (Not ``codemotion.exe``: Windows
  file names ignore case, so it would collide with ``CodeMotion.exe``.)

Both first let multiprocessing's frozen bootstrap take over when started as a render
worker.
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

CHILD_FLAG = "--codemotion-script-child"
PROGRAM_FLAG = "--codemotion-run-program"
SELF_TEST_FLAG = "--self-test"


def run_program_file(script: Path) -> int:
    """Run a video program like ``python script.py`` (the bundle has no python.exe)."""
    import runpy
    import traceback

    for stream in (sys.stdout, sys.stderr):  # unbuffered, so progress lines stream
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.argv = [str(script)]
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


def _run_child_if_asked() -> None:
    """``codemotion-cli.exe --codemotion-script-child SCRIPT OUT``: run a video script;
    ``codemotion-cli.exe --codemotion-run-program SCRIPT``: run a video program."""
    if len(sys.argv) == 4 and sys.argv[1] == CHILD_FLAG:
        from codemotion.script.child import run

        sys.exit(run(Path(sys.argv[2]).resolve(), Path(sys.argv[3])))
    if len(sys.argv) == 3 and sys.argv[1] == PROGRAM_FLAG:
        sys.exit(run_program_file(Path(sys.argv[2]).resolve()))


def cli() -> None:
    multiprocessing.freeze_support()
    _run_child_if_asked()
    from codemotion.cli import main

    main()


def gui() -> None:
    multiprocessing.freeze_support()
    _run_child_if_asked()
    if len(sys.argv) == 3 and sys.argv[1] == SELF_TEST_FLAG:
        from codemotion.desktop.selftest import self_test

        sys.exit(self_test(Path(sys.argv[2])))
    from codemotion.desktop.app import run

    script = Path(sys.argv[1]) if len(sys.argv) > 1 and Path(sys.argv[1]).is_file() else None
    sys.exit(run(script))
