"""Desktop app (M4): workers, editor, preview and the main window, on offscreen Qt."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import EXAMPLES_DIR
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from codemotion.desktop import dialogs, main_window
from codemotion.desktop.app import dark_palette
from codemotion.desktop.dialogs import CodeSettingsDialog, default_code_options
from codemotion.desktop.editor import CodeEditor
from codemotion.desktop.main_window import MainWindow, list_renders
from codemotion.desktop.workers import (
    LatestWins,
    RenderJob,
    RenderWorker,
    RunRequest,
    run_source,
    still_image,
)
from codemotion.errors import UserFacingError
from codemotion.script.runner import ScriptRunError

WAIT = 30_000  # ms; script runs start a Python process

SCRIPT = """from codemotion import Shape, Text, Video

video = Video(duration={duration}, aspect="1:1")
title = video.add(Text("Hello", 64, at=(0.5, 0.3)))
title.fade_in(0.2)
dot = video.add(Shape.circle(40, at=(0.5, 0.7)), start=0.5)
dot.pop_in(start=0.5)
"""
BROKEN = 'from codemotion import Text, Video\n\nvideo = Video(duration=4)\nTxt("oops")\n'


def script(duration: float = 3) -> str:
    return SCRIPT.format(duration=duration)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(qtbot: QtBot, settings: QSettings, tmp_path: Path) -> Iterator[MainWindow]:
    settings.setValue("output_dir", str(tmp_path / "renders"))
    win = MainWindow(settings)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: win.result is not None, timeout=WAIT)  # the starter script
    yield win
    win.editor.document().setModified(False)  # no "save changes?" prompt on close
    win.close()


def set_script(qtbot: QtBot, win: MainWindow, text: str) -> None:
    """Replace the editor text and run it, waiting for the run that matches the text."""
    win.editor.setPlainText(text)
    win.run()
    qtbot.waitUntil(
        lambda: (win.result is not None and win.result.request.text == text and
                 not win.runner.busy) or (bool(win.problems) and not win.runner.busy and
                 win.problems[0].severity == "error"),
        timeout=WAIT,
    )  # fmt: skip


# --------------------------------------------------------------------------- workers


def test_latest_wins_runs_current_and_latest_job(qtbot: QtBot) -> None:
    worker = LatestWins()
    release = threading.Event()
    results: list[object] = []
    worker.finished.connect(results.append)

    def first() -> int:
        release.wait(5)
        return 1

    worker.submit(first)
    worker.submit(lambda: 2)  # replaced by 3 before it starts
    worker.submit(lambda: 3)
    assert worker.busy
    release.set()
    qtbot.waitUntil(lambda: len(results) == 2, timeout=5000)
    assert results == [1, 3]
    assert not worker.busy


def test_latest_wins_reports_errors(qtbot: QtBot) -> None:
    worker = LatestWins()
    with qtbot.waitSignal(worker.failed, timeout=5000) as blocker:
        worker.submit(lambda: 1 / 0)
    assert isinstance(blocker.args[0], ZeroDivisionError)


def test_run_source_prepares_a_preview_and_plan(tmp_path: Path) -> None:
    result = run_source(RunRequest(script(), tmp_path))
    assert result.loaded.is_script
    assert result.prepared.preview
    assert [e.id for e in result.plan.elements] == ["text1", "shape1"]
    image = still_image(result.stills, 1.0)
    assert (image.width(), image.height()) == (result.prepared.width, result.prepared.height)


def test_run_source_shows_a_code_file_as_a_code_card(tmp_path: Path) -> None:
    request = RunRequest("x = 1\n", tmp_path, code_options={"duration": 4})
    result = run_source(request)
    assert not result.loaded.is_script
    assert [e.type for e in result.plan.elements] == ["code"]


def test_run_source_reports_script_errors_with_the_line(tmp_path: Path) -> None:
    with pytest.raises(ScriptRunError) as info:
        run_source(RunRequest(BROKEN, tmp_path))
    assert info.value.line == 4
    assert "Txt" in info.value.message


def _render_job(tmp_path: Path, duration: float = 3) -> RenderJob:
    doc = run_source(RunRequest(script(duration), tmp_path)).loaded.doc
    return RenderJob(doc, tmp_path / "out.mp4", preview=True)


def test_render_worker_reports_progress_and_the_video(qtbot: QtBot, tmp_path: Path) -> None:
    worker = RenderWorker()
    progress: list[float] = []
    worker.progress.connect(lambda p: progress.append(p.fraction))
    job = _render_job(tmp_path)
    with qtbot.waitSignal(worker.finished, timeout=WAIT) as blocker:
        worker.start(job)
    done_job, video = blocker.args[0]
    assert done_job is job and video == tmp_path / "out.mp4" and video.is_file()
    assert video.with_suffix(".json").is_file()
    assert progress and progress[-1] == 1.0
    assert not worker.busy


def test_render_worker_cancel_leaves_no_file(qtbot: QtBot, tmp_path: Path) -> None:
    worker = RenderWorker()
    job = _render_job(tmp_path, duration=12)
    with qtbot.waitSignal(worker.cancelled, timeout=WAIT):
        worker.start(job)
        worker.cancel()
    assert list(tmp_path.glob("*.mp4")) == []


def test_render_worker_runs_one_render_at_a_time(qtbot: QtBot, tmp_path: Path) -> None:
    worker = RenderWorker()
    job = _render_job(tmp_path)
    with qtbot.waitSignal(worker.finished, timeout=WAIT):
        worker.start(job)
        with pytest.raises(RuntimeError):
            worker.start(job)


# --------------------------------------------------------------------------- editor


def _formats(doc: QTextDocument, line: int) -> dict[int, str]:
    """Foreground colour (#rrggbb) of each formatted position on ``line`` (0-based)."""
    block = doc.findBlockByNumber(line)
    colors: dict[int, str] = {}
    for fmt in block.layout().formats():
        for pos in range(fmt.start, fmt.start + fmt.length):
            colors[pos] = fmt.format.foreground().color().name()
    return colors


def test_highlighter_colours_keywords_strings_and_comments(qtbot: QtBot) -> None:
    from codemotion import config

    editor = CodeEditor()
    qtbot.addWidget(editor)
    doc = editor.document()
    editor.setPlainText('def f(x):  # note "x"\n    return "a # b"\n')
    colors = config.EDITOR_COLORS
    first, second = _formats(doc, 0), _formats(doc, 1)
    assert first[0] == colors["keyword"].lower()  # def
    assert first[4] == colors["definition"].lower()  # f
    assert first[11] == colors["comment"].lower()  # the whole comment, quotes included
    assert first[19] == colors["comment"].lower()
    assert second[4] == colors["keyword"].lower()  # return
    assert second[13] == colors["string"].lower()  # '#' inside a string stays a string


def test_highlighter_tracks_triple_quoted_strings_across_lines(qtbot: QtBot) -> None:
    from codemotion import config

    editor = CodeEditor()
    qtbot.addWidget(editor)
    doc = editor.document()
    editor.setPlainText('x = """start\nif inside\nend""" if y else 2\n')
    string = config.EDITOR_COLORS["string"].lower()
    assert _formats(doc, 1)[0] == string  # "if" inside the string is not a keyword
    third = _formats(doc, 2)
    assert third[0] == string
    assert third[7] == config.EDITOR_COLORS["keyword"].lower()  # if, after the string


def test_editor_marks_and_clears_the_error_line(qtbot: QtBot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("a\nb\nc\n")
    editor.set_error_line(2)
    assert editor.error_line == 2
    assert len(editor.extraSelections()) == 2  # current line + error line
    editor.set_error_line(99)  # out of range: ignored
    assert editor.error_line is None
    assert len(editor.extraSelections()) == 1


def test_editor_go_to_line(qtbot: QtBot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("a\nb\nc\nd\n")
    editor.go_to_line(3)
    assert editor.textCursor().blockNumber() == 2


def test_editor_indents_with_spaces_and_after_colons(qtbot: QtBot) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.show()
    qtbot.keyClicks(editor, "for i in x:")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.keyClicks(editor, "pass")
    assert editor.toPlainText() == "for i in x:\n    pass"
    qtbot.keyClick(editor, Qt.Key.Key_Backtab)
    assert editor.toPlainText() == "for i in x:\npass"
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText().endswith("pass    ")


def test_editor_hands_dropped_files_to_the_window(qtbot: QtBot, tmp_path: Path) -> None:
    editor = CodeEditor()
    qtbot.addWidget(editor)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "a.py"))])
    with qtbot.waitSignal(editor.files_dropped) as blocker:
        editor.insertFromMimeData(mime)
    assert blocker.args[0] == [tmp_path / "a.py"]
    assert editor.toPlainText() == ""


# --------------------------------------------------------------------------- dialogs & app


def test_code_settings_dialog_round_trips_and_snaps_duration(qtbot: QtBot) -> None:
    dialog = CodeSettingsDialog({"duration": 6, "aspect": "16:9", "style": "fade_in"})
    qtbot.addWidget(dialog)
    dialog.duration.setValue(7.3)
    options = dialog.options()
    assert options == {**default_code_options(), "duration": 7.5, "aspect": "16:9",
                       "style": "fade_in"}  # fmt: skip


def test_dark_palette_uses_config_colours() -> None:
    from PySide6.QtGui import QPalette

    from codemotion import config

    palette = dark_palette()
    assert palette.color(QPalette.ColorRole.Window).name() == config.UI_COLORS["window"].lower()


# --------------------------------------------------------------------------- main window


def test_window_runs_the_starter_script_on_open(window: MainWindow, qtbot: QtBot) -> None:
    assert window.windowTitle().startswith("untitled.py")
    assert window.element_tree.topLevelItemCount() == 3
    qtbot.waitUntil(lambda: window.preview.frame.image is not None, timeout=WAIT)
    assert window.problems == []
    assert window.statusBar().currentMessage().startswith("Script ran in")


def test_window_shows_errors_on_their_line(window: MainWindow, qtbot: QtBot) -> None:
    previous = window.result
    set_script(qtbot, window, BROKEN)
    assert window.problems[0].severity == "error"
    assert window.problems[0].line == 4
    assert window.editor.error_line == 4
    assert window.result is previous  # the last good preview stays
    window.editor.go_to_line(1)
    window._on_problem_activated(window.problem_list.item(0))
    assert window.editor.textCursor().blockNumber() == 3
    set_script(qtbot, window, script())  # fixing it clears the mark
    assert window.editor.error_line is None
    assert window.problems == []


def test_window_auto_runs_after_typing_stops(window: MainWindow, qtbot: QtBot) -> None:
    window.auto_run_action.setChecked(True)
    window.editor.setPlainText(script(4))
    assert window.auto_timer.isActive()
    qtbot.waitUntil(
        lambda: window.result is not None and window.result.loaded.doc.duration == 4,
        timeout=WAIT,
    )
    window.auto_run_action.setChecked(False)
    window.editor.setPlainText(script(5))
    assert not window.auto_timer.isActive()


def test_window_scrubbing_renders_stills(window: MainWindow, qtbot: QtBot) -> None:
    set_script(qtbot, window, script())
    qtbot.waitUntil(lambda: window.preview.frame.image is not None, timeout=WAIT)
    window.preview.frame.image = None
    window.preview.set_time(0.1)
    qtbot.waitUntil(lambda: window.preview.frame.image is not None, timeout=WAIT)
    early = window.preview.frame.image
    window.preview.frame.image = None
    window.preview.set_time(2.5)
    qtbot.waitUntil(lambda: window.preview.frame.image is not None, timeout=WAIT)
    assert early is not None and window.preview.frame.image != early


def test_window_renders_and_lists_the_video(
    window: MainWindow, qtbot: QtBot, tmp_path: Path
) -> None:
    set_script(qtbot, window, script())
    with qtbot.waitSignal(window.renderer.finished, timeout=WAIT * 2):
        window.render_script()
    videos = list((tmp_path / "renders").glob("*.mp4"))
    assert len(videos) == 1 and videos[0].name.startswith("untitled-1x1-")
    assert window.render_list.count() == 1
    assert window.preview.in_player
    assert not window.progress_bar.isVisible()
    assert window.render_action.isEnabled()


def test_window_runs_changed_text_before_rendering(
    window: MainWindow, qtbot: QtBot, tmp_path: Path
) -> None:
    set_script(qtbot, window, script(3))
    window.editor.setPlainText(script(4))  # not run yet
    with qtbot.waitSignal(window.renderer.finished, timeout=WAIT * 2) as blocker:
        window.render_script(preview=True)
    job, video = blocker.args[0]
    assert job.preview and job.doc.duration == 4
    data = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["scene"]["duration"] == 4
    assert window.render_list.count() == 0  # previews aren't listed as renders


def test_window_cancel_render(window: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    set_script(qtbot, window, script(12))
    with qtbot.waitSignal(window.renderer.cancelled, timeout=WAIT):
        window.render_script()
        window.cancel_render()
    assert list((tmp_path / "renders").glob("*.mp4")) == []
    assert "cancelled" in window.statusBar().currentMessage()


def test_window_opens_a_code_file_with_settings(
    window: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def accept(dialog: CodeSettingsDialog) -> int:
        dialog.duration.setValue(5)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.CodeSettingsDialog, "exec", accept)
    assert window.open_path(EXAMPLES_DIR / "hello.py")
    qtbot.waitUntil(lambda: window.result is not None and window.path is not None, timeout=WAIT)
    assert not window.result.loaded.is_script
    assert window.result.loaded.doc.duration == 5
    assert window.windowTitle().startswith("hello.py")


def test_window_code_file_without_settings_explains_what_to_do(
    window: MainWindow, tmp_path: Path
) -> None:
    code = tmp_path / "plain.py"
    code.write_bytes(b"print('hi')\n")
    assert window.open_path(code, ask_code_settings=False)
    assert window.result is None
    assert "Code card settings" in window.problems[0].message


def test_window_saves_and_keeps_line_endings(
    window: MainWindow, qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "video.py"
    path.write_bytes(script().replace("\n", "\r\n").encode())
    window.open_path(path)
    window.editor.appendPlainText("# more")
    assert window.windowTitle().startswith("video.py*")
    assert window.save()
    assert window.windowTitle().startswith("video.py —")
    saved = path.read_bytes()
    assert saved.endswith(b"# more")
    assert b"\r\n" in saved and b"\n" not in saved.replace(b"\r\n", b"")
    assert window.recent_files()[0] == path
    qtbot.waitUntil(lambda: not window.runner.busy, timeout=WAIT)


def test_window_open_reports_unreadable_files(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(main_window.MainWindow, "_error_box", lambda self, m: shown.append(m))
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\xff\xfe\x00bad")
    assert not window.open_path(bad)
    assert "UTF-8" in shown[0]
    assert not window.open_path(tmp_path / "missing.py")
    assert "Could not open" in shown[1]


def test_window_persists_settings(window: MainWindow, settings: QSettings) -> None:
    window.auto_run_action.setChecked(False)
    window.save_settings()
    assert settings.value("auto_run") in (False, "false")
    assert settings.value("geometry") is not None


def test_list_renders_reads_sidecars_newest_first(tmp_path: Path) -> None:
    for name, aspect in (("a", "9:16"), ("b", "1:1")):
        (tmp_path / f"{name}.mp4").write_bytes(b"x")
        sidecar = {"codemotion": "0", "video": f"{name}.mp4", "preview": False,
                   "scene": {"aspect": aspect, "duration": 8}}  # fmt: skip
        (tmp_path / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    os.utime(tmp_path / "a.mp4", (1, 1))
    (tmp_path / "other.json").write_text('{"not": "ours"}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    entries = list_renders(tmp_path)
    assert [e.video.name for e in entries] == ["b.mp4", "a.mp4"]
    assert entries[0].label == "b · 1:1 · 8 s"
    assert list_renders(tmp_path / "missing") == []


def test_user_facing_errors_show_their_message(window: MainWindow) -> None:
    window._on_run_failed(UserFacingError("Plain words."))
    assert window.problems[0].message == "Plain words."
    window._on_run_failed(RuntimeError("boom"))
    assert window.problems[0].message == "Unexpected error: RuntimeError: boom"


# --------------------------------------------------------------------------- M5 additions


def test_window_nests_group_members_in_the_element_list(window: MainWindow, qtbot: QtBot) -> None:
    text = (
        "from codemotion import Group, Shape, Text, Video\n"
        "video = Video(duration=3)\n"
        "card = video.add(Group(Shape.rect(200, 100), Text('hi')))\n"
    )
    set_script(qtbot, window, text)
    assert window.element_tree.topLevelItemCount() == 1
    group = window.element_tree.topLevelItem(0)
    assert group.text(0) == "group1" and group.childCount() == 2
    assert group.child(1).text(0) == "text1"


def test_window_exports_gif_with_full_renders(
    window: MainWindow, qtbot: QtBot, tmp_path: Path
) -> None:
    set_script(qtbot, window, script())
    window.gif_action.setChecked(True)
    assert window.export_formats() == ("gif",)
    with qtbot.waitSignal(window.renderer.finished, timeout=WAIT * 2) as blocker:
        window.render_script()
    job, video = blocker.args[0]
    assert job.formats == ("gif",) and video.with_suffix(".gif").is_file()
    assert window.render_list.item(0).text().endswith("+GIF")
    window.save_settings()
    assert window.settings.value("export_gif") in (True, "true")


def test_quick_preview_never_exports(window: MainWindow, qtbot: QtBot) -> None:
    set_script(qtbot, window, script())
    window.webm_action.setChecked(True)
    with qtbot.waitSignal(window.renderer.finished, timeout=WAIT * 2) as blocker:
        window.render_script(preview=True)
    assert blocker.args[0][0].formats == ()


def test_editor_highlights_the_new_api_names(qtbot: QtBot) -> None:
    from codemotion import config

    editor = CodeEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("Group(Line(a, b), Terminal())\n")
    colors = _formats(editor.document(), 0)
    api = config.EDITOR_COLORS["definition"].lower()
    assert colors[0] == colors[6] == colors[18] == api
