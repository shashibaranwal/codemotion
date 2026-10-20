"""Syntax check with ``ast``: problems are warnings, not blockers (partial snippets are fine)."""

from __future__ import annotations

import ast

from codemotion.models import CodeWarning


def check_syntax(code: str) -> list[CodeWarning]:
    try:
        ast.parse(code)
    except SyntaxError as exc:
        where = []
        if exc.lineno is not None:
            where.append(f"line {exc.lineno}")
        if exc.offset is not None:
            where.append(f"column {exc.offset}")
        location = f" ({', '.join(where)})" if where else ""
        return [
            CodeWarning(
                line=exc.lineno,
                column=exc.offset,
                message=f"Syntax warning{location}: {exc.msg}. The video will still be made.",
            )
        ]
    return []
