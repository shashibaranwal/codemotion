"""Dialogs: code-card settings for plain code files (the v1 shortcut)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QWidget,
)

from codemotion import config
from codemotion.models import Aspect, Style
from codemotion.themes.loader import list_themes

STYLE_LABELS = {
    Style.typewriter: "Typewriter",
    Style.line_cascade: "Line cascade",
    Style.fade_in: "Fade in",
}


def default_code_options() -> dict[str, Any]:
    return {
        "duration": config.DURATION_DEFAULT,
        "aspect": Aspect.portrait.value,
        "fps": 30,
        "style": Style.typewriter.value,
        "theme": config.DEFAULT_THEME,
        "outro": "hold",
    }


class CodeSettingsDialog(QDialog):
    """Settings for showing a plain code file as a code card."""

    def __init__(
        self, options: dict[str, Any] | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Code card settings")
        values = {**default_code_options(), **(options or {})}

        self.duration = QDoubleSpinBox()
        self.duration.setRange(config.DURATION_MIN, config.DURATION_MAX)
        self.duration.setSingleStep(config.DURATION_STEP)
        self.duration.setDecimals(1)
        self.duration.setSuffix(" s")
        self.duration.setValue(float(values["duration"]))
        self.aspect = self._combo([(a.value, a.value) for a in Aspect], values["aspect"])
        self.fps = self._combo([("30", 30), ("60", 60)], values["fps"])
        self.reveal = self._combo([(STYLE_LABELS[s], s.value) for s in Style], values["style"])
        themes = [(theme.name, theme.id) for theme in list_themes()]
        self.theme = self._combo(themes, values["theme"])
        self.outro = self._combo([("Hold", "hold"), ("Fade out", "fade")], values["outro"])

        form = QFormLayout(self)
        note = QLabel("This file doesn't import codemotion, so it's shown as a code card.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow("Duration", self.duration)
        form.addRow("Format", self.aspect)
        form.addRow("Frame rate", self.fps)
        form.addRow("Reveal", self.reveal)
        form.addRow("Theme", self.theme)
        form.addRow("Ending", self.outro)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _combo(items: list[tuple[str, Any]], current: Any) -> QComboBox:
        combo = QComboBox()
        for label, value in items:
            combo.addItem(label, value)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    def options(self) -> dict[str, Any]:
        # Snap to the allowed step, so typed values like 7.3 don't fail validation.
        step = config.DURATION_STEP
        return {
            "duration": round(self.duration.value() / step) * step,
            "aspect": self.aspect.currentData(),
            "fps": self.fps.currentData(),
            "style": self.reveal.currentData(),
            "theme": self.theme.currentData(),
            "outro": self.outro.currentData(),
        }
