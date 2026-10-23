"""Desktop frame review (desktop/review.py): generate, edit, approve, render, on offscreen Qt."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot
from test_desktop import WAIT, script, set_script, settings, window  # noqa: F401 (fixtures)
from test_program import program

from codemotion.desktop.main_window import MainWindow, list_renders
from codemotion.desktop.review import FrameReviewDialog
from codemotion.review import write_png


def open_review(qtbot: QtBot, win: MainWindow) -> FrameReviewDialog:
    """Review frames, and wait for the (new) review window."""
    previous = win.review_dialog
    win.review_frames()
    qtbot.waitUntil(
        lambda: win.review_dialog is not None and win.review_dialog is not previous, timeout=WAIT
    )
    assert win.review_dialog is not None
    return win.review_dialog


def answer_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    yes = QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: yes)


def drag(qtbot: QtBot, widget: object, start: QPoint, end: QPoint) -> None:
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, end)
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)


def test_review_frames_then_render_the_approved_frames(
    window: MainWindow,  # noqa: F811
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_script(qtbot, window, script())
    dialog = open_review(qtbot, window)
    session = dialog.session
    assert len(session.entries) == 90 and dialog.strip.count() == 90
    assert not dialog.render_button.isEnabled()

    def full_frame() -> bool:
        base = dialog.canvas.base
        return base is not None and base.width() == 1080

    qtbot.waitUntil(full_frame, timeout=WAIT)
    qtbot.waitUntil(lambda: dialog.thumbs.pending == 0 and len(dialog._icons) == 90, timeout=WAIT)
    dialog.approve_and_next()
    assert session.entries[0].approved and dialog.current is session.entries[1]
    assert dialog.strip.item(0).text() == "1 ✓"
    assert "1 of 90 frames approved" in dialog.summary.text()
    dialog.accept()  # not every frame is approved yet
    assert window.review_dialog is dialog
    answer_yes(monkeypatch)
    dialog.approve_all()
    assert dialog.render_button.isEnabled()
    with qtbot.waitSignal(window.renderer.finished, timeout=WAIT * 2) as blocker:
        dialog.render_button.click()
    _, video = blocker.args[0]
    assert window.review_dialog is None
    assert video.parent == window.output_dir and "reviewed" in video.name
    assert [(e.video, e.duration) for e in list_renders(window.output_dir)] == [(video, 3.0)]
    assert window.statusBar().currentMessage().startswith("Rendered 90 reviewed frames")


def test_painting_a_frame_saves_it_and_needs_a_new_look(
    window: MainWindow,  # noqa: F811
    qtbot: QtBot,
) -> None:
    set_script(qtbot, window, script())
    dialog = open_review(qtbot, window)
    session = dialog.session
    entry = session.entries[20]
    session.approve([entry.id])
    dialog.show_entry(entry)
    canvas = dialog.canvas
    centre = canvas.target().center().toPoint()
    drag(qtbot, canvas, centre, centre + QPoint(30, 10))
    assert session.is_painted(entry.id) and not entry.approved
    assert "✎" in dialog.strip.item(20).text()
    layer = session.paint(entry.id)
    assert layer is not None and layer[540, 540, 3] == 255  # in the frame's own pixels
    dialog.set_tool("rect")
    drag(qtbot, canvas, centre - QPoint(40, 40), centre - QPoint(20, 20))
    canvas.undo()
    canvas.undo()  # back to nothing painted
    assert not canvas.can_undo and not session.is_painted(entry.id)
    dialog.set_tool("text")
    canvas.ask_text = lambda: "fix"  # type: ignore[method-assign]
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=centre)
    assert session.is_painted(entry.id)
    # Closing keeps the edits; reviewing the same video again resumes.
    dialog.reject()
    assert window.review_dialog is None
    assert "Review closed" in window.statusBar().currentMessage()
    again = open_review(qtbot, window)
    assert again.session.folder == session.folder and again.session.is_painted(entry.id)


def test_paint_can_be_copied_to_the_selected_frames(
    window: MainWindow,  # noqa: F811
    qtbot: QtBot,
) -> None:
    set_script(qtbot, window, script())
    dialog = open_review(qtbot, window)
    session = dialog.session
    canvas = dialog.canvas
    canvas._push_undo()
    canvas.fill_rect(QPointF(0, 0), QPointF(50, 50))
    canvas.changed.emit()
    for row in (1, 2, 3):
        dialog.strip.item(row).setSelected(True)
    dialog.copy_paint_to_selected()
    assert [session.is_painted(e.id) for e in session.entries[:5]] == [True] * 4 + [False]


def test_a_frame_edited_in_another_app_comes_back(
    window: MainWindow,  # noqa: F811
    qtbot: QtBot,
) -> None:
    set_script(qtbot, window, script())
    dialog = open_review(qtbot, window)
    session = dialog.session
    opened: list[Path] = []
    dialog.open_editor = opened.append
    dialog.edit_externally()
    entry = dialog.current
    assert opened == [session.edit_path(entry.id)] and session.is_replaced(entry.id)
    session.approve([entry.id])
    green = np.zeros((1080, 1080, 4), np.uint8)
    green[...] = (0, 255, 0, 255)
    write_png(opened[0], green)
    os.utime(opened[0], (1e9, 1e9))  # not the time it had when it was opened
    dialog.check_external_edits()
    assert not entry.approved

    def shows_green() -> bool:
        base = dialog.canvas.base
        return base is not None and base.width() == 1080 and base.pixelColor(5, 5).green() == 255

    qtbot.waitUntil(shows_green, timeout=WAIT)
    dialog.revert()
    assert not session.is_edited(entry.id)


def test_review_a_video_program(
    window: MainWindow,  # noqa: F811
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window.editor.setPlainText(program())
    dialog = open_review(qtbot, window)  # runs the program
    assert len(dialog.session.entries) == 10 and dialog.session.size == (64, 48)
    assert "Rendered 10/10 frames" in window.output_view.toPlainText()
    dialog.duplicate()
    assert dialog.strip.count() == 11 and "⧉" in dialog.strip.item(1).text()
    assert dialog.current is dialog.session.entries[1]
    dialog.delete_selected()
    assert dialog.strip.count() == 10
    dialog.session.set_paint(dialog.current.id, np.full((48, 64, 4), 255, np.uint8))
    # Regenerating throws the review away and runs the program again.
    answer_yes(monkeypatch)
    folder = dialog.session.folder
    dialog.regenerate()

    def regenerated() -> bool:
        return window.review_dialog is not None and window.review_dialog is not dialog

    qtbot.waitUntil(regenerated, timeout=WAIT)
    fresh = window.review_dialog
    assert fresh is not None and fresh.session.folder == folder
    assert fresh.session.edited_count == 0 and len(fresh.session.entries) == 10


def test_review_reports_a_failing_program(
    window: MainWindow,  # noqa: F811
    qtbot: QtBot,
) -> None:
    window.editor.setPlainText("import cv2\n\nraise ValueError('bad')\n")
    with qtbot.waitSignal(window.renderer.failed, timeout=WAIT):
        window.review_frames()
    assert window.problems[0].line == 3 and "ValueError: bad" in window.problems[0].message
    assert window.review_dialog is None
