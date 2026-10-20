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
SELF_TEST_FLAG = "--self-test"


def _run_child_if_asked() -> None:
    """``codemotion-cli.exe --codemotion-script-child SCRIPT OUT``: run a video script."""
    if len(sys.argv) == 4 and sys.argv[1] == CHILD_FLAG:
        from codemotion.script.child import run

        sys.exit(run(Path(sys.argv[2]).resolve(), Path(sys.argv[3])))


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
