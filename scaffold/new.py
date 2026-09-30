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
stays a link. The copy's skills sit in `.agents/skills/`, and its
`.claude/skills` is a link to them, whatever the source holds there: the link,
or a folder when the scaffold was copied without its links. The copy pins the
guideline release this checkout carries and starts a git repository. When the
checkout is a clean git checkout of the guideline, the repository's first
commit is the scaffold as copied, on the branch `scaffold` and the main
branch, naming the guideline commit it came from: the copy's base, which
`scaffold/base.py` moves forward. Otherwise nothing is committed, and the
first move grafts the base. It prints the next step.

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

SKILLS = Path(".agents") / "skills"
"""Where a copy keeps its skills: the folder every agent that reads the Agent
Skills standard shares."""
CLAUDE_SKILLS = Path(".claude") / "skills"
"""The folder Claude Code reads, which a copy makes a link to SKILLS."""
CLAUDE_SKILLS_TARGET = "../.agents/skills"
"""The link's target, relative, so the copy can move."""

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


BRANCH = "scaffold"
"""The copy's branch that holds its base, as `base.py` keeps it."""
REPOSITORY = "https://github.com/baristaze/swe_guidelines"
"""Where the guideline lives, unless the plugin manifest names another."""


def release(plugin: Path = PLUGIN) -> str | None:
    """The guideline release this checkout carries, or None outside one."""
    try:
        return str(json.loads(plugin.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return None


def git(where: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, input=stdin, check=False)


def checkout_commit(source: Path, plugin: Path) -> str | None:
    """The guideline commit `source` is, when it sits clean in a git checkout
    of the guideline; None otherwise: an archive, a staged plugin, or a
    scaffold with changes of its own."""
    root = source.resolve().parent.parent
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
        return None
    changed = git(root, "status", "--porcelain", "--", str(source.resolve()), str(plugin.resolve().parent))
    head = git(root, "rev-parse", "HEAD")
    if changed.returncode != 0 or changed.stdout.strip() or head.returncode != 0:
        return None
    return head.stdout.strip()


def repository(plugin: Path) -> str:
    try:
        named = json.loads(plugin.read_text(encoding="utf-8")).get("repository")
    except (OSError, ValueError, AttributeError):
        named = None
    return named if isinstance(named, str) and named.startswith("https://github.com/") else REPOSITORY


def record_base(dest: Path, names: Names, commit: str | None, plugin: Path) -> str:
    """Commits the copy as its base when `commit` names where it came from;
    returns the line that says what happened."""
    if commit is None:
        return "base: not recorded, since this is not a clean checkout of the guideline; the first move grafts it"
    if git(dest, "var", "GIT_COMMITTER_IDENT").returncode != 0:
        return "base: not recorded, since git has no identity here; the first move grafts it"
    message = (
        f"The scaffold at {commit[:9]}, as {names.snake}\n\n"
        f"Scaffold-Source: {repository(plugin)}\nScaffold-Commit: {commit}\nScaffold-Name: {names.snake}\n"
    )
    for args, stdin in ((("add", "--all", "--force", "."), None), (("commit", "--quiet", "-F", "-"), message)):
        done = git(dest, *args, stdin=stdin)
        if done.returncode != 0:
            git(dest, "read-tree", "--empty")
            return f"base: not recorded, since git {args[0]} failed: {done.stderr.strip()}"
    git(dest, "branch", BRANCH)
    return f"base: the first commit, on {BRANCH} and the main branch, is the scaffold at {commit[:9]}"


def copy(source: Path, dest: Path, names: Names, version: str | None) -> int:
    """Copies `source` into `dest` under `names`; returns how many files."""
    count = 0
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if any(part in SKIPPED or part.endswith(".pyc") for part in rel.parts):
            continue
        if rel.parts[: len(CLAUDE_SKILLS.parts)] == CLAUDE_SKILLS.parts:
            continue  # made a link below, whatever the source holds
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
    if (dest / SKILLS).is_dir():
        link = dest / CLAUDE_SKILLS
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(CLAUDE_SKILLS_TARGET)
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
    based = record_base(dest, names, checkout_commit(source, plugin), plugin)
    pinned = f", pinned at guideline v{version}" if version else ""
    print(f"{count} files in {dest} as {names.snake}{pinned}")
    print(based)
    print(f"next: cd {dest} && make setup && make check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
