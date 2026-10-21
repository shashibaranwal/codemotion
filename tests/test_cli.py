from __future__ import annotations

import json
from pathlib import Path

import pytest
import skia
from conftest import EXAMPLES_DIR, SNIPPETS_DIR
from typer.testing import CliRunner

from codemotion.cli import app

runner = CliRunner()


def test_still_writes_png(tmp_path: Path) -> None:
    out = tmp_path / "sub" / "frame.png"
    result = runner.invoke(
        app,
        ["still", str(EXAMPLES_DIR / "fib.py"), "--duration", "8", "--t", "4.2", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    image = skia.Image.open(str(out))
    assert (image.width(), image.height()) == (1080, 1920)


def test_still_aspect_and_stdin(tmp_path: Path) -> None:
    out = tmp_path / "frame.png"
    result = runner.invoke(
        app,
        ["still", "-", "--duration", "5", "--aspect", "16:9", "-o", str(out)],
        input="print('hi')\n",
    )
    assert result.exit_code == 0, result.output
    assert skia.Image.open(str(out)).width() == 1920


def test_still_prints_warnings(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "still",
            str(SNIPPETS_DIR / "broken.py"),
            "--duration",
            "5",
            "-o",
            str(tmp_path / "f.png"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Syntax warning" in result.output


def test_missing_duration_with_stdin_is_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["still", "-", "-o", str(tmp_path / "f.png")], input="x = 1\n")
    assert result.exit_code == 1
    assert "Duration is required" in result.output


def test_bad_duration_is_friendly(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "still",
            str(EXAMPLES_DIR / "hello.py"),
            "--duration",
            "20",
            "-o",
            str(tmp_path / "f.png"),
        ],
    )
    assert result.exit_code == 1
    assert "between 3 and 12 seconds" in result.output
    assert "Traceback" not in result.output


def test_missing_file_is_friendly(tmp_path: Path) -> None:
    result = runner.invoke(app, ["still", str(tmp_path / "nope.py"), "--duration", "8"])
    assert result.exit_code == 1
    assert "File not found" in result.output


def test_too_wide_line_wraps_with_a_warning(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["still", "-", "--duration", "8", "-o", str(tmp_path / "f.png")], input="x" * 80 + "\n"
    )
    assert result.exit_code == 0
    assert "they wrap" in result.output


def test_themes_lists_midnight() -> None:
    result = runner.invoke(app, ["themes"])
    assert result.exit_code == 0
    assert "midnight" in result.output


def test_app_opens_the_desktop_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import codemotion.desktop.app as desktop

    opened: list[Path | None] = []

    def fake_run(script: Path | None = None) -> int:
        opened.append(script)
        return 0

    monkeypatch.setattr(desktop, "run", fake_run)
    script = tmp_path / "video.py"
    script.write_bytes(b"x = 1\n")
    assert runner.invoke(app, ["app", str(script)]).exit_code == 0
    assert runner.invoke(app, ["app"]).exit_code == 0
    assert opened == [script, None]


def test_app_rejects_a_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["app", str(tmp_path / "missing.py")])
    assert result.exit_code == 1
    assert "File not found" in result.output


def test_plan_json_escapes_non_ascii_text(tmp_path: Path) -> None:
    """Piped output on Windows uses the ANSI code page; JSON must not depend on it."""
    script = tmp_path / "video.py"
    script.write_text(
        "from codemotion import Text, Video\nvideo = Video(duration=3)\n"
        "video.add(Text('\u20b9 \u2192 \u20ac'))\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["plan", str(script), "--json"])
    assert result.exit_code == 0, result.output
    assert result.output.isascii()
    assert json.loads(result.output)["scene"]["elements"][0]["text"] == "\u20b9 \u2192 \u20ac"
