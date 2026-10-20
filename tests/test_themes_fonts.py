from __future__ import annotations

from pathlib import Path

import pytest

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.geometry import Color
from codemotion.layout.fonts import FontRole, measure, metrics, replace_missing_glyphs, typeface
from codemotion.themes.loader import list_themes, load_theme, load_theme_file

MIDNIGHT = config.BUNDLED_THEMES_DIR / "midnight.toml"

# --------------------------------------------------------------------------- colours


def test_color_from_hex() -> None:
    assert Color.from_hex("#FF8000") == Color(255, 128, 0, 255)
    assert Color.from_hex("#FFFFFF14") == Color(255, 255, 255, 20)


@pytest.mark.parametrize("bad", ["FF8000", "#FFF", "#GG0000", "#12345", "red"])
def test_color_rejects_bad_hex(bad: str) -> None:
    with pytest.raises(ValueError):
        Color.from_hex(bad)


def test_color_with_alpha() -> None:
    assert Color(10, 20, 30, 200).with_alpha(0.5) == Color(10, 20, 30, 100)


# --------------------------------------------------------------------------- themes


def test_all_bundled_themes_load() -> None:
    files = sorted(config.BUNDLED_THEMES_DIR.glob("*.toml"))
    assert files, "no bundled themes"
    for path in files:
        theme = load_theme_file(path)
        assert theme.id == path.stem
        assert theme.name


def test_midnight_values() -> None:
    theme = load_theme("midnight")
    assert theme.name == "Midnight"
    assert theme.card.border == Color(255, 255, 255, 0x14)
    assert len(theme.chrome.dots) == 3


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("Keyword", "#C792EA"),
        ("Keyword.Namespace", "#C792EA"),  # falls back to parent
        ("Literal.String.Double", "#C3E88D"),  # "String" alias resolves to Literal.String
        ("Literal.Number.Integer", "#F78C6C"),
        ("Name.Function", "#82AAFF"),
        ("Name", "#E6E9F2"),  # no entry -> text.default
        ("", "#E6E9F2"),
    ],
)
def test_token_color_fallback(kind: str, expected: str) -> None:
    assert load_theme("midnight").token_color(kind) == Color.from_hex(expected)


def test_unknown_theme_is_friendly() -> None:
    with pytest.raises(UserFacingError, match=r"Unknown theme 'nope'. Available: .*midnight"):
        load_theme("nope")


def _write_theme(path: Path, transform: str = "", replace: tuple[str, str] | None = None) -> Path:
    text = MIDNIGHT.read_text(encoding="utf-8")
    if replace:
        text = text.replace(*replace)
    path.write_text(text + transform, encoding="utf-8")
    return path


def test_invalid_color_rejected(tmp_path: Path) -> None:
    path = _write_theme(tmp_path / "bad.toml", replace=('fill = "#11162A"', 'fill = "navy"'))
    with pytest.raises(UserFacingError, match=r"bad.toml' is invalid at 'card.fill'"):
        load_theme_file(path)


def test_missing_section_rejected(tmp_path: Path) -> None:
    path = _write_theme(tmp_path / "bad.toml", replace=("[accent]\ncolor", "[accent]\ncolour"))
    with pytest.raises(UserFacingError, match="is invalid"):
        load_theme_file(path)


def test_broken_toml_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("name = ", encoding="utf-8")
    with pytest.raises(UserFacingError, match="could not be read"):
        load_theme_file(path)


def test_user_themes_dir_is_scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "USER_THEMES_DIR", tmp_path)
    _write_theme(tmp_path / "mine.toml", replace=('name = "Midnight"', 'name = "Mine"'))
    (tmp_path / "broken.toml").write_text("nope", encoding="utf-8")
    ids = {t.id for t in list_themes()}
    assert {"midnight", "mine"} <= ids
    assert "broken" not in ids
    assert load_theme("mine").name == "Mine"


# --------------------------------------------------------------------------- fonts


def test_fonts_and_licenses_are_bundled() -> None:
    for role in FontRole:
        assert typeface(role) is not None
    licenses = {p.name for p in config.FONTS_DIR.glob("OFL*.txt")}
    assert licenses == {"OFL-JetBrainsMono.txt", "OFL-Inter.txt"}


def test_mono_metrics() -> None:
    m = metrics(FontRole.mono)
    assert m.advance == pytest.approx(0.6)
    assert 0.8 < m.ascent < 1.2
    assert 0.1 < m.descent < 0.5


def test_mono_measure_is_linear() -> None:
    assert measure("abcd", FontRole.mono, 40) == pytest.approx(4 * 0.6 * 40)
    assert measure("ab", FontRole.mono, 26.4) == pytest.approx(
        2 * measure("a", FontRole.mono, 26.4)
    )


def test_missing_glyphs_replaced() -> None:
    text, replaced = replace_missing_glyphs("héllo 😀 中", FontRole.mono)
    assert text == f"héllo {config.MISSING_GLYPH_REPLACEMENT} {config.MISSING_GLYPH_REPLACEMENT}"
    assert replaced == 2


def test_no_replacement_for_plain_code() -> None:
    assert replace_missing_glyphs("x = [1, 2]  # ok", FontRole.mono) == ("x = [1, 2]  # ok", 0)
