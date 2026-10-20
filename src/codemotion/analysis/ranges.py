"""Parse line-range specs like ``"3-5,9"``."""

from __future__ import annotations

import re

from codemotion.errors import UserFacingError

_PART = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_line_ranges(spec: str, line_count: int, what: str = "Lines") -> frozenset[int]:
    """1-based line numbers named by ``spec``; raises ``UserFacingError`` if invalid."""
    lines: set[int] = set()
    for part in spec.split(","):
        match = _PART.match(part)
        if not match:
            raise UserFacingError(f"{what} must look like '3-5,9', got '{spec}'.", "lines")
        first = int(match[1])
        last = int(match[2] or match[1])
        if first > last:
            first, last = last, first
        if first < 1 or last > line_count:
            raise UserFacingError(
                f"{what} '{spec}' is out of range; the code has {line_count} lines.", "lines"
            )
        lines.update(range(first, last + 1))
    return frozenset(lines)


def contiguous_runs(lines: frozenset[int]) -> list[tuple[int, int]]:
    """Group sorted line numbers into inclusive (first, last) runs."""
    runs: list[tuple[int, int]] = []
    for n in sorted(lines):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs
