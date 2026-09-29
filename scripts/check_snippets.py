#!/usr/bin/env python3
"""Check that every Python and YAML block in the repository's Markdown parses.

Indentation is syntax in both languages, so a line shifted one level in
a snippet is an error, not a matter of style. A field that sits one
level too deep in a class reads as a nested block, and a YAML key that
sits too deep moves under another parent or fails. Other languages are
not read, since their indentation is style.

A block is a run of fenced lines, as `fenced_lines` reads them.
markdownlint keeps a blank line around every fence, so two blocks never
touch. A block's language is the first word of its info string. A block
inside a list item is dedented before it is parsed. In Python, `...`
stands for elided code and parses as an expression, and `await` may
stand at the top level. The files are `markdown_files`, the one list
every script reads.

Exit status is non-zero on any block that does not parse. Needs PyYAML,
which `make snippets` brings at its pin.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from collections.abc import Iterator, Sequence
from pathlib import Path

import yaml

from _common import FENCE, ROOT, arguments, fenced_lines, markdown_files

PYTHON = {"python", "py", "python3"}
YAML = {"yaml", "yml"}


def blocks(text: str) -> Iterator[tuple[int, str, str]]:
    """Each fenced block of `text`: the line number of its opening fence, its language, and its body."""
    lines = text.split("\n")
    code = fenced_lines(text)
    i = 0
    while i < len(lines):
        if not code[i]:
            i += 1
            continue
        end = i
        while end + 1 < len(lines) and code[end + 1]:
            end += 1
        m = FENCE.match(lines[i])
        info = lines[i][m.end() :].split() if m else []
        closed = end > i and FENCE.match(lines[end]) is not None
        body = lines[i + 1 : end if closed else end + 1]
        yield i + 1, (info[0].lower() if info else ""), textwrap.dedent("\n".join(body))
        i = end + 1


def error(language: str, body: str) -> tuple[int, str] | None:
    """Where a block fails to parse, as a line of its body and a message; None when it parses."""
    if language in PYTHON:
        try:
            compile(body, "<snippet>", "exec", flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        except SyntaxError as e:
            return e.lineno or 1, e.msg
    elif language in YAML:
        try:
            list(yaml.safe_load_all(body))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            return (mark.line + 1 if mark is not None else 1), str(getattr(e, "problem", None) or e)
    return None


def check(path: Path) -> tuple[int, list[str]]:
    """How many Python and YAML blocks `path` holds, and a line for each that does not parse."""
    count = 0
    errors: list[str] = []
    for start, language, body in blocks(path.read_text(encoding="utf-8")):
        if language not in PYTHON | YAML:
            continue
        count += 1
        found = error(language, body)
        if found is not None:
            line, message = found
            errors.append(f"{path.relative_to(ROOT)}:{start + line}: this {language} block does not parse: {message}")
    return count, errors


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    total = 0
    errors: list[str] = []
    for path in markdown_files(ROOT):
        count, found = check(path)
        total += count
        errors += found
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} block(s) that do not parse")
        return 1
    print(f"snippets ok: {total} Python and YAML block(s) parse")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
