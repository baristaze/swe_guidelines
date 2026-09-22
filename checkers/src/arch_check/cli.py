"""The `arch-check` command line.

Exit status: 0 when clean, 1 when there are findings, 2 on a
configuration or usage error. A file that does not parse is a `PARSE`
finding, never a crash.
"""

from __future__ import annotations

import argparse
import re
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from arch_check import __version__, registry, report
from arch_check.config import ConfigError, find_root, load
from arch_check.model import GROUPS, Rule
from arch_check.project import Project
from arch_check.runner import run

CLEAN, FINDINGS, ERROR = 0, 1, 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="arch-check",
        description="Static checks for the lenses of the Software Design and Architecture Guidelines.",
        allow_abbrev=False,
    )
    p.add_argument("paths", nargs="*", help="report findings only under these paths (the whole project is read)")
    p.add_argument("--root", help="repository root (default: nearest directory whose pyproject.toml has [tool.arch-check])")
    p.add_argument("--package", help="the product's import root, overriding [tool.arch-check] package")
    p.add_argument("--group", help="only the rules of these lens groups, comma-separated")
    p.add_argument("--rule", help="only these rules, comma-separated lens ids")
    p.add_argument("--format", choices=("text", "json"), default="text", help="report format (default: text)")
    p.add_argument("--list", action="store_true", help="print every rule and exit")
    p.add_argument("--version", action="version", version=f"arch-check {__version__}")
    return p


def split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def pinned_python(root: Path) -> tuple[int, int] | None:
    """The `major.minor` the project's `.python-version` pins, or None.

    `ast` parses with the running interpreter's grammar, so a project
    pinned to a newer Python can hold syntax this one cannot read.
    """
    path = root / ".python-version"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").replace("\ufffd", "")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    m = re.match(r"\s*(\d+)\.(\d+)", lines[0]) if lines else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def options_of(id: str, rules: Sequence[Rule]) -> set[str]:
    """Every option key the rules with this id declare: a shipped rule and a local one may share an id."""
    return {key for r in rules if r.id == id for key in r.options}


def error(message: str) -> int:
    print(f"arch-check: error: {message}", file=sys.stderr)
    return ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    root = Path(args.root) if args.root else find_root(Path.cwd())
    try:
        config = load(root, args.package, need_package=not args.list)
        everything = sorted([*registry.rules(), *registry.load_local(config.root, config.local)], key=registry.order)
    except (ConfigError, registry.RegistryError) as e:
        return error(str(e))
    known = {r.id for r in everything}

    groups = split(args.group)
    unknown_groups = [g for g in groups if g not in GROUPS]
    if unknown_groups:
        return error(f"unknown group(s) {', '.join(unknown_groups)}; groups are {', '.join(GROUPS)}")
    ids = split(args.rule)
    unknown_ids = [i for i in ids if i not in known]
    if unknown_ids:
        return error(f"unknown rule(s) {', '.join(unknown_ids)}; `arch-check --list` prints them")
    selected = [r for r in everything if (not groups or r.group in groups) and (not ids or r.id in ids)]

    if args.list:
        print(report.listing(selected))
        return CLEAN

    for d in (*config.disabled, *config.exceptions):
        if d.rule not in known:
            return error(f"[tool.arch-check] names unknown rule {d.rule}")
    for name, table in config.options.items():
        if name not in known:
            return error(f"[tool.arch-check.options] names unknown rule {name}")
        unknown_keys = sorted(set(table) - options_of(name, everything))
        if unknown_keys:
            return error(f"[tool.arch-check.options.{name}]: unknown key(s) {', '.join(unknown_keys)}")
    disabled = {d.rule for d in config.disabled}
    selected = [r for r in selected if r.id not in disabled]
    if not selected:
        return error("every selected rule is disabled by [tool.arch-check]; nothing to run")

    paths: list[str] = []
    for raw in args.paths:
        path = Path(raw).resolve()
        try:
            paths.append(path.relative_to(config.root).as_posix())
        except ValueError:
            return error(f"{raw} is outside the root {config.root}")

    pinned = pinned_python(config.root)
    if pinned is not None and pinned > sys.version_info[:2]:
        return error(
            f"the project pins Python {pinned[0]}.{pinned[1]} (.python-version) and arch-check runs on "
            f"{sys.version_info[0]}.{sys.version_info[1]}, whose parser cannot read it; "
            f"run it with that Python, e.g. uvx --python {pinned[0]}.{pinned[1]} ..."
        )
    project = Project(config)
    # Source globs that match nothing read as a clean project too: every
    # Python rule runs over no file and finds nothing.
    if not project.python_files:
        return error(
            f"the source globs {', '.join(config.src)} match no Python file under {config.root}; "
            "check `src` under [tool.arch-check]"
        )
    # A misspelled package reads as a clean project: every rule looks under
    # a package no file is in, and finds nothing. That is an error, not a pass.
    if project.python_files and not project.modules_under(config.package):
        found = sorted({f.module.split(".")[0] for f in project.python_files})
        return error(f"no module is under the package {config.package!r}; the source roots hold {', '.join(found)}")
    try:
        result = run(project, selected, known, [p for p in paths if p != "."])
    except ConfigError as e:
        return error(str(e))
    except Exception:
        traceback.print_exc()
        return error("a rule failed; this is a bug in the rule, not in the project")
    print(report.json(result) if args.format == "json" else report.text(result))
    return FINDINGS if result.findings else CLEAN
