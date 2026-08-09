"""Minimal MATLAB lexer: strips comments and extracts string literals.

Pure stdlib. Not a full parser -- it only needs to be accurate enough to tell
code from comments and to pull out quoted literals, so that the convention
checks in check_conventions.py do not fire on commented-out example code.

The hard case in MATLAB is the single quote, which is both the string delimiter
and the transpose operator. We use the standard heuristic: a quote directly
following an identifier, digit, closing bracket, '.', or another transpose is
the transpose operator; otherwise it opens a string literal.
"""

from __future__ import annotations

from dataclasses import dataclass

# Characters that, when immediately preceding a quote, make it a transpose.
_TRANSPOSE_PREDECESSORS = set("_)]}.'")


@dataclass(frozen=True)
class StringLiteral:
    """A quoted literal found in code (not in a comment)."""

    line: int  # 1-indexed
    col: int  # 1-indexed, position of the opening quote
    value: str  # literal contents, escapes already collapsed
    quote: str  # "'" or '"'


@dataclass(frozen=True)
class LexResult:
    code_lines: list[str]  # same length as input; comments blanked to spaces
    strings: list[StringLiteral]

    def code_text(self) -> str:
        return "\n".join(self.code_lines)


def _is_transpose(prev: str) -> bool:
    if not prev:
        return False
    return prev.isalnum() or prev in _TRANSPOSE_PREDECESSORS


def lex(text: str) -> LexResult:
    """Blank out comments and collect string literals from MATLAB source."""
    code_lines: list[str] = []
    strings: list[StringLiteral] = []
    in_block_comment = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()

        # Block comment delimiters must be alone on their line.
        if in_block_comment:
            code_lines.append(" " * len(raw))
            if stripped == "%}":
                in_block_comment = False
            continue
        if stripped == "%{":
            in_block_comment = True
            code_lines.append(" " * len(raw))
            continue

        out = list(raw)
        i = 0
        n = len(raw)
        prev_significant = ""

        while i < n:
            ch = raw[i]

            if ch == "%":
                # Line comment: blank from here to end of line.
                for j in range(i, n):
                    out[j] = " "
                break

            if ch == "." and raw.startswith("...", i):
                # Line continuation: everything after it is a comment.
                for j in range(i + 3, n):
                    out[j] = " "
                i = n
                break

            if ch == '"' or (ch == "'" and not _is_transpose(prev_significant)):
                quote = ch
                start = i
                i += 1
                buf: list[str] = []
                closed = False
                while i < n:
                    if raw[i] == quote:
                        if i + 1 < n and raw[i + 1] == quote:
                            buf.append(quote)  # doubled quote = escaped quote
                            i += 2
                            continue
                        i += 1
                        closed = True
                        break
                    buf.append(raw[i])
                    i += 1
                strings.append(
                    StringLiteral(
                        line=lineno, col=start + 1, value="".join(buf), quote=quote
                    )
                )
                if not closed:
                    # Unterminated literal; treat rest of line as consumed.
                    break
                prev_significant = quote
                continue

            if not ch.isspace():
                prev_significant = ch
            i += 1

        code_lines.append("".join(out))

    return LexResult(code_lines=code_lines, strings=strings)
