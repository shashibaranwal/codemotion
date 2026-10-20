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

    Raises ``UserFacingError`` if the result is empty or exceeds the input limits.
    """
    code = code.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in code:
        raise UserFacingError("The code contains a null character; is it a binary file?", "code")
    lines = [line.replace("\t", " " * config.TAB_SIZE).rstrip() for line in code.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        raise UserFacingError("Paste some Python code first.", field="code")

    text = "\n".join(lines)
    if len(text) > config.MAX_TOTAL_CHARS:
        raise UserFacingError(
            f"Your code has {len(text):,} characters; the maximum is {config.MAX_TOTAL_CHARS:,}.",
            field="code",
        )
    if len(lines) > config.MAX_LINES:
        raise UserFacingError(
            f"Your code has {len(lines)} lines; the maximum is {config.MAX_LINES}.", field="code"
        )
    for number, line in enumerate(lines, start=1):
        if len(line) > config.MAX_LINE_LENGTH:
            raise UserFacingError(
                f"Line {number} is {len(line)} characters long; "
                f"the maximum is {config.MAX_LINE_LENGTH}.",
                field="code",
            )
    return text
