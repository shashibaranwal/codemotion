"""``CodeMotion.exe --self-test REPORT``: check a packaged app works, without a screen.

Opens the main window offscreen, runs the starter script (a child process), draws a
preview still and renders a quick preview video (worker processes, ffmpeg). Writes one
line per step to ``REPORT`` and returns 0 only if every step passed.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from codemotion import __version__


def self_test(report: Path, timeout: float = 120.0) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QSettings

    from codemotion.desktop.app import create_app
    from codemotion.desktop.main_window import MainWindow

    lines = [f"CodeMotion {__version__} self-test"]
    ok = True
    app = create_app([])

    def wait(condition: Callable[[], bool]) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if condition():
                return True
        return False

    def step(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        lines.append(f"{'PASS' if passed else 'FAIL'} {name}{': ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory(prefix="codemotion-selftest-") as tmp:
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("output_dir", str(Path(tmp) / "renders"))
        window = MainWindow(settings)
        try:
            ran = wait(lambda: window.result is not None or bool(window.problems))
            detail = window.problems[0].message if window.problems else ""
            step("run the starter script", ran and window.result is not None, detail)
            step("draw a preview still", wait(lambda: window.preview.frame.image is not None))
            rendered: list[Path] = []
            failed: list[str] = []
            window.renderer.finished.connect(lambda outcome: rendered.append(outcome[1]))
            window.renderer.failed.connect(lambda outcome: failed.append(str(outcome[1])))
            window.render_script(preview=True)
            wait(lambda: bool(rendered or failed))
            size = rendered[0].stat().st_size if rendered else 0
            detail = failed[0] if failed else f"{size} bytes"
            step("render a quick preview", size > 0, detail)
        finally:
            window.editor.document().setModified(False)
            window.close()
    lines.append("RESULT " + ("PASS" if ok else "FAIL"))
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1
