"""The CodeMotion main window (SPEC 4.1): editor, run, preview, elements, renders."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codemotion import __version__, config
from codemotion.analysis.normalize import decode
from codemotion.desktop.dialogs import CodeSettingsDialog
from codemotion.desktop.editor import CodeEditor
from codemotion.desktop.preview import PreviewPanel
from codemotion.desktop.review import REGENERATE, FrameReviewDialog
from codemotion.desktop.workers import (
    FramesJob,
    LatestWins,
    ProgramJob,
    RenderJob,
    RenderWorker,
    ReviewRenderJob,
    RunRequest,
    RunResult,
    run_source,
    still_image,
)
from codemotion.errors import UserFacingError
from codemotion.models import ElementPlan, SoundPlan
from codemotion.render.encoder import ExportFormat
from codemotion.render.renderer import Progress, default_output, program_output
from codemotion.review import FrameSession
from codemotion.script.program import ProgramResult, incomplete
from codemotion.script.runner import ScriptRunError
from codemotion.script.source import SourceKind, SourceMode, source_kind
from codemotion.script.template import STARTER

_LINE_ROLE = Qt.ItemDataRole.UserRole
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
_STAGE_LABELS = {"frames": "", "program": "Running the program ", "import": "Importing ",
                 "extract": "Collecting the frames "}  # fmt: skip

PROGRAM_HINT = (
    "This is a video program: it draws its own frames (Pillow/OpenCV) and writes a video. "
    "Press Run to run it and play its video, or Render to also save it to Renders. "
    "It isn't run automatically while you type."
)
CODE_CARD_HINT = (
    "Shown as a code card: this code doesn't import codemotion or an image/video library "
    "(Pillow, OpenCV…), so it's displayed, not run. To run it, choose Run as → Video program."
)
# Toolbar "Run as" choices; Auto's label also shows what it detected.
RUN_AS = (
    (SourceMode.auto, "Auto"),
    (SourceMode.script, "Video script (CodeMotion)"),
    (SourceMode.program, "Video program (Pillow/OpenCV)"),
    (SourceMode.code, "Code card"),
)
KIND_NAMES = {
    SourceKind.script: "video script",
    SourceKind.program: "video program",
    SourceKind.code: "code card",
}


def app_settings() -> QSettings:
    return QSettings(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, config.APP_NAME, "desktop"
    )


@dataclass(frozen=True)
class Problem:
    severity: str  # "error" | "warning"
    message: str
    line: int | None = None


@dataclass(frozen=True)
class RenderEntry:
    video: Path
    aspect: str
    duration: float
    preview: bool
    exports: tuple[str, ...] = ()  # file names of GIF / WebM copies

    @property
    def label(self) -> str:
        tag = " · preview" if self.preview else ""
        extra = "".join(f" +{Path(name).suffix[1:].upper()}" for name in self.exports)
        return f"{self.video.stem} · {self.aspect} · {self.duration:g} s{tag}{extra}"


def list_renders(folder: Path, limit: int = 50) -> list[RenderEntry]:
    """Videos in ``folder`` that have a CodeMotion sidecar, newest first."""
    entries: list[tuple[float, RenderEntry]] = []
    try:
        sidecars = list(folder.glob("*.json"))
    except OSError:
        return []
    for sidecar in sidecars:
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            video = sidecar.with_name(str(data["video"]))
            scene = data["scene"]
            exports = tuple(str(name) for name in data.get("exports", []))
            entry = RenderEntry(video, str(scene["aspect"]), float(scene["duration"]),
                                bool(data.get("preview", False)), exports)  # fmt: skip
            if "codemotion" in data and video.is_file():
                entries.append((video.stat().st_mtime, entry))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # not ours, or unreadable
    entries.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in entries[:limit]]


def element_details(element: ElementPlan) -> str:
    parts = [f"{element.animations} anim{'s' if element.animations != 1 else ''}"]
    if element.reveal is not None:
        parts.append(f"{element.reveal} {element.reveal_start:g}-{element.reveal_end:g} s")
        if element.scroll:
            parts.append("scrolls")
    return ", ".join(parts)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else app_settings()
        self.path: Path | None = None
        self.newline = "\n"  # the open file's line endings, kept on save
        self.code_options: dict[str, Any] | None = None  # plain code files only
        self.result: RunResult | None = None  # the last successful run
        self.source_mode = SourceMode.auto  # Run as; reset for each file
        self.problems: list[Problem] = []
        self._after_run: str | None = None  # "render", "preview" or "review", once current
        self.review_dialog: FrameReviewDialog | None = None
        self._preview_dir = Path(tempfile.mkdtemp(prefix="codemotion-preview-"))
        default_out = str(config.DESKTOP_OUTPUT_DIR.resolve())
        self.output_dir = Path(str(self.settings.value("output_dir", default_out)))

        self.runner = LatestWins(self)
        self.runner.finished.connect(self._on_run_finished)
        self.runner.failed.connect(self._on_run_failed)
        self.stills = LatestWins(self)
        self.stills.finished.connect(self._on_still)
        self.stills.failed.connect(self._on_still_failed)
        self.renderer = RenderWorker(self)
        self.renderer.progress.connect(self._on_render_progress)
        self.renderer.output.connect(self.output_view_append)
        self.renderer.finished.connect(self._on_render_finished)
        self.renderer.failed.connect(self._on_render_failed)
        self.renderer.cancelled.connect(self._on_render_cancelled)
        self.auto_timer = QTimer(self)
        self.auto_timer.setSingleShot(True)
        self.auto_timer.setInterval(config.AUTO_RUN_DELAY_MS)
        self.auto_timer.timeout.connect(lambda: self.run(auto=True))

        self._build_widgets()
        self._build_actions()
        self._build_menus()
        self.setAcceptDrops(True)
        self._restore_settings()
        self._load_text(STARTER.format(name="untitled.py"), None)
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------ layout

    def _build_widgets(self) -> None:
        self.editor = CodeEditor()
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.document().modificationChanged.connect(lambda _: self._update_title())
        self.editor.files_dropped.connect(self._open_dropped)

        self.problem_list = QListWidget()
        self.problem_list.itemActivated.connect(self._on_problem_activated)
        self.problem_list.itemClicked.connect(self._on_problem_activated)
        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setPlaceholderText("What the script prints appears here.")
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.problem_list, "Problems")
        self.bottom_tabs.addTab(self.output_view, "Output")

        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.addWidget(self.editor)
        self.left_split.addWidget(self.bottom_tabs)
        self.left_split.setStretchFactor(0, 4)
        self.left_split.setStretchFactor(1, 1)

        self.preview = PreviewPanel()
        self.preview.time_changed.connect(self._request_still)

        self.element_tree = QTreeWidget()
        self.element_tree.setHeaderLabels(["Element", "Type", "Shown", "Details"])
        self.element_tree.setRootIsDecorated(False)
        self.element_tree.itemDoubleClicked.connect(self._on_element_activated)

        self.render_list = QListWidget()
        self.render_list.itemActivated.connect(self._on_render_activated)
        self.render_list.itemDoubleClicked.connect(self._on_render_activated)
        self.play_render_button = QPushButton("Play")
        self.play_render_button.clicked.connect(
            lambda: self._on_render_activated(self.render_list.currentItem())
        )
        self.open_folder_button = QPushButton("Show folder")
        self.open_folder_button.clicked.connect(self.show_output_folder)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.progress_label = QLabel("")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_render)
        self._set_rendering(False)

        renders = QWidget()
        renders_layout = QVBoxLayout(renders)
        renders_layout.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Renders"), 1)
        title_row.addWidget(self.play_render_button)
        title_row.addWidget(self.open_folder_button)
        renders_layout.addLayout(title_row)
        renders_layout.addWidget(self.render_list, 1)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.cancel_button)
        renders_layout.addLayout(progress_row)

        elements = QWidget()
        elements_layout = QVBoxLayout(elements)
        elements_layout.setContentsMargins(0, 0, 0, 0)
        elements_layout.addWidget(QLabel("Elements"))
        elements_layout.addWidget(self.element_tree, 1)

        self.right_split = QSplitter(Qt.Orientation.Vertical)
        self.right_split.addWidget(self.preview)
        self.right_split.addWidget(elements)
        self.right_split.addWidget(renders)
        self.right_split.setStretchFactor(0, 3)
        self.right_split.setStretchFactor(1, 1)
        self.right_split.setStretchFactor(2, 1)

        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split.addWidget(self.left_split)
        self.main_split.addWidget(self.right_split)
        self.main_split.setStretchFactor(0, 1)
        self.main_split.setStretchFactor(1, 1)
        self.setCentralWidget(self.main_split)
        self.resize(1400, 860)

    def _action(self, text: str, slot: Any, shortcut: Any = None, tip: str = "") -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            shortcuts = shortcut if isinstance(shortcut, list) else [shortcut]
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        return action

    def _build_actions(self) -> None:
        self.new_action = self._action("&New", self.new_file, QKeySequence.StandardKey.New)
        self.open_action = self._action("&Open…", self.open_file, QKeySequence.StandardKey.Open)
        self.save_action = self._action("&Save", self.save, QKeySequence.StandardKey.Save)
        self.save_as_action = self._action("Save &As…", self.save_as, "Ctrl+Shift+S")
        self.output_folder_action = self._action("Choose output folder…", self.choose_output_dir)
        self.quit_action = self._action("&Quit", self.close, QKeySequence.StandardKey.Quit)
        self.run_action = self._action(
            "▶ Run", lambda: self.run(), ["Ctrl+Return", "Ctrl+Enter"],
            "Run the script and update the preview (Ctrl+Enter)",
        )  # fmt: skip
        self.quick_action = self._action(
            "Quick preview", lambda: self.render_script(preview=True), "Ctrl+Shift+Return",
            "Render a fast 540p, 15 fps preview and play it",
        )  # fmt: skip
        self.review_action = self._action(
            "Review frames", self.review_frames, "Ctrl+Shift+R",
            "Generate every frame, fix and approve each one, then render the final video",
        )  # fmt: skip
        self.render_action = self._action(
            "Render", lambda: self.render_script(), "Ctrl+R", "Render the full-quality MP4"
        )
        self.cancel_action = self._action("Cancel render", self.cancel_render, "Esc")
        self.cancel_action.setEnabled(False)
        self.code_settings_action = self._action(
            "Code card settings…", self.edit_code_settings,
            tip="Duration, format and style for a plain code file",
        )  # fmt: skip
        self.auto_run_action = QAction("Auto-run", self)
        self.auto_run_action.setCheckable(True)
        self.auto_run_action.setToolTip("Run the script automatically when you stop typing")
        self.gif_action = QAction("Also export GIF", self)
        self.gif_action.setCheckable(True)
        self.gif_action.setToolTip("Render also writes a 540p, 15 fps GIF next to the MP4")
        self.webm_action = QAction("Also export WebM", self)
        self.webm_action.setCheckable(True)
        self.webm_action.setToolTip("Render also writes a WebM (VP9) next to the MP4")
        self.trust_action = self._action("About running scripts", self.show_trust_notice)
        self.about_action = self._action("About CodeMotion", self.show_about)

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        for action in (self.run_action, self.quick_action, self.review_action,
                       self.render_action):  # fmt: skip
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.auto_run_action)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Run as: "))
        self.mode_box = QComboBox()
        self.mode_box.setToolTip(
            "How to treat the code: Auto decides from its imports (codemotion → video script; "
            "Pillow/OpenCV → video program; anything else → code card)"
        )
        for mode, label in RUN_AS:
            self.mode_box.addItem(label, mode)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self.mode_box)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.new_action)
        file_menu.addAction(self.open_action)
        self.examples_menu = file_menu.addMenu("Open &example")
        self._fill_examples_menu()
        self.recent_menu = file_menu.addMenu("Open &recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(self.output_folder_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        edit_menu = bar.addMenu("&Edit")
        for text, slot, key in (
            ("Undo", self.editor.undo, QKeySequence.StandardKey.Undo),
            ("Redo", self.editor.redo, QKeySequence.StandardKey.Redo),
            ("Cut", self.editor.cut, QKeySequence.StandardKey.Cut),
            ("Copy", self.editor.copy, QKeySequence.StandardKey.Copy),
            ("Paste", self.editor.paste, QKeySequence.StandardKey.Paste),
            ("Select all", self.editor.selectAll, QKeySequence.StandardKey.SelectAll),
        ):
            action = self._action(text, slot, key)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # editor handles keys
            edit_menu.addAction(action)

        video_menu = bar.addMenu("&Video")
        for action in (self.run_action, self.quick_action, self.review_action,
                       self.render_action, self.cancel_action):  # fmt: skip
            video_menu.addAction(action)
        video_menu.addSeparator()
        video_menu.addAction(self.auto_run_action)
        video_menu.addAction(self.code_settings_action)
        video_menu.addSeparator()
        video_menu.addAction(self.gif_action)
        video_menu.addAction(self.webm_action)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.trust_action)
        help_menu.addAction(self.about_action)

    def _fill_examples_menu(self) -> None:
        groups = (
            ("Video scripts", config.EXAMPLES_DIR / "scripts"),
            ("Code files (code card)", config.EXAMPLES_DIR),
        )
        for title, folder in groups:
            files = sorted(folder.glob("*.py")) if folder.is_dir() else []
            if not files:
                continue
            self.examples_menu.addSection(title)
            for file in files:
                action = self.examples_menu.addAction(file.name)
                action.triggered.connect(lambda _=False, f=file: self.open_path(f))
        self.examples_menu.setEnabled(not self.examples_menu.isEmpty())

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.recent_files()
        for path in recent:
            action = self.recent_menu.addAction(str(path))
            action.triggered.connect(lambda _=False, p=path: self.open_path(p))
        if not recent:
            self.recent_menu.addAction("No recent files").setEnabled(False)

    # ------------------------------------------------------------------ files

    @property
    def base_dir(self) -> Path:
        """Where relative paths in the script resolve: its folder (untitled: the cwd)."""
        return self.path.parent if self.path is not None else Path.cwd()

    @property
    def modified(self) -> bool:
        return self.editor.document().isModified()

    def new_file(self) -> None:
        if self.maybe_save():
            self._load_text(STARTER.format(name="untitled.py"), None)

    def open_file(self) -> None:
        start = str(self.settings.value("last_dir", str(Path.cwd())))
        name, _ = QFileDialog.getOpenFileName(
            self, "Open script or code file", start, "Python files (*.py);;All files (*)"
        )
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path, ask_code_settings: bool = True) -> bool:
        """Open ``path``; a plain code file offers the code-card settings first."""
        if not self.maybe_save():
            return False
        try:
            raw = path.read_bytes()
            text = decode(raw)
        except OSError as exc:
            self._error_box(f"Could not open {path}: {exc.strerror}.")
            return False
        except UserFacingError as exc:
            self._error_box(exc.message)
            return False
        self.settings.setValue("last_dir", str(path.parent))
        self._add_recent(path)
        self.newline = "\r\n" if b"\r\n" in raw else "\n"
        self._load_text(text.replace("\r\n", "\n"), path, run=False)
        if self.kind is SourceKind.code and ask_code_settings:
            self.edit_code_settings(run=False)
        self.run(auto=True)
        return True

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        start = str(self.path or Path(str(self.settings.value("last_dir", str(Path.cwd())))))
        name, _ = QFileDialog.getSaveFileName(self, "Save script", start, "Python files (*.py)")
        if not name:
            return False
        return self._write(Path(name))

    def _write(self, path: Path) -> bool:
        data = self.editor.toPlainText().replace("\n", self.newline).encode("utf-8")
        try:
            path.write_bytes(data)
        except OSError as exc:
            self._error_box(f"Could not save {path}: {exc.strerror}.")
            return False
        moved = path != self.path
        self.path = path
        self.editor.document().setModified(False)
        self.settings.setValue("last_dir", str(path.parent))
        self._add_recent(path)
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        if moved:  # relative paths in the script now resolve from the new folder
            self.run(auto=True)
        return True

    def maybe_save(self) -> bool:
        """Ask to save unsaved changes; False if the user cancelled."""
        if not self.modified:
            return True
        name = self.path.name if self.path else "untitled.py"
        answer = QMessageBox.question(
            self,
            config.APP_NAME,
            f"Save changes to {name}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def recent_files(self) -> list[Path]:
        value = self.settings.value("recent_files", [])
        items = (
            [value] if isinstance(value, str) else list(value) if isinstance(value, list) else []
        )
        return [Path(str(item)) for item in items if Path(str(item)).is_file()]

    def _add_recent(self, path: Path) -> None:
        recent = [p for p in self.recent_files() if p.resolve() != path.resolve()]
        recent.insert(0, path)
        self.settings.setValue("recent_files", [str(p) for p in recent[: config.RECENT_FILES_MAX]])

    def _load_text(self, text: str, path: Path | None, run: bool = True) -> None:
        self.path = path
        self.code_options = None
        self.result = None
        self.source_mode = SourceMode.auto
        self.mode_box.blockSignals(True)
        self.mode_box.setCurrentIndex(0)
        self.mode_box.blockSignals(False)
        self.editor.setPlainText(text)
        self._update_kind_label()
        self.editor.document().setModified(False)
        self.editor.set_error_line(None)
        self.auto_timer.stop()
        self._update_title()
        if run:
            self.run(auto=True)

    def _open_dropped(self, paths: list[Path]) -> None:
        files = [p for p in paths if p.is_file()]
        if files:
            self.open_path(files[0])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        self._open_dropped(paths)

    def _update_title(self) -> None:
        name = self.path.name if self.path else "untitled.py"
        star = "*" if self.modified else ""
        self.setWindowTitle(f"{name}{star} — {config.APP_NAME}")

    # ------------------------------------------------------------------ running

    @property
    def kind(self) -> SourceKind:
        """What the editor holds, as Run as says (Auto: detected from its imports)."""
        return source_kind(self.editor.toPlainText(), self.source_mode)

    @property
    def is_program(self) -> bool:
        """True if the editor holds a video program (it writes its own video)."""
        return self.kind is SourceKind.program

    def output_view_append(self, line: str) -> None:
        self.output_view.appendPlainText(line)

    def _update_kind_label(self) -> None:
        """Show what Auto detected, e.g. "Auto: video program"."""
        detected = KIND_NAMES[source_kind(self.editor.toPlainText())]
        self.mode_box.setItemText(0, f"Auto: {detected}")

    def _on_mode_changed(self, index: int) -> None:
        self.source_mode = SourceMode(self.mode_box.itemData(index))
        self.result = None
        self.run(auto=True)

    def current_request(self) -> RunRequest | None:
        """What a run would execute now; None for a code file without settings."""
        text = self.editor.toPlainText()
        if self.kind is SourceKind.script:
            return RunRequest(text, self.base_dir, SourceMode.script)
        if self.code_options is None:
            return None
        return RunRequest(text, self.base_dir, SourceMode.code, dict(self.code_options))

    def run(self, auto: bool = False) -> bool:
        """Run the script (or build the code card); False if nothing was started."""
        self.auto_timer.stop()
        if not self.editor.toPlainText().strip():
            self._set_problems([])
            return False
        if self.is_program:
            # Programs render every frame themselves (slow, and they write files), so they
            # run only when asked: Run plays the video, Render also saves it.
            if auto:
                self.result = None
                self._fill_elements([])
                problems = [Problem("warning", PROGRAM_HINT)]
                cut_off = incomplete(self.editor.toPlainText())
                if cut_off is not None:  # say so now, not after a run that does nothing
                    problems.insert(0, Problem("error", *cut_off))
                self._set_problems(problems)
                self.statusBar().showMessage("Video program: press Run or Render to run it.")
                return False
            self.render_script(preview=True)
            return True
        request = self.current_request()
        if request is None and not auto and self.edit_code_settings(run=False):
            request = self.current_request()
        if request is None:
            self._set_problems([Problem(
                "warning",
                "This is shown as a code card: choose its settings in Video → Code card "
                "settings…, or to run it, choose Run as → Video program.",
            )])  # fmt: skip
            return False
        self.statusBar().showMessage("Running…")
        self.runner.submit(lambda: run_source(request))
        return True

    def edit_code_settings(self, run: bool = True) -> bool:
        dialog = CodeSettingsDialog(self.code_options, self)
        if dialog.exec() != CodeSettingsDialog.DialogCode.Accepted:
            return False
        self.code_options = dialog.options()
        if run:
            self.run()
        return True

    def _on_text_changed(self) -> None:
        self._update_kind_label()
        if self.auto_run_action.isChecked():
            self.auto_timer.start()

    def _on_run_finished(self, result: RunResult) -> None:
        first = self.result is None
        self.result = result
        plan = result.plan
        self.editor.set_error_line(None)
        problems = [Problem("warning", w) for w in plan.warnings]
        if not result.loaded.is_script and self.source_mode is SourceMode.auto:
            problems.insert(0, Problem("warning", CODE_CARD_HINT))
        self._set_problems(problems)
        self.output_view.setPlainText(result.loaded.output)
        self._fill_elements(plan.elements, plan.sounds)
        self.preview.set_duration(plan.duration)
        doc = result.loaded.doc
        if first:  # open on the finished scene rather than the black first frame
            self.preview.set_time(max(0.0, doc.duration - doc.fade_out - 1 / result.prepared.fps))
        self._request_still(self.preview.time)
        kind = "Script" if result.loaded.is_script else "Code card"
        count = len(plan.elements)
        self.statusBar().showMessage(
            f"{kind} ran in {result.seconds:.2f} s · {count} element{'s' if count != 1 else ''}"
            f" · {doc.duration:g} s {doc.aspect.value}"
        )
        if self._after_run is not None and result.request == self.current_request():
            action, self._after_run = self._after_run, None
            if action == "review":
                self._start_frames()
            else:
                self._start_render(preview=action == "preview")

    def _on_run_failed(self, error: Exception) -> None:
        line = error.line if isinstance(error, ScriptRunError) else None
        if isinstance(error, UserFacingError):
            message = error.message
        else:
            message = f"Unexpected error: {type(error).__name__}: {error}"
        self._set_problems([Problem("error", message, line)])
        self.editor.set_error_line(line)
        self.bottom_tabs.setCurrentWidget(self.problem_list)
        if not self.runner.busy:
            self._after_run = None
        self.statusBar().showMessage("The script failed; see Problems.")

    def _set_problems(self, problems: list[Problem]) -> None:
        self.problems = problems
        self.problem_list.clear()
        for problem in problems:
            icon = "✖" if problem.severity == "error" else "⚠"
            item = QListWidgetItem(f"{icon} {problem.message}")
            item.setForeground(QColor(config.UI_COLORS[problem.severity]))
            item.setData(_LINE_ROLE, problem.line)
            if problem.line is not None:
                item.setToolTip(f"Go to line {problem.line}")
            self.problem_list.addItem(item)
        errors = sum(p.severity == "error" for p in problems)
        label = "Problems" + (f" ({len(problems)})" if problems else "")
        self.bottom_tabs.setTabText(0, label + (" ✖" if errors else ""))

    def _on_problem_activated(self, item: QListWidgetItem) -> None:
        line = item.data(_LINE_ROLE)
        if isinstance(line, int):
            self.editor.go_to_line(line)

    def _fill_elements(
        self, elements: list[ElementPlan], sounds: list[SoundPlan] | None = None
    ) -> None:
        sounds = sounds or []
        self.element_tree.clear()
        items: dict[str, QTreeWidgetItem] = {}
        for element in elements:
            shown = f"{element.start:g}-{element.end:g} s"
            item = QTreeWidgetItem([element.id, element.type, shown, element_details(element)])
            item.setData(0, _LINE_ROLE, element.start)
            parent = items.get(element.parent) if element.parent is not None else None
            if parent is not None:
                parent.addChild(item)
            else:
                self.element_tree.addTopLevelItem(item)
            items[element.id] = item
        for cue in sounds:
            item = QTreeWidgetItem([cue.name, "sound", f"{cue.at:g} s", f"volume {cue.volume:g}"])
            item.setData(0, _LINE_ROLE, cue.at)
            self.element_tree.addTopLevelItem(item)
        self.element_tree.setRootIsDecorated(any(e.parent for e in elements))
        self.element_tree.expandAll()
        for column in range(3):
            self.element_tree.resizeColumnToContents(column)

    def _on_element_activated(self, item: QTreeWidgetItem, column: int) -> None:
        start = item.data(0, _LINE_ROLE)
        if isinstance(start, float | int):
            self.preview.show_stills()
            self.preview.set_time(float(start) + 0.5)

    # ------------------------------------------------------------------ stills

    def _request_still(self, t: float) -> None:
        if self.result is None:
            return
        stills = self.result.stills
        t = min(max(0.0, t), self.result.loaded.doc.duration)
        self.stills.submit(lambda: still_image(stills, t))

    def _on_still(self, image: QImage) -> None:
        self.preview.show_image(image)

    def _on_still_failed(self, error: Exception) -> None:
        message = error.message if isinstance(error, UserFacingError) else str(error)
        self.statusBar().showMessage(f"Preview failed: {message}")

    # ------------------------------------------------------------------ rendering

    def render_script(self, preview: bool = False) -> None:
        """Render the current script (running it first if it changed since the last run)."""
        if self.renderer.busy:
            self.statusBar().showMessage("A render is already running.")
            return
        if self.is_program:
            self._start_program(preview)
            return
        if self.result is not None and self.result.request == self.current_request():
            self._start_render(preview)
            return
        self._after_run = "preview" if preview else "render"
        if not self.run():
            self._after_run = None

    def _start_render(self, preview: bool) -> None:
        if self.result is None or self.renderer.busy:
            return
        result = self.result
        folder = self._preview_dir if preview else self.output_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_problems([Problem("error", f"Could not create {folder}: {exc.strerror}.")])
            return
        stem = self.path.stem if self.path else "untitled"
        output = default_output(stem, result.loaded.doc, preview, folder)
        job = RenderJob(
            doc=result.loaded.doc,
            output=output,
            preview=preview,
            source=str(self.path) if self.path else None,
            warnings=list(result.plan.warnings),
            formats=() if preview else self.export_formats(),
        )
        self.renderer.start(job)
        self._set_rendering(True, "Preview" if preview else "Render")

    def _start_program(self, preview: bool) -> None:
        """Run the video program; Run (``preview``) plays its video, Render also lists it."""
        folder = self._preview_dir if preview else self.output_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_problems([Problem("error", f"Could not create {folder}: {exc.strerror}.")])
            return
        stem = self.path.stem if self.path else "untitled"
        job = ProgramJob(
            source=self.editor.toPlainText(),
            output=program_output(stem, folder),
            preview=preview,
            script_path=self.path,
            formats=() if preview else self.export_formats(),
        )
        self.editor.set_error_line(None)
        self._set_problems([])
        self._fill_elements([])
        self.output_view.clear()
        self.bottom_tabs.setCurrentWidget(self.output_view)
        self.renderer.start(job)
        self._set_rendering(True, "Running the program")
        self.statusBar().showMessage("Running the video program…")

    # ------------------------------------------------------------------ frame review

    def review_frames(self) -> None:
        """Step 2 of Code → Review frames → Render: generate every frame (running the
        script first if it changed) and open them for review."""
        if self.renderer.busy:
            self.statusBar().showMessage("A render is already running.")
            return
        current = self.result is not None and self.result.request == self.current_request()
        if self.is_program or current:
            self._start_frames()
            return
        self._after_run = "review"
        if not self.run():
            self._after_run = None

    def _start_frames(self) -> None:
        if self.renderer.busy:
            return
        stem = self.path.stem if self.path else "untitled"
        if self.is_program:
            job = FramesJob(self.output_dir, stem, source=self.editor.toPlainText(),
                            script_path=self.path)  # fmt: skip
            self.editor.set_error_line(None)
            self._set_problems([])
            self._fill_elements([])
            self.output_view.clear()
            self.bottom_tabs.setCurrentWidget(self.output_view)
        elif self.result is not None:
            job = FramesJob(self.output_dir, stem, doc=self.result.loaded.doc,
                            script_path=self.path)  # fmt: skip
        else:
            return
        self.renderer.start(job)
        self._set_rendering(True, "Generating the frames")
        self.statusBar().showMessage("Generating the frames…")

    def _open_review(self, session: FrameSession) -> None:
        dialog = FrameReviewDialog(session, self)
        dialog.finished.connect(lambda code: self._on_review_closed(session, code))
        self.review_dialog = dialog
        count = len(session.entries)
        self.statusBar().showMessage(f"Review the {count} frames, then render the video.")
        dialog.open()

    def _on_review_closed(self, session: FrameSession, code: int) -> None:
        self.review_dialog = None
        if code == REGENERATE:
            session.discard()
            self.review_frames()
        elif code == FrameReviewDialog.DialogCode.Accepted.value:
            self._render_review(session)
        else:
            edited = session.edited_count
            kept = f"; your edits to {edited} frames are kept" if edited else ""
            self.statusBar().showMessage(
                f"Review closed: {session.approved_count} of {len(session.entries)} frames "
                f"approved{kept}. Review frames picks up where you left off."
            )

    def _render_review(self, session: FrameSession) -> None:
        if self.renderer.busy:
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            message = f"Could not create {self.output_dir}: {exc.strerror}."
            self._set_problems([Problem("error", message)])
            return
        stem = self.path.stem if self.path else "untitled"
        output = program_output(f"{stem}-reviewed", self.output_dir)
        self.renderer.start(ReviewRenderJob(session, output, self.export_formats()))
        self._set_rendering(True, "Rendering the reviewed frames")

    def export_formats(self) -> tuple[ExportFormat, ...]:
        """Extra formats a full render writes, from Video → Also export …"""
        formats: list[ExportFormat] = []
        if self.gif_action.isChecked():
            formats.append("gif")
        if self.webm_action.isChecked():
            formats.append("webm")
        return tuple(formats)

    def cancel_render(self) -> None:
        if self.renderer.busy:
            self.renderer.cancel()
            self.progress_label.setText("Cancelling…")

    def _set_rendering(self, active: bool, what: str = "") -> None:
        self.progress_bar.setVisible(active)
        self.progress_label.setVisible(active)
        self.cancel_button.setVisible(active)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"{what} starting…" if active else "")
        for action in ("render_action", "quick_action", "review_action"):
            if hasattr(self, action):
                getattr(self, action).setEnabled(not active)
        if hasattr(self, "cancel_action"):
            self.cancel_action.setEnabled(active)

    def _on_render_progress(self, progress: Progress) -> None:
        self.progress_bar.setValue(round(progress.fraction * 1000))
        eta = f" · ETA {progress.eta:.0f} s" if progress.eta is not None else ""
        stage = _STAGE_LABELS.get(progress.stage, f"Exporting {progress.stage.upper()} ")
        self.progress_label.setText(f"{stage}{progress.fraction:.0%}{eta}")

    def _on_render_finished(
        self,
        outcome: tuple[RenderJob | ReviewRenderJob, Path]
        | tuple[ProgramJob, ProgramResult]
        | tuple[FramesJob, FrameSession],
    ) -> None:
        job, result = outcome
        self._set_rendering(False)
        if isinstance(result, FrameSession):
            self._open_review(result)
            return
        video = result.video if isinstance(result, ProgramResult) else result
        if not job.preview:
            self.refresh_renders(select=video)
        self.preview.play(video)
        if isinstance(result, ProgramResult):
            info = result.info
            saved = "Saved" if not job.preview else "Ran"
            self.statusBar().showMessage(
                f"{saved}: the program ran in {result.seconds:.1f} s · {info.width}x"
                f"{info.height} · {info.duration:g} s · {video}"
            )
            return
        if isinstance(job, ReviewRenderJob):
            count = len(job.session.entries)
            self.statusBar().showMessage(f"Rendered {count} reviewed frames: {video}")
            return
        what = "Preview" if job.preview else "Rendered"
        self.statusBar().showMessage(f"{what}: {video}")

    def _on_render_failed(
        self, outcome: tuple[RenderJob | ProgramJob | FramesJob | ReviewRenderJob, Exception]
    ) -> None:
        job, error = outcome
        self._set_rendering(False)
        message = error.message if isinstance(error, UserFacingError) else repr(error)
        if isinstance(job, FramesJob) and job.source is not None:
            line = error.line if isinstance(error, ScriptRunError) else None
            self._set_problems([Problem("error", f"The program failed: {message}", line)])
            self.editor.set_error_line(line)
        elif isinstance(job, FramesJob):
            failure = Problem("error", f"Generating the frames failed: {message}")
            self._set_problems([*self.problems, failure])
        elif isinstance(job, ProgramJob):
            line = error.line if isinstance(error, ScriptRunError) else None
            self._set_problems([Problem("error", f"The program failed: {message}", line)])
            self.editor.set_error_line(line)
        else:
            self._set_problems([*self.problems, Problem("error", f"Render failed: {message}")])
        self.bottom_tabs.setCurrentWidget(self.problem_list)
        self.statusBar().showMessage("Render failed; see Problems.")

    def _on_render_cancelled(self, job: object) -> None:
        self._set_rendering(False)
        if isinstance(job, FramesJob):
            self.statusBar().showMessage("Generating the frames was cancelled.")
            return
        self.statusBar().showMessage("Render cancelled; no video was written.")

    # ------------------------------------------------------------------ renders list

    def refresh_renders(self, select: Path | None = None) -> None:
        self.render_list.clear()
        for entry in list_renders(self.output_dir):
            item = QListWidgetItem(entry.label)
            item.setData(_PATH_ROLE, str(entry.video))
            item.setToolTip(str(entry.video))
            self.render_list.addItem(item)
            if select is not None and entry.video == select:
                self.render_list.setCurrentItem(item)

    def _on_render_activated(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        video = Path(str(item.data(_PATH_ROLE)))
        if video.is_file():
            self.preview.play(video)

    def show_output_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def choose_output_dir(self) -> None:
        name = QFileDialog.getExistingDirectory(self, "Output folder", str(self.output_dir))
        if name:
            self.set_output_dir(Path(name))

    def set_output_dir(self, folder: Path) -> None:
        self.output_dir = folder
        self.settings.setValue("output_dir", str(folder))
        self.refresh_renders()

    # ------------------------------------------------------------------ help

    def show_trust_notice(self) -> None:
        QMessageBox.information(
            self,
            "Running scripts",
            f"{config.TRUST_NOTICE}\n\nCodeMotion runs each script in a separate process with "
            f"a {config.SCRIPT_TIMEOUT_SECONDS:g} second limit (video programs, which render "
            f"their own frames: {config.PROGRAM_TIMEOUT_SECONDS / 60:g} minutes), but that "
            "isn't a sandbox: a script can do anything a normal Python file can.",
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {config.APP_NAME}",
            f"{config.APP_NAME} {__version__}\n\nTurns Python video scripts into short "
            "motion-graphic videos. Everything runs offline on this computer.",
        )

    def _error_box(self, message: str) -> None:
        QMessageBox.warning(self, config.APP_NAME, message)

    # ------------------------------------------------------------------ settings & closing

    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        for key, splitter in self._splitters().items():
            state = self.settings.value(key)
            if isinstance(state, QByteArray):
                splitter.restoreState(state)
        auto = self.settings.value("auto_run", True)
        self.auto_run_action.setChecked(auto in (True, "true"))
        self.gif_action.setChecked(self.settings.value("export_gif", False) in (True, "true"))
        self.webm_action.setChecked(self.settings.value("export_webm", False) in (True, "true"))
        self.refresh_renders()

    def save_settings(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        for key, splitter in self._splitters().items():
            self.settings.setValue(key, splitter.saveState())
        self.settings.setValue("auto_run", self.auto_run_action.isChecked())
        self.settings.setValue("export_gif", self.gif_action.isChecked())
        self.settings.setValue("export_webm", self.webm_action.isChecked())
        self.settings.sync()

    def _splitters(self) -> dict[str, QSplitter]:
        return {
            "split_main": self.main_split,
            "split_left": self.left_split,
            "split_right": self.right_split,
        }

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.maybe_save():
            event.ignore()
            return
        if self.review_dialog is not None:
            self.review_dialog.reject()  # its edits are already saved
        if self.renderer.busy:
            self.renderer.cancel()
            self.renderer.wait(10)
        self.auto_timer.stop()
        self.runner.wait(config.SCRIPT_TIMEOUT_SECONDS + 2)
        self.stills.wait(5)
        self.preview.release()
        self.save_settings()
        shutil.rmtree(self._preview_dir, ignore_errors=True)
        event.accept()
