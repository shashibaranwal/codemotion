from __future__ import annotations

from pathlib import Path

import pytest
from conftest import EXAMPLES, SNIPPETS, read_source

from codemotion import config
from codemotion.analysis.normalize import decode, normalize
from codemotion.analysis.tokenize import tokenize
from codemotion.analysis.validate import check_syntax
from codemotion.errors import UserFacingError

# --------------------------------------------------------------------------- normalize


def test_tabs_become_four_spaces() -> None:
    assert normalize("def f():\n\treturn 1") == "def f():\n    return 1"


def test_trailing_whitespace_and_blank_lines_stripped() -> None:
    assert normalize("a = 1   \nb = 2\t\n\n  \n") == "a = 1\nb = 2"


def test_crlf_and_bom_normalized() -> None:
    assert normalize("﻿a = 1\r\nb = 2\r\n") == "a = 1\nb = 2"


def test_inner_blank_lines_kept() -> None:
    assert normalize("a\n\n\nb") == "a\n\n\nb"


@pytest.mark.parametrize("code", ["", "   ", "\n\n\t\n"])
def test_empty_code_rejected(code: str) -> None:
    with pytest.raises(UserFacingError, match="Paste some Python code first"):
        normalize(code)


def test_line_count_is_unlimited() -> None:
    code = "\n".join(f"value_{i} = {i}" for i in range(5_000))
    assert normalize(code).count("\n") == 4_999


def test_line_length_is_unlimited() -> None:
    code = "a = 1\n" + "b" * 500
    assert normalize(code) == code


def test_tabs_expand_to_spaces() -> None:
    assert normalize("\tx") == " " * config.TAB_SIZE + "x"


def test_total_size_is_unlimited() -> None:
    code = "\n".join(["x" * 100] * 2_000)
    assert len(normalize(code)) == len(code)


def test_comments_only_is_allowed() -> None:
    assert normalize("# just a note") == "# just a note"


def test_decode_rejects_non_utf8() -> None:
    with pytest.raises(UserFacingError, match="not valid UTF-8"):
        decode("caf\xe9".encode("latin-1"))


def test_decode_strips_bom() -> None:
    assert decode("﻿x = 1".encode()) == "x = 1"


def test_null_bytes_rejected() -> None:
    with pytest.raises(UserFacingError, match="null character"):
        normalize("a = 1\x00")


# --------------------------------------------------------------------------- validate


def test_valid_code_has_no_warnings() -> None:
    assert check_syntax("def f():\n    return 1") == []


def test_syntax_error_is_a_warning_with_position() -> None:
    [warning] = check_syntax("x = 1\nif x\n    pass")
    assert warning.line == 2
    assert warning.column is not None
    assert "line 2" in warning.message
    assert "will still be made" in warning.message


def test_partial_snippet_warns_but_does_not_raise() -> None:
    assert len(check_syntax("def broken(:\n    return [1, 2")) == 1


# --------------------------------------------------------------------------- tokenize


@pytest.mark.parametrize("path", EXAMPLES + SNIPPETS, ids=lambda p: Path(p).name)
def test_tokens_reproduce_normalized_source(path: Path) -> None:
    code = normalize(read_source(path))
    lines = tokenize(code)
    assert "\n".join(line.text for line in lines) == code
    assert len(lines) == code.count("\n") + 1
    assert all("\n" not in t.text for line in lines for t in line.tokens)


def test_token_kinds_have_no_token_prefix() -> None:
    lines = tokenize('def f():\n    return "s"')
    kinds = {t.kind for line in lines for t in line.tokens}
    assert "Keyword" in kinds
    assert "Name.Function" in kinds
    assert any(k.startswith("Literal.String") for k in kinds)
    assert not any(k.startswith("Token") for k in kinds)


def test_multiline_string_split_across_lines() -> None:
    lines = tokenize('s = """a\nb"""')
    assert lines[1].text == 'b"""'
    assert lines[1].tokens[0].kind.startswith("Literal.String")


def test_examples_have_expected_line_counts() -> None:
    counts = {p.name: len(tokenize(normalize(read_source(p)))) for p in EXAMPLES}
    assert counts == {
        "dataclass.py": 25,
        "fastapi_app.py": 20,
        "fib.py": 10,
        "hello.py": 3,
        "long_script.py": 55,
    }
