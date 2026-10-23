"""Frame review window: check every frame, fix the ones that are wrong, approve them all.

Step 2 of Code → Review frames → Render video. The large view shows one frame; paint on it
(brush, rectangle, text, eraser), replace it with an image, edit it in another app, hold it
for an extra frame or drop it. The filmstrip below shows every frame with its state:
✓ approved, ✎ edited, ⧉ held. Render video is enabled once every frame is approved.
Edits are saved as they happen (``codemotion.review``), so closing keeps them.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
from PySide6.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codemotion import config
from codemotion.desktop.workers import LatestWins
from codemotion.errors import UserFacingError
from codemotion.review import FrameEntry, FrameSession, Image

REGENERATE = 2  # dialog result: discard the review and generate the frames again
_ID_ROLE = Qt.ItemDataRole.UserRole
TOOLS = (("brush", "Brush"), ("rect", "Rectangle"), ("text", "Text"), ("eraser", "Eraser"))


def to_qimage(image: Image) -> QImage:
    height, width = image.shape[:2]
    data = np.ascontiguousarray(image).tobytes()
    return QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def from_qimage(image: QImage) -> Image:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    rows = np.frombuffer(rgba.constBits(), np.uint8).reshape(height, rgba.bytesPerLine())
    return rows[:, : width * 4].reshape(height, width, 4).copy()


def open_in_editor(path: Path) -> None:
    """Open ``path`` in the system's image editor (Windows: its "Edit" app, e.g. Paint)."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            os.startfile(str(path), "edit")
            return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class PaintCanvas(QWidget):
    """A frame with a transparent paint layer over it, drawn at the frame's own size."""

    changed = Signal()  # a stroke, shape or text was added to the layer, or undone

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.frame_size = QSize(0, 0)
        self.base: QImage | None = None
        self.layer: QImage | None = None
        self.tool = "brush"
        self.color = QColor(config.REVIEW_PAINT_COLOR)
        self.pen_size = config.REVIEW_PAINT_SIZE
        self._undo: list[QImage] = []
        self._last: QPointF | None = None
        self._drag: tuple[QPointF, QPointF] | None = None
        self.setMinimumSize(QSize(240, 240))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_frame(self, size: QSize, layer: QImage | None) -> None:
        """Show a new frame (its picture arrives with ``set_base``) and its paint layer."""
        self.frame_size = size
        self.base = None
        if layer is None:
            layer = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            layer.fill(Qt.GlobalColor.transparent)
        self.layer = layer.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._undo.clear()
        self._last = self._drag = None
        self.update()

    def set_base(self, image: QImage | None) -> None:
        self.base = image
        self.update()

    def layer_image(self) -> Image | None:
        """The paint layer as RGBA pixels (None if nothing is painted)."""
        if self.layer is None:
            return None
        layer = from_qimage(self.layer)
        return layer if layer[..., 3].any() else None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> None:
        if self._undo:
            self.layer = self._undo.pop()
            self.update()
            self.changed.emit()

    def clear(self) -> None:
        if self.layer is not None and self.layer_image() is not None:
            self._push_undo()
            self.layer.fill(Qt.GlobalColor.transparent)
            self.update()
            self.changed.emit()

    # -- geometry --------------------------------------------------------------------------

    def target(self) -> QRectF:
        """Where the frame is drawn in the widget (scaled to fit, centred)."""
        if self.frame_size.isEmpty():
            return QRectF()
        size = self.frame_size.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())  # fmt: skip

    def to_frame(self, pos: QPointF) -> QPointF:
        rect = self.target()
        if rect.isEmpty():
            return QPointF()
        scale = self.frame_size.width() / rect.width()
        return QPointF((pos.x() - rect.x()) * scale, (pos.y() - rect.y()) * scale)

    # -- drawing ---------------------------------------------------------------------------

    def _push_undo(self) -> None:
        if self.layer is not None:
            self._undo.append(self.layer.copy())
            del self._undo[: -config.REVIEW_UNDO_STEPS]

    def _painter(self) -> QPainter:
        assert self.layer is not None
        painter = QPainter(self.layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.tool == "eraser":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        pen = QPen(self.color, self.pen_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)  # fmt: skip
        painter.setPen(pen)
        return painter

    def stroke(self, start: QPointF, end: QPointF) -> None:
        """Paint (or erase) a line in frame coordinates."""
        painter = self._painter()
        if start == end:
            painter.drawPoint(start)
        else:
            painter.drawLine(start, end)
        painter.end()
        self.update()

    def fill_rect(self, a: QPointF, b: QPointF) -> None:
        painter = self._painter()
        painter.fillRect(QRectF(a, b).normalized(), self.color)
        painter.end()
        self.update()

    def draw_text(self, at: QPointF, text: str) -> None:
        painter = self._painter()
        font = QFont(painter.font())
        font.setPixelSize(max(8, self.pen_size * 4))
        painter.setFont(font)
        painter.drawText(at, text)
        painter.end()
        self.update()

    def ask_text(self) -> str | None:
        text, ok = QInputDialog.getText(self, "Add text", "Text:")
        return text if ok and text else None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.layer is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.target().contains(event.position()):
            return
        point = self.to_frame(event.position())
        if self.tool == "text":
            text = self.ask_text()
            if text is not None:
                self._push_undo()
                self.draw_text(point, text)
                self.changed.emit()
            return
        self._push_undo()
        if self.tool == "rect":
            self._drag = (point, point)
        else:
            self._last = point
            self.stroke(point, point)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = self.to_frame(event.position())
        if self._drag is not None:
            self._drag = (self._drag[0], point)
            self.update()
        elif self._last is not None:
            self.stroke(self._last, point)
            self._last = point

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            start, end = self._drag
            self._drag = None
            self.fill_rect(start, end)
            self.changed.emit()
        elif self._last is not None:
            self._last = None
            self.changed.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(config.UI_COLORS["base"]))
        rect = self.target()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.base is not None and not self.base.isNull():
            painter.drawImage(rect, self.base)
        else:
            painter.setPen(QColor(config.UI_COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading the frame…")
        if self.layer is not None:
            painter.drawImage(rect, self.layer)
        if self._drag is not None:
            scale = rect.width() / max(1, self.frame_size.width())
            a, b = (QPointF(rect.x() + p.x() * scale, rect.y() + p.y() * scale)
                    for p in self._drag)  # fmt: skip
            color = QColor(self.color)
            color.setAlphaF(0.5)
            painter.fillRect(QRectF(a, b).normalized(), color)
        painter.end()


class ThumbnailLoader(QObject):
    """Draws filmstrip thumbnails on a worker thread, most recently requested first."""

    ready = Signal(int, QImage)  # entry id, thumbnail
    _ready = Signal(int, object)

    def __init__(self, session: FrameSession, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._queue: deque[FrameEntry] = deque()
        self._cond = threading.Condition()
        self._stopped = False
        self._ready.connect(self._on_ready, Qt.ConnectionType.QueuedConnection)
        self._thread = threading.Thread(target=self._run, name="codemotion-thumbs", daemon=True)
        self._thread.start()

    def request(self, entries: Iterable[FrameEntry], first: bool = False) -> None:
        """Queue thumbnails; ``first`` puts them before the ones already waiting."""
        wanted = list(entries)
        ids = {e.id for e in wanted}
        with self._cond:
            kept = [e for e in self._queue if e.id not in ids]
            self._queue = deque([*wanted, *kept] if first else [*kept, *wanted])
            self._cond.notify()

    @property
    def pending(self) -> int:
        with self._cond:
            return len(self._queue)

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._queue.clear()
            self._cond.notify()
        self._thread.join(5)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    return
                entry = self._queue.popleft()
            try:
                image = to_qimage(self.session.preview(entry))
            except Exception:  # deleted or being rewritten meanwhile; asked for again later
                continue
            thumb = image.scaledToHeight(config.REVIEW_THUMB_HEIGHT,
                                         Qt.TransformationMode.SmoothTransformation)  # fmt: skip
            self._ready.emit(entry.id, thumb)

    def _on_ready(self, entry_id: int, image: QImage) -> None:
        if not self._stopped:
            self.ready.emit(entry_id, image)


class FrameReviewDialog(QDialog):
    """Review every frame of ``session``; accepted means "render the approved frames"."""

    def __init__(self, session: FrameSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.current: FrameEntry = session.entries[0]
        self._icons: dict[int, QIcon] = {}
        self._thumbs: dict[int, QImage] = {}
        self._watched: dict[int, float] = {}  # entry id -> mtime of a file open in another app
        self.open_editor: Callable[[Path], None] = open_in_editor
        self.setWindowTitle(f"Review frames — {config.APP_NAME}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(1280, 860)

        self.loader = LatestWins(self)
        self.loader.finished.connect(self._on_base)
        self.loader.failed.connect(self._on_load_failed)
        self.thumbs = ThumbnailLoader(session, self)
        self.thumbs.ready.connect(self._on_thumbnail)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _: self.check_external_edits())

        self._build()
        self._fill_strip()
        self.thumbs.request(session.entries)
        self.show_entry(self._first_unapproved() or session.entries[0])

    # -- layout ----------------------------------------------------------------------------

    def _button(self, text: str, slot: Callable[[], object], tip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setAutoDefault(False)  # Enter approves; it never presses a button
        button.clicked.connect(lambda: slot())
        if tip:
            button.setToolTip(tip)
        return button

    def _build(self) -> None:
        self.steps = QLabel("1 Code ✓   →   <b>2 Review frames</b>   →   3 Render video")
        self.summary = QLabel()
        header = QHBoxLayout()
        header.addWidget(self.steps, 1)
        header.addWidget(self.summary)

        self.canvas = PaintCanvas()
        self.canvas.changed.connect(self._on_painted)

        tools = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        self.tool_buttons: dict[str, QToolButton] = {}
        for name, label in TOOLS:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, n=name: self.set_tool(n))
            self.tool_group.addButton(button)
            self.tool_buttons[name] = button
            tools.addWidget(button)
        self.tool_buttons["brush"].setChecked(True)
        self.color_button = QToolButton()
        self.color_button.setToolTip("Paint colour")
        self.color_button.clicked.connect(self.choose_color)
        self._show_color()
        self.size_box = QSpinBox()
        self.size_box.setRange(1, 200)
        self.size_box.setValue(config.REVIEW_PAINT_SIZE)
        self.size_box.setSuffix(" px")
        self.size_box.setToolTip("Brush and eraser width (text is 4 times this tall)")
        self.size_box.valueChanged.connect(lambda v: setattr(self.canvas, "pen_size", v))
        tools.addWidget(self.color_button)
        tools.addWidget(self.size_box)
        tools.addWidget(self._button("Undo", self.canvas.undo, "Undo the last stroke (Ctrl+Z)"))
        tools.addWidget(self._button("Clear paint", self.canvas.clear))
        tools.addSpacing(16)
        tools.addWidget(self._button(
            "Paint → selected", self.copy_paint_to_selected,
            "Copy this frame's paint to the frames selected in the filmstrip",
        ))  # fmt: skip
        tools.addStretch(1)
        tools.addWidget(self._button("Replace with image…", self.replace_with_image))
        tools.addWidget(self._button(
            "Edit in image app", self.edit_externally,
            "Open the frame in your image editor; it updates here when you save",
        ))  # fmt: skip
        tools.addWidget(self._button("Revert", self.revert, "Back to the generated frame"))
        tools.addWidget(self._button(
            "Hold +1", self.duplicate, "Show this frame one frame longer (a copy after it)"
        ))  # fmt: skip
        tools.addWidget(self._button("Delete", self.delete_selected, "Drop the selected frames"))

        self.frame_label = QLabel()
        self.prev_button = self._button("◀ Previous", lambda: self.step(-1))
        self.next_button = self._button("Next ▶", lambda: self.step(1))
        self.approve_button = self._button(
            "✓ Approve && next", self.approve_and_next, "Approve this frame (Enter)"
        )
        nav = QHBoxLayout()
        nav.addWidget(self.prev_button)
        nav.addWidget(self.frame_label, 1, Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.next_button)
        nav.addSpacing(16)
        nav.addWidget(self.approve_button)
        nav.addWidget(self._button("Approve selected", lambda: self.approve_selected(True)))
        nav.addWidget(self._button("Unapprove selected", lambda: self.approve_selected(False)))
        nav.addWidget(self._button("Approve all", self.approve_all))

        self.strip = QListWidget()
        self.strip.setViewMode(QListView.ViewMode.IconMode)
        self.strip.setFlow(QListView.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListView.Movement.Static)
        self.strip.setUniformItemSizes(True)
        self.strip.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        width, height = self.session.size
        thumb = QSize(max(1, round(config.REVIEW_THUMB_HEIGHT * width / height)),
                      config.REVIEW_THUMB_HEIGHT)  # fmt: skip
        self.strip.setIconSize(thumb)
        self.strip.setFixedHeight(thumb.height() + 56)
        self.strip.currentItemChanged.connect(self._on_strip_current)
        self._placeholder = QPixmap(thumb)
        self._placeholder.fill(QColor(config.UI_COLORS["button"]))

        self.render_button = self._button(
            "Render video ▶", self.accept, "Render the approved frames as the final video"
        )
        footer = QHBoxLayout()
        footer.addWidget(self._button(
            "Regenerate frames", self.regenerate,
            "Throw away this review and generate the frames again from the code",
        ))  # fmt: skip
        footer.addStretch(1)
        footer.addWidget(self._button("Back to code", self.reject,
                                      "Close the review; your edits are kept"))  # fmt: skip
        footer.addWidget(self.render_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(tools)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(nav)
        layout.addWidget(self.strip)
        layout.addLayout(footer)

        for keys, slot in (
            (["Left"], lambda: self.step(-1)),
            (["Right"], lambda: self.step(1)),
            (["Home"], lambda: self.show_entry(self.session.entries[0])),
            (["End"], lambda: self.show_entry(self.session.entries[-1])),
            (["Return", "Enter"], self.approve_and_next),
            (["Ctrl+Z"], self.canvas.undo),
            (["Delete"], self.delete_selected),
            (["B"], lambda: self.set_tool("brush")),
            (["R"], lambda: self.set_tool("rect")),
            (["T"], lambda: self.set_tool("text")),
            (["E"], lambda: self.set_tool("eraser")),
        ):
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.activated.connect(slot)

    # -- the filmstrip ---------------------------------------------------------------------

    def _item_text(self, index: int, entry: FrameEntry) -> str:
        marks = ("✓" if entry.approved else "") + ("✎" if self.session.is_edited(entry.id) else "")
        marks += "⧉" if self.session.is_duplicate(entry) else ""
        return f"{index + 1} {marks}".strip()

    def _fill_strip(self) -> None:
        self.strip.blockSignals(True)
        self.strip.clear()
        for index, entry in enumerate(self.session.entries):
            item = QListWidgetItem(self._icons.get(entry.id, QIcon(self._placeholder)),
                                   self._item_text(index, entry))  # fmt: skip
            item.setData(_ID_ROLE, entry.id)
            self.strip.addItem(item)
        self.strip.blockSignals(False)
        self._refresh_labels()

    def _refresh_labels(self, ids: Iterable[int] | None = None) -> None:
        wanted = None if ids is None else set(ids)
        ok = QColor(config.UI_COLORS["ok"])
        normal = QColor(config.UI_COLORS["text"])
        for index, entry in enumerate(self.session.entries):
            if wanted is not None and entry.id not in wanted:
                continue
            item = self.strip.item(index)
            if item is not None:
                item.setText(self._item_text(index, entry))
                item.setForeground(ok if entry.approved else normal)
        self._update_summary()

    def _update_summary(self) -> None:
        session = self.session
        total = len(session.entries)
        done = session.approved_count
        self.summary.setText(f"{done} of {total} frames approved · {session.edited_count} edited"
                             f" · {session.duration:.2f} s")  # fmt: skip
        self.render_button.setEnabled(done == total)
        self.render_button.setToolTip(
            "Render the approved frames as the final video" if done == total
            else f"Approve every frame first ({total - done} to go)"
        )  # fmt: skip
        index = self.session.index(self.current.id)
        state = "approved ✓" if self.current.approved else "not approved yet"
        edited = " · edited" if session.is_edited(self.current.id) else ""
        self.frame_label.setText(f"Frame {index + 1} of {total} · {index / session.fps:.2f} s"
                                 f" · {state}{edited}")  # fmt: skip

    def _on_thumbnail(self, entry_id: int, image: QImage) -> None:
        self._thumbs[entry_id] = image
        self._icons[entry_id] = QIcon(QPixmap.fromImage(image))
        with contextlib.suppress(KeyError):
            item = self.strip.item(self.session.index(entry_id))
            if item is not None:
                item.setIcon(self._icons[entry_id])

    def _on_strip_current(self, item: QListWidgetItem | None, _: object = None) -> None:
        if item is None:
            return
        with contextlib.suppress(KeyError):
            entry = self.session.entry(int(item.data(_ID_ROLE)))
            if entry.id != self.current.id or self.canvas.layer is None:
                self.show_entry(entry)

    def selected(self) -> list[FrameEntry]:
        """The frames selected in the filmstrip (at least the current one)."""
        ids = {int(item.data(_ID_ROLE)) for item in self.strip.selectedItems()}
        ids.add(self.current.id)
        return [e for e in self.session.entries if e.id in ids]

    # -- showing a frame -------------------------------------------------------------------

    def show_entry(self, entry: FrameEntry) -> None:
        self.current = entry
        index = self.session.index(entry.id)
        item = self.strip.item(index)
        if item is not None and self.strip.currentItem() is not item:
            self.strip.blockSignals(True)
            self.strip.setCurrentItem(item)
            self.strip.blockSignals(False)
        if item is not None:
            self.strip.scrollToItem(item)
        try:
            layer = self.session.paint(entry.id)
        except UserFacingError:
            layer = None
        width, height = self.session.size
        self.canvas.set_frame(QSize(width, height), to_qimage(layer) if layer is not None else None)
        thumb = self._thumbs.get(entry.id)
        if thumb is not None:  # until the full-size frame is drawn
            self.canvas.set_base(thumb)
        self.loader.submit(lambda: (entry.id, to_qimage(self.session.base(entry))))
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(self.session.entries) - 1)
        self._update_summary()

    def _on_base(self, result: tuple[int, QImage]) -> None:
        entry_id, image = result
        if entry_id == self.current.id and self.isVisible():
            self.canvas.set_base(image)

    def _on_load_failed(self, error: Exception) -> None:
        """A frame couldn't be read (e.g. an image app is saving it): say so, don't block."""
        if self.isVisible():
            message = error.message if isinstance(error, UserFacingError) else repr(error)
            self.frame_label.setText(f"⚠ {message}")

    def _on_error(self, error: Exception) -> None:
        message = error.message if isinstance(error, UserFacingError) else repr(error)
        QMessageBox.warning(self, config.APP_NAME, message)

    def step(self, delta: int) -> None:
        index = self.session.index(self.current.id) + delta
        if 0 <= index < len(self.session.entries):
            self.show_entry(self.session.entries[index])

    def _first_unapproved(self) -> FrameEntry | None:
        return next((e for e in self.session.entries if not e.approved), None)

    def _changed(self, entries: Iterable[FrameEntry]) -> None:
        """Frames whose pictures changed: new thumbnails, labels and (if shown) view."""
        changed = list(entries)
        self.thumbs.request(changed, first=True)
        self._refresh_labels(e.id for e in changed)

    # -- tools -----------------------------------------------------------------------------

    def set_tool(self, name: str) -> None:
        self.canvas.tool = name
        self.tool_buttons[name].setChecked(True)

    def _show_color(self) -> None:
        pixmap = QPixmap(QSize(18, 18))
        pixmap.fill(self.canvas.color)
        self.color_button.setIcon(QIcon(pixmap))

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.color, self, "Paint colour")
        if color.isValid():
            self.canvas.color = color
            self._show_color()

    def _on_painted(self) -> None:
        try:
            self.session.set_paint(self.current.id, self.canvas.layer_image())
        except UserFacingError as exc:
            self._on_error(exc)
            return
        self._changed([self.current])

    def copy_paint_to_selected(self) -> None:
        layer = self.canvas.layer_image()
        targets = [e for e in self.selected() if e.id != self.current.id]
        if not targets:
            self._info("Select the frames to copy the paint to in the filmstrip "
                       "(Shift- or Ctrl-click), then press this again.")  # fmt: skip
            return
        try:
            for entry in targets:
                self.session.set_paint(entry.id, layer)
        except UserFacingError as exc:
            self._on_error(exc)
        self._changed(targets)

    def replace_with_image(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Replace the frame with an image", "",
                                              "Images (*.png *.jpg *.jpeg)")  # fmt: skip
        if name:
            self.replace_current(Path(name))

    def replace_current(self, image_file: Path) -> None:
        try:
            self.session.replace(self.current.id, image_file)
        except UserFacingError as exc:
            self._on_error(exc)
            return
        self._changed([self.current])
        self.show_entry(self.current)

    def edit_externally(self) -> None:
        """Save the frame (with its paint) as its replacement image and open it elsewhere."""
        try:
            path = self.session.flatten(self.current.id)
        except UserFacingError as exc:
            self._on_error(exc)
            return
        self._watched[self.current.id] = path.stat().st_mtime
        self.watcher.addPath(str(path))
        self._changed([self.current])
        self.show_entry(self.current)
        self.open_editor(path)

    def check_external_edits(self) -> None:
        """Pick up frames saved in another app since they were opened."""
        changed: list[FrameEntry] = []
        for entry_id, mtime in list(self._watched.items()):
            path = self.session.edit_path(entry_id)
            try:
                entry = self.session.entry(entry_id)
                now = path.stat().st_mtime
            except (KeyError, OSError):  # deleted, or mid-save
                continue
            if now != mtime:
                self._watched[entry_id] = now
                if str(path) not in self.watcher.files():  # editors often save by replacing
                    self.watcher.addPath(str(path))
                self.session.approve([entry_id], False)
                changed.append(entry)
        if changed:
            self._changed(changed)
            if any(e.id == self.current.id for e in changed):
                self.show_entry(self.current)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowActivate and self._watched:
            self.check_external_edits()  # back from the image editor
        return super().event(event)

    def revert(self) -> None:
        self.session.revert(self.current.id)
        self._watched.pop(self.current.id, None)
        self._changed([self.current])
        self.show_entry(self.current)

    def duplicate(self) -> None:
        copy = self.session.duplicate(self.current.id)
        if self.current.id in self._icons:
            self._icons[copy.id] = self._icons[self.current.id]
            self._thumbs[copy.id] = self._thumbs[self.current.id]
        self._fill_strip()
        self.show_entry(copy)

    def delete_selected(self) -> None:
        doomed = self.selected()
        if len(doomed) > 1:
            question = f"Delete {len(doomed)} frames from the video?"
            answer = QMessageBox.question(self, config.APP_NAME, question)
            if answer != QMessageBox.StandardButton.Yes:
                return
        index = self.session.index(doomed[0].id)
        try:
            self.session.delete(e.id for e in doomed)
        except UserFacingError as exc:
            self._on_error(exc)
            return
        for entry in doomed:
            self._watched.pop(entry.id, None)
        self.current = self.session.entries[min(index, len(self.session.entries) - 1)]
        self._fill_strip()
        self.show_entry(self.current)

    # -- approving -------------------------------------------------------------------------

    def approve_and_next(self) -> None:
        self.session.approve([self.current.id])
        self._refresh_labels([self.current.id])
        index = self.session.index(self.current.id)
        if index < len(self.session.entries) - 1:
            self.show_entry(self.session.entries[index + 1])
        else:
            pending = self._first_unapproved()
            if pending is not None:
                self.show_entry(pending)
            self._update_summary()

    def approve_selected(self, approved: bool) -> None:
        entries = self.selected()
        self.session.approve((e.id for e in entries), approved)
        self._refresh_labels(e.id for e in entries)

    def approve_all(self) -> None:
        pending = [e for e in self.session.entries if not e.approved]
        if not pending:
            return
        answer = QMessageBox.question(
            self, config.APP_NAME,
            f"Approve all {len(pending)} remaining frames without looking at each one?",
        )  # fmt: skip
        if answer == QMessageBox.StandardButton.Yes:
            self.session.approve(e.id for e in pending)
            self._refresh_labels(e.id for e in pending)

    # -- closing ---------------------------------------------------------------------------

    def regenerate(self) -> None:
        edited = self.session.edited_count
        detail = f" and your edits to {edited} frame{'s' if edited != 1 else ''}" if edited else ""
        answer = QMessageBox.question(self, config.APP_NAME,
                                      f"Generate the frames again? The approvals{detail} "
                                      "will be lost.")  # fmt: skip
        if answer == QMessageBox.StandardButton.Yes:
            self.done(REGENERATE)

    def accept(self) -> None:
        if self.session.all_approved:
            super().accept()

    def done(self, result: int) -> None:
        self.thumbs.stop()
        self.loader.wait(5)
        for path in self.watcher.files():
            self.watcher.removePath(path)
        super().done(result)

    def _info(self, message: str) -> None:
        QMessageBox.information(self, config.APP_NAME, message)
