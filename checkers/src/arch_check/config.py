"""The project's configuration: `[tool.arch-check]` in its root `pyproject.toml`.

Every key has a default that matches the monorepo layout the guideline
prescribes, so a scaffolded project needs only `package`:

    [tool.arch-check]
    package = "acme"
    # src = ["om/src", "infra/src", "services/*/src", ...]   # see DEFAULT_SRC
    # exclude = ["**/migrations/**"]
    # local = ["tools/arch_check"]   # the project's own rules

    [tool.arch-check.options.CTX-26]   # a rule fed the project's own names
    sites = ["om/src/acme/om/tenancy/impl/manager.py"]

    [[tool.arch-check.disable]]
    rule = "OM-09"
    adr = "docs/adr/0003-no-mixins.md"
    reason = "one line"

    [[tool.arch-check.exception]]
    rule = "STO-04"
    path = "om/src/acme/om/storage/impl/legacy.py"
    adr = "docs/adr/0012-shallow-freeze.md"
    reason = "one line"

With no `[tool.arch-check]` table at all, arch-check still runs: the
package is `--package`, or else inferred when `om/src/` holds exactly
one package directory, and there are no disables or exceptions. So a
review can run it on a project that never adopted it.

A disable or an exception is a deviation from the guideline, so each
names an ADR file that exists. Anything else is a `ConfigError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

PYPROJECT = "pyproject.toml"
TABLE = "arch-check"
ADR_DIR = "docs/adr"
DEFAULT_SRC: tuple[str, ...] = (
    "om/src",
    "infra/src",
    "integrations/src",
    "gateway/src",
    "services/*/src",
    "workers/*/src",
    "apps/*/src",
    "clients/*/src",
    "ops/src",
)
"""Every Python distribution of the monorepo layout the guideline prescribes (Monorepo Folder Structure)."""
DEFAULT_EXCLUDE: tuple[str, ...] = ()
PACKAGE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
KEYS = {"package", "src", "exclude", "local", "options", "disable", "exception"}
DISABLE_KEYS = {"rule", "adr", "reason"}
EXCEPTION_KEYS = {"rule", "path", "adr", "reason"}


class ConfigError(Exception):
    """The configuration cannot be used: arch-check exits 2."""


@dataclass(frozen=True)
class Deviation:
    """A disabled rule (`path` is None) or an exception for the files `path` matches."""

    rule: str
    adr: str
    reason: str
    path: str | None = None


@dataclass(frozen=True)
class Config:
    root: Path
    package: str
    src: tuple[str, ...] = DEFAULT_SRC
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    local: tuple[str, ...] = ()
    disabled: tuple[Deviation, ...] = ()
    exceptions: tuple[Deviation, ...] = ()
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per-rule options by rule id: the project's own names a rule is fed as data."""


def table(path: Path) -> dict[str, Any] | None:
    """The `[tool.arch-check]` table of a pyproject.toml, or None when it has none."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ConfigError(f"{path}: [tool] is not a table")
    found = tool.get(TABLE)
    if found is not None and not isinstance(found, dict):
        raise ConfigError(f"{path}: [tool.{TABLE}] is not a table")
    return found


def find_root(start: Path) -> Path:
    """The nearest directory at or above `start` whose pyproject.toml has `[tool.arch-check]`, else `start`."""
    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / PYPROJECT
        if candidate.is_file():
            try:
                if table(candidate) is not None:
                    return directory
            except ConfigError:
                return directory
    return start


def glob_match(pattern: str, rel: str) -> bool:
    """Whether a path relative to the root matches a glob.

    `*` and `?` stay inside one path segment; `**` matches any number of
    segments, none included. Both sides use `/`.
    """
    return re.fullmatch(glob_regex(pattern), rel) is not None


def relative_glob(pattern: str) -> bool:
    """Whether a glob stays under the root: not empty, not absolute, never `..`."""
    return bool(pattern.strip()) and not pattern.startswith(("/", "\\")) and ".." not in re.split(r"[/\\]", pattern)


def globs(value: Any, where: str) -> tuple[str, ...]:
    """A list of globs relative to the root; an empty, absolute, or climbing one is a `ConfigError`."""
    out = strings(value, where)
    for pattern in out:
        if not relative_glob(pattern):
            raise ConfigError(f"{where}: {pattern!r} is not a glob relative to the root")
    return out


def glob_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def adr_by_number(root: Path, number: str) -> Path | None:
    """The ADR file `docs/adr/NNNN-*.md` for a four-digit number, or None."""
    found = sorted((root / ADR_DIR).glob(f"{number}-*.md"))
    return found[0] if found else None


def infer_package(root: Path) -> str | None:
    """The product package when `om/src/` holds exactly one package directory: `om/src/acme/om` gives `acme`."""
    src = root / "om" / "src"
    if not src.is_dir():
        return None
    found = [
        p.name
        for p in src.iterdir()
        if p.is_dir() and PACKAGE.match(p.name) and not p.name.startswith("_") and not p.name.endswith(".egg-info")
    ]
    return found[0] if len(found) == 1 else None


def strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} is not a list of strings")
    return tuple(value)


def text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: `{key}` is missing or empty")
    return value


def deviations(root: Path, value: Any, name: str, keys: set[str]) -> tuple[Deviation, ...]:
    """The entries of `disable` or `exception`, each with an ADR file that exists."""
    if not isinstance(value, list):
        raise ConfigError(f"[tool.{TABLE}] `{name}` is not an array of tables")
    out: list[Deviation] = []
    for i, entry in enumerate(value):
        where = f"[[tool.{TABLE}.{name}]] #{i + 1}"
        if isinstance(entry, str):
            raise ConfigError(f"{where}: {entry!r} names no ADR; write it as a table with rule, adr, and reason")
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} is not a table")
        unknown = sorted(set(entry) - keys)
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
        rule = text(entry, "rule", where)
        adr = text(entry, "adr", where)
        reason = text(entry, "reason", where)
        path = text(entry, "path", where) if "path" in keys else None
        if not (root / adr).is_file():
            raise ConfigError(f"{where} ({rule}): ADR file {adr} does not exist")
        out.append(Deviation(rule=rule, adr=adr, reason=reason, path=path))
    return tuple(out)


def load(root: Path, package: str | None = None, *, need_package: bool = True) -> Config:
    """Read the configuration under `root`; `package` overrides the configured one.

    `need_package=False` accepts a project whose package is unknown
    (its `package` is then empty), for `--list`, which reads only the
    local rules.
    """
    root = root.resolve()
    if not root.is_dir():
        raise ConfigError(f"root {root} is not a directory")
    path = root / PYPROJECT
    data: dict[str, Any] = {}
    if path.is_file():
        data = table(path) or {}
    unknown = sorted(set(data) - KEYS)
    if unknown:
        raise ConfigError(f"[tool.{TABLE}]: unknown key(s) {', '.join(unknown)}")
    name = package if package is not None else data.get("package")
    if name is None:
        name = infer_package(root)
    if name is None and not need_package:
        name = ""
    if name is None:
        raise ConfigError(
            f"no package: om/src/ does not hold exactly one package; pass --package or set `package` under [tool.{TABLE}]"
        )
    if not isinstance(name, str) or (name and not PACKAGE.match(name)) or (need_package and not name):
        raise ConfigError(f"package {name!r} is not a dotted Python name")
    return Config(
        root=root,
        package=name,
        src=globs(data["src"], f"[tool.{TABLE}] `src`") if "src" in data else DEFAULT_SRC,
        exclude=globs(data["exclude"], f"[tool.{TABLE}] `exclude`") if "exclude" in data else DEFAULT_EXCLUDE,
        local=globs(data["local"], f"[tool.{TABLE}] `local`") if "local" in data else (),
        disabled=deviations(root, data.get("disable", []), "disable", DISABLE_KEYS),
        exceptions=deviations(root, data.get("exception", []), "exception", EXCEPTION_KEYS),
        options=rule_options(data.get("options", {})),
    )


def rule_options(value: Any) -> dict[str, dict[str, Any]]:
    """`[tool.arch-check.options.<RULE-ID>]` tables, each keyed by a lens id.

    The command line holds each id to a known rule and each key to the
    keys that rule's registration names (`options=`); a rule reads them
    with `Project.option`.
    """
    where = f"[tool.{TABLE}.options]"
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table of tables")
    out: dict[str, dict[str, Any]] = {}
    for rule, table_ in value.items():
        if not isinstance(table_, dict):
            raise ConfigError(f"{where}.{rule} must be a table")
        out[rule] = dict(table_)
    return out
