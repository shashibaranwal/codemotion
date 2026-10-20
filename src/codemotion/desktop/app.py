"""Desktop app entry point: ``codemotion app [SCRIPT]``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from codemotion import config
from codemotion.desktop.main_window import MainWindow, app_settings


def register_fonts() -> None:
    """Make the bundled fonts available to Qt (editor: JetBrains Mono; UI: Inter)."""
    for font in (config.MONO_FONT_FILE, config.MONO_BOLD_FONT_FILE, config.SANS_FONT_FILE,
                 config.SANS_SEMIBOLD_FONT_FILE):  # fmt: skip
        QFontDatabase.addApplicationFont(str(font))


def dark_palette() -> QPalette:
    c = {name: QColor(value) for name, value in config.UI_COLORS.items()}
    palette = QPalette()
    role = QPalette.ColorRole
    for r, color in (
        (role.Window, c["window"]),
        (role.WindowText, c["text"]),
        (role.Base, c["base"]),
        (role.AlternateBase, c["alternate"]),
        (role.ToolTipBase, c["window"]),
        (role.ToolTipText, c["text"]),
        (role.Text, c["text"]),
        (role.PlaceholderText, c["muted"]),
        (role.Button, c["button"]),
        (role.ButtonText, c["text"]),
        (role.Highlight, c["highlight"]),
        (role.HighlightedText, c["highlighted_text"]),
        (role.Link, c["highlight"]),
    ):
        palette.setColor(r, color)
    for r in (role.Text, role.ButtonText, role.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, r, c["muted"])
    return palette


def create_app(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName(config.APP_NAME)
    app.setOrganizationName(config.APP_NAME)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setWindowIcon(QIcon(str(config.ICON_FILE)))
    register_fonts()
    return app


def show_first_run_notice(window: MainWindow) -> None:
    settings = window.settings
    if settings.value("trust_notice_shown") in (True, "true"):
        return
    QMessageBox.information(window, f"Welcome to {config.APP_NAME}", config.TRUST_NOTICE)
    settings.setValue("trust_notice_shown", True)


def run(script: Path | None = None) -> int:
    """Open the desktop app (optionally with ``script``) and block until it closes."""
    app = create_app()
    window = MainWindow(app_settings())
    window.show()
    show_first_run_notice(window)
    if script is not None:
        window.open_path(script)
    return app.exec()
