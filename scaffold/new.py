#!/usr/bin/env python3
"""Copy the scaffold into a new project under its own name.

    python3 scaffold/new.py ~/code/pressroom

The last part of the destination is the name: one or two snake_case words,
such as `pressroom` or `free_press`, of at most 17 characters, the most the
tightest cloud name built from it holds. The copy renames every path and every
text file from `acme` to the name, in each of its forms: the Python package
and the database logins take the snake form (`free_press`), the
distributions, the domains, and the cloud resources the kebab form
(`free-press`), the environment the UPPER prefix (`FREE_PRESS_`), and prose
the Title (`Free Press`). Binary files are copied as they are, and a link
stays a link: `.claude/skills` points at `.agents/skills` in the copy as it
does here. The copy pins the guideline release this checkout carries, starts
a git repository with nothing staged, and prints the next step.

Standard library only, so it runs before anything is installed.
"""

from __future__ import annotations

import json
import keyword
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "acme_root"
PLUGIN = HERE.parent / ".claude-plugin" / "plugin.json"
PLACEHOLDER = "acme"

NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)?$")
"""One or two snake_case words: a Python package, a database login, and a
directory alike."""

TIGHTEST_NAME = "{name}-production-api"
"""The tightest cloud name a copy builds from the name: the API's target group
behind the load balancer, in the longest environment's name."""
TIGHTEST_LIMIT = 32
"""What AWS holds a target group's name to."""
MAX_NAME_LENGTH = TIGHTEST_LIMIT - len(TIGHTEST_NAME.format(name=""))
"""The longest name a copy takes, 17: its tightest cloud name fits, and every
line of the copy stays within its own lint."""

SKIPPED = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".terraform",
        "dist",
        ".env",
    }
)
"""What a copy never carries: what a tool installed, built, or cached in the
scaffold, and local settings."""

PIN = re.compile(r"(swe_guidelines(?:@|/blob/|/tree/)v|pinned at release `v)\d+\.\d+\.\d+")
"""Where a copy names the guideline release it follows."""


@dataclass(frozen=True)
class Names:
    """The forms of one product name."""

    snake: str

    @property
    def kebab(self) -> str:
        return self.snake.replace("_", "-")

    @property
    def upper(self) -> str:
        return self.snake.upper()

    @property
    def title(self) -> str:
        return " ".join(word.capitalize() for word in self.snake.split("_"))

    @property
    def pascal(self) -> str:
        return "".join(word.capitalize() for word in self.snake.split("_"))


def refusal(name: str) -> str | None:
    """Why a name cannot be a product's, or None when it can."""
    if not NAME.match(name):
        return f"{name!r} is not one or two snake_case words, such as pressroom or free_press"
    if len(name) > MAX_NAME_LENGTH:
        tightest = TIGHTEST_NAME.format(name=name.replace("_", "-"))
        return (
            f"{name!r} is longer than {MAX_NAME_LENGTH} characters: the target group "
            f"{tightest} would pass the {TIGHTEST_LIMIT} AWS allows"
        )
    if name in sys.stdlib_module_names or keyword.iskeyword(name):
        return f"{name!r} is a Python module or keyword; the package would shadow it"
    if name == PLACEHOLDER:
        return f"{name!r} is the scaffold's own name; choose the product's"
    return None


TOKEN = re.compile(r"ACME|Acme|acme")
DOMAIN = re.compile(r"\.(?:example|invalid|test|local)\b", re.IGNORECASE)


def rename(text: str, names: Names) -> str:
    """Every form of the placeholder in `text`, each in its own form of the
    name, in one pass, so a name that holds the placeholder is never renamed
    twice. The case follows the placeholder's; the words join by the place:

    - touching a hyphen, or standing in a domain, by a hyphen: a
      distribution (`acme-om`), a resource (`acme-staging`), a header
      (`X-Acme-Edge`), a domain (`api.acme.example`, `Platform.Acme.Invalid`);
    - `Acme` before a letter or a digit, by nothing: an identifier
      (`AcmeReaders`);
    - `Acme` anywhere else, by a space: prose;
    - anywhere else, by an underscore: a package, a login, a path, the
      environment (`ACME_DATABASE_URL`)."""
    words = names.snake.split("_")

    def form(match: re.Match[str]) -> str:
        word, start, end = match.group(0), match.start(), match.end()
        if text[start - 1 : start] == "-" or text[end : end + 1] == "-" or DOMAIN.match(text, end):
            joiner = "-"
        elif word == "Acme":
            joiner = "" if text[end : end + 1].isalnum() else " "
        else:
            joiner = "_"
        if word == "ACME":
            return joiner.join(w.upper() for w in words)
        if word == "Acme":
            return joiner.join(w.capitalize() for w in words)
        return joiner.join(words)

    return TOKEN.sub(form, text)


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def release(plugin: Path = PLUGIN) -> str | None:
    """The guideline release this checkout carries, or None outside one."""
    try:
        return str(json.loads(plugin.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return None


def copy(source: Path, dest: Path, names: Names, version: str | None) -> int:
    """Copies `source` into `dest` under `names`; returns how many files."""
    count = 0
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if any(part in SKIPPED or part.endswith(".pyc") for part in rel.parts):
            continue
        out = dest.joinpath(*(rename(part, names) for part in rel.parts))
        if path.is_symlink():
            # a link is copied as a link, its target renamed like a path, so the copy wires what the scaffold wires
            out.parent.mkdir(parents=True, exist_ok=True)
            out.symlink_to(rename(os.readlink(path), names))
            count += 1
            continue
        if not path.is_file():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        if is_text(data):
            text = rename(data.decode("utf-8"), names)
            if version is not None:
                text = PIN.sub(lambda m: f"{m.group(1)}{version}", text)
            out.write_text(text, encoding="utf-8", newline="")
        else:
            out.write_bytes(data)
        shutil.copymode(path, out)
        count += 1
    return count


def main(argv: list[str] | None = None, source: Path = SOURCE, plugin: Path = PLUGIN) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0].startswith("-"):
        print(
            "usage: python3 scaffold/new.py <dir>/<name>\n"
            f"  <name>: one or two snake_case words, at most {MAX_NAME_LENGTH} characters",
            file=sys.stderr,
        )
        return 2
    dest = Path(args[0]).expanduser().resolve()
    problem = refusal(dest.name)
    if problem is not None:
        print(f"refused: {problem}", file=sys.stderr)
        return 2
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        print(f"refused: {dest} exists and is not an empty folder", file=sys.stderr)
        return 2
    names = Names(dest.name)
    version = release(plugin)
    count = copy(source, dest, names, version)
    subprocess.run(["git", "init", "--quiet", str(dest)], check=True)
    pinned = f", pinned at guideline v{version}" if version else ""
    print(f"{count} files in {dest} as {names.snake}{pinned}")
    print(f"next: cd {dest} && make setup && make check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
