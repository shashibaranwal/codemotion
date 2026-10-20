"""Pygments tokenization into lines of (text, kind) tokens.

The layer is language-agnostic: pass any Pygments lexer alias. ``kind`` is the dotted
Pygments token type without the ``Token.`` prefix, e.g. ``"Literal.String.Double"``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pygments.lexers import get_lexer_by_name


@dataclass(frozen=True)
class Token:
    text: str
    kind: str


@dataclass(frozen=True)
class Line:
    tokens: tuple[Token, ...]

    @property
    def text(self) -> str:
        return "".join(t.text for t in self.tokens)


def tokenize(code: str, language: str = "python") -> list[Line]:
    """Split normalized code into lines of tokens.

    ``"\\n".join(line.text for line in result) == code`` always holds.
    """
    lexer = get_lexer_by_name(language, stripnl=False, stripall=False, ensurenl=False)
    lines: list[list[Token]] = [[]]
    for ttype, value in lexer.get_tokens(code):
        kind = ".".join(ttype)
        for i, part in enumerate(value.split("\n")):
            if i > 0:
                lines.append([])
            if part:
                lines[-1].append(Token(part, kind))
    return [Line(tuple(tokens)) for tokens in lines]
