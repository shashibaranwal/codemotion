from __future__ import annotations

from pathlib import Path

import pytest

from codemotion.models import parse_request
from codemotion.pipeline import PreparedScene, prepare_scene
from codemotion.script.showcase import showcase_doc

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "examples"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SNIPPETS_DIR = FIXTURES_DIR / "snippets"
GOLDEN_DIR = FIXTURES_DIR / "golden"

EXAMPLES = sorted(EXAMPLES_DIR.glob("*.py"))
SNIPPETS = sorted(SNIPPETS_DIR.glob("*.py"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="Rewrite golden-frame PNGs from current output.",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


def read_source(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


SCRIPTS_DIR = EXAMPLES_DIR / "scripts"
SCRIPTS = sorted(SCRIPTS_DIR.glob("*.py"))


def code_scene(code: str, preview: bool = True, **settings: object) -> PreparedScene:
    """A plain code snippet prepared as its showcase scene (``duration`` defaults to 8)."""
    request = parse_request({"code": code, "duration": 8, **settings})
    doc, _ = showcase_doc(request)
    return prepare_scene(doc, preview)
