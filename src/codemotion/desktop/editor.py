"""Script editor: Python highlighting, line numbers, error-line marks and auto-indent."""

from __future__ import annotations

import builtins
import keyword
import re
from pathlib import Path

from PySide6.QtCore import QMimeData, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from codemotion import config

_API_NAMES = ("Video", "Text", "Code", "Image", "Shape", "Line", "Terminal", "Group", "ease",
              "stagger")  # fmt: skip
_WORDS = re.compile(r"\b[A-Za-z_]\w*\b")
_NUMBER = re.compile(r"\b(?:0[xXoObB][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?j?)\b")
_DEFINITION = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")
_DECORATOR = re.compile(r"^\s*@[\w.]+")
_PREFIX = r"[rRbBfFuU]{0,2}"
_TOKEN = re.compile(
    rf"(?P<triple>{_PREFIX}(?:\"\"\"|'''))"
    rf"|(?P<string>{_PREFIX}(?:\"(?:[^\"\\\n]|\\.)*\"?|'(?:[^'\\\n]|\\.)*'?))"
    r"|(?P<comment>#.*)"
)
_IN_TRIPLE = {'"""': 1, "'''": 2}  # block states: inside a triple-quoted string


def _format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    fmt.setFontItalic(italic)
    return fmt


def _utf16_index(text: str) -> list[int]:
    """Qt positions count UTF-16 units; map each Python index to one (characters > U+FFFF)."""
    index = [0]
    for ch in text:
        index.append(index[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return index


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        colors = config.EDITOR_COLORS
        self.formats = {
            "keyword": _format(colors["keyword"], bold=True),
            "builtin": _format(colors["builtin"]),
            "api": _format(colors["definition"], bold=True),
            "number": _format(colors["number"]),
            "string": _format(colors["string"]),
            "comment": _format(colors["comment"], italic=True),
            "definition": _format(colors["definition"]),
            "decorator": _format(colors["decorator"]),
        }
        self.keywords = set(keyword.kwlist) | set(keyword.softkwlist)
        self.builtins = {name for name in dir(builtins) if not name.startswith("_")}

    def highlightBlock(self, text: str) -> None:
        wide = _utf16_index(text) if any(ord(ch) > 0xFFFF for ch in text) else None

        def paint(start: int, end: int, kind: str) -> None:
            if wide is not None:
                start, end = wide[start], wide[end]
            self.setFormat(start, end - start, self.formats[kind])

        self.setCurrentBlockState(0)
        pos = 0
        previous = self.previousBlockState()
        for delim, state in _IN_TRIPLE.items():
            if previous == state:
                close = text.find(delim)
                if close < 0:
                    paint(0, len(text), "string")
                    self.setCurrentBlockState(state)
                    return
                pos = close + 3
                paint(0, pos, "string")

        for match in _WORDS.finditer(text, pos):
            word = match.group()
            if word in self.keywords:
                paint(match.start(), match.end(), "keyword")
            elif word in _API_NAMES:
                paint(match.start(), match.end(), "api")
            elif word in self.builtins:
                paint(match.start(), match.end(), "builtin")
        for match in _NUMBER.finditer(text, pos):
            paint(match.start(), match.end(), "number")
        for match in _DEFINITION.finditer(text, pos):
            paint(match.start(1), match.end(1), "definition")
        decorator = _DECORATOR.match(text)
        if decorator and pos == 0:
            paint(decorator.start(), decorator.end(), "decorator")

        # Strings and comments last, so they override words inside them.
        while (token := _TOKEN.search(text, pos)) is not None:
            if token.lastgroup == "triple":
                delim = token.group()[-3:]
                close = text.find(delim, token.end())
                if close < 0:
                    paint(token.start(), len(text), "string")
                    self.setCurrentBlockState(_IN_TRIPLE[delim])
                    return
                paint(token.start(), close + 3, "string")
                pos = close + 3
            else:
                paint(token.start(), token.end(), token.lastgroup or "string")
                pos = token.end()


class _Gutter(QWidget):
    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    """Plain-text Python editor. ``files_dropped`` fires when files are dropped on it."""

    files_dropped = Signal(list)  # list[Path]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.error_line: int | None = None
        self._colors = {k: QColor(v) for k, v in config.EDITOR_COLORS.items()}
        self.setFont(editor_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(
            self.fontMetrics().horizontalAdvance(" ") * len(config.EDITOR_INDENT)
        )
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {config.EDITOR_COLORS['background']};"
            f" color: {config.EDITOR_COLORS['text']}; border: none; }}"
        )
        self.highlighter = PythonHighlighter(self.document())
        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_margins)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._update_selections)
        self._update_margins()
        self._update_selections()

    # -- error marks and navigation ------------------------------------------------------

    def set_error_line(self, line: int | None) -> None:
        """Mark ``line`` (1-based) as the error line, or clear the mark."""
        if line is not None and not 1 <= line <= self.blockCount():
            line = None
        self.error_line = line
        self._update_selections()
        self._gutter.update()

    def go_to_line(self, line: int) -> None:
        block = self.document().findBlockByLineNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    # -- typing --------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Tab and not event.modifiers():
            self.textCursor().insertText(config.EDITOR_INDENT)
            return
        if key == Qt.Key.Key_Backtab:
            self._dedent()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            line = self.textCursor().block().text()[: self.textCursor().positionInBlock()]
            indent = line[: len(line) - len(line.lstrip())]
            if line.rstrip().endswith(":"):
                indent += config.EDITOR_INDENT
            self.textCursor().insertText("\n" + indent)
            self.ensureCursorVisible()
            return
        super().keyPressEvent(event)

    def _dedent(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        text = cursor.block().text()
        spaces = len(text) - len(text.lstrip(" "))
        remove = min(spaces, len(config.EDITOR_INDENT))
        if remove:
            cursor.movePosition(
                QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, remove
            )
            cursor.removeSelectedText()

    # -- dropped files go to the window, not into the text ---------------------------------

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        return source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        paths = [Path(url.toLocalFile()) for url in source.urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        elif not source.hasUrls():
            super().insertFromMimeData(source)

    # -- gutter --------------------------------------------------------------------------

    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))

    def paint_gutter(self, event: QPaintEvent) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._colors["gutter_background"])
        block = self.firstVisibleBlock()
        number = block.blockNumber() + 1
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        width = self._gutter.width() - 8
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                if number == self.error_line:
                    painter.setPen(self._colors["error_line"].lighter(250))
                    painter.setBrush(QColor(config.UI_COLORS["error"]))
                    painter.drawEllipse(4, top + height // 2 - 3, 6, 6)
                    painter.setPen(QColor(config.UI_COLORS["error"]))
                else:
                    painter.setPen(self._colors["gutter"])
                painter.drawText(0, top, width, height, Qt.AlignmentFlag.AlignRight, str(number))
            block = block.next()
            top = bottom
            number += 1
        painter.end()

    def _update_margins(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margins()

    def _update_selections(self) -> None:
        selections = []
        current = QTextEdit.ExtraSelection()
        current.format.setBackground(self._colors["current_line"])
        current.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        current.cursor = self.textCursor()
        current.cursor.clearSelection()
        selections.append(current)
        if self.error_line is not None:
            mark = QTextEdit.ExtraSelection()
            block = self.document().findBlockByLineNumber(self.error_line - 1)
            mark.format.setBackground(self._colors["error_line"])
            mark.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            mark.cursor = QTextCursor(block)
            selections.append(mark)
        self.setExtraSelections(selections)


def editor_font() -> QFont:
    """The bundled JetBrains Mono when registered (see ``app.register_fonts``), else monospace."""
    families = QFontDatabase.families()
    if "JetBrains Mono" in families:
        font = QFont("JetBrains Mono", config.EDITOR_FONT_POINT_SIZE)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(config.EDITOR_FONT_POINT_SIZE)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
