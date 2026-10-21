"""Input normalization and limit checks (SPEC 2.2)."""

from __future__ import annotations

from codemotion import config
from codemotion.errors import UserFacingError


def decode(raw: bytes) -> str:
    """Decode file/stdin bytes as UTF-8 (a leading BOM is allowed)."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UserFacingError(
            "The code is not valid UTF-8 text. Save the file as UTF-8 and try again.",
            field="code",
        ) from None


def normalize(code: str) -> str:
    """Return the code with unified newlines, tabs as spaces and trailing whitespace removed.

    Raises ``UserFacingError`` if the result is empty. Size is not limited: code taller
    than its card scrolls and lines wider than it wrap (see ``layout``).
    """
    code = code.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in code:
        raise UserFacingError("The code contains a null character; is it a binary file?", "code")
    lines = [line.replace("\t", " " * config.TAB_SIZE).rstrip() for line in code.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        raise UserFacingError("Paste some Python code first.", field="code")
    return "\n".join(lines)
