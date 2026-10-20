"""Load and validate TOML themes from the bundled folder and ``~/.codemotion/themes/``.

A theme's id is its file stem (``midnight.toml`` -> ``"midnight"``); user themes with the
same id override bundled ones.
"""

from __future__ import annotations

import tomllib
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from pygments.token import string_to_tokentype

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.geometry import Color


def _parse_color(value: Any) -> Color:
    if isinstance(value, Color):
        return value
    if not isinstance(value, str):
        raise ValueError("colours must be strings like '#RRGGBB'")
    return Color.from_hex(value)


ThemeColor = Annotated[Color, BeforeValidator(_parse_color)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackgroundColors(_Section):
    gradient: tuple[ThemeColor, ThemeColor]
    blobs: tuple[ThemeColor, ThemeColor]


class CardColors(_Section):
    fill: ThemeColor
    border: ThemeColor
    shadow: ThemeColor


class ChromeColors(_Section):
    dots: tuple[ThemeColor, ThemeColor, ThemeColor]
    tab_text: ThemeColor


class TextColors(_Section):
    default: ThemeColor
    gutter: ThemeColor
    cursor: ThemeColor


class AccentColors(_Section):
    color: ThemeColor


class TerminalColors(_Section):
    fill: ThemeColor
    prompt: ThemeColor
    text: ThemeColor


class Theme(_Section):
    id: str
    name: str
    background: BackgroundColors
    card: CardColors
    chrome: ChromeColors
    text: TextColors
    accent: AccentColors
    tokens: dict[str, ThemeColor] = Field(default_factory=dict)
    terminal: TerminalColors

    @cached_property
    def _token_colors(self) -> dict[str, Color]:
        # Resolve aliases ("String" is Pygments' Literal.String) to canonical dotted kinds.
        return {".".join(string_to_tokentype(key)): color for key, color in self.tokens.items()}

    def named_color(self, name: str) -> Color:
        """A script colour: a theme name (text, accent, muted, card, gutter, background) or hex."""
        named = {
            "text": self.text.default,
            "accent": self.accent.color,
            "muted": self.chrome.tab_text,
            "card": self.card.fill,
            "gutter": self.text.gutter,
            "background": self.background.gradient[0],
        }
        return named[name] if name in named else Color.from_hex(name)

    def token_color(self, kind: str) -> Color:
        """Colour for a token kind, falling back to parent types, then ``text.default``."""
        parts = kind.split(".") if kind else []
        while parts:
            color = self._token_colors.get(".".join(parts))
            if color is not None:
                return color
            parts.pop()
        return self.text.default


def _theme_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for folder in (config.BUNDLED_THEMES_DIR, config.USER_THEMES_DIR):
        if folder.is_dir():
            for path in sorted(folder.glob("*.toml")):
                files[path.stem] = path
    return files


def load_theme_file(path: Path) -> Theme:
    """Parse and validate one theme file; raises ``UserFacingError`` with the problem."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return Theme.model_validate({"id": path.stem, **data})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UserFacingError(
            f"Theme file '{path.name}' could not be read: {exc}", "theme"
        ) from None
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise UserFacingError(
            f"Theme file '{path.name}' is invalid at '{where}': {first['msg']}", "theme"
        ) from None


def list_themes() -> list[Theme]:
    """All themes that load and validate; broken user theme files are skipped."""
    themes = []
    for path in _theme_files().values():
        try:
            themes.append(load_theme_file(path))
        except UserFacingError:
            continue
    return themes


def load_theme(theme_id: str) -> Theme:
    files = _theme_files()
    path = files.get(theme_id)
    if path is None:
        available = ", ".join(sorted(files)) or "none"
        raise UserFacingError(f"Unknown theme '{theme_id}'. Available: {available}.", "theme")
    return load_theme_file(path)
