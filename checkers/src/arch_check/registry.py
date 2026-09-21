"""The rule registry: one registration per rule, keyed by its lens id.

A rule module under `arch_check.rules` registers each rule with the
`rule` decorator. `rules()` imports every module of that package, so a
shipped rule is one new module and nothing else to wire.

A project's own rules live in its tree, in the directories `local`
names in its config. `load_local` runs each file there, and the same
decorator registers its rules into a separate list marked `local`, so
one project's rules never leak into another run.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import pkgutil
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from arch_check.lenses import LENSES
from arch_check.model import COVERAGES, GROUPS, SEVERITIES, Check, Coverage, Origin, Rule, Severity

RULES: dict[str, Rule] = {}
"""The shipped rules, by id."""

ID = re.compile(r"^([A-Z]{2,3})-(\d{2})$")
PREFIXES = {prefix: group for group, prefix in GROUPS.items()}

_collecting: list[Rule] | None = None
"""The list a local file's registrations go to while `load_local` runs it."""


class RegistryError(Exception):
    """A registration that contradicts the lens catalog: a bug in a rule module."""


def group_of(id: str) -> str:
    m = ID.match(id)
    if not m:
        raise RegistryError(f"rule id {id!r} is not PREFIX-NN")
    if m.group(1) not in PREFIXES:
        raise RegistryError(f"{id}: no lens group has the prefix {m.group(1)}")
    return PREFIXES[m.group(1)]


def make(
    id: str,
    check: Check,
    *,
    coverage: Coverage,
    summary: str,
    group: str | None = None,
    severity: Severity | None = None,
    origin: Origin = "guideline",
    options: Sequence[str] = (),
) -> Rule:
    """A rule checked against the catalog: the lens exists, and group and severity are the lens's.

    `group` and `severity` default to the lens's; when given, they must agree.
    """
    lens_group = group_of(id)
    if id not in LENSES:
        raise RegistryError(f"{id}: no lens has this id")
    if group is not None and group != lens_group:
        raise RegistryError(f"{id}: group {group!r}, and the lens is in {lens_group!r}")
    if severity is not None and severity not in SEVERITIES:
        raise RegistryError(f"{id}: severity {severity!r} is not high, medium, or low")
    if severity is not None and severity != LENSES[id]:
        raise RegistryError(f"{id}: severity {severity!r}, and the lens says {LENSES[id]!r}")
    if coverage not in COVERAGES:
        raise RegistryError(f"{id}: coverage {coverage!r} is not full or partial")
    if not summary.strip():
        raise RegistryError(f"{id}: empty summary")
    return Rule(
        id=id,
        group=lens_group,
        severity=LENSES[id],
        coverage=coverage,
        summary=summary,
        check=check,
        origin=origin,
        options=frozenset(options),
    )


def register(new: Rule) -> Rule:
    """Add a shipped rule, or a local one while `load_local` runs; a repeated id is refused."""
    if _collecting is not None:
        if any(r.id == new.id for r in _collecting):
            raise RegistryError(f"{new.id} is registered twice by the project's local rules")
        _collecting.append(new)
        return new
    old = RULES.get(new.id)
    if old is not None and old.check is not new.check:
        raise RegistryError(f"{new.id} is registered twice")
    RULES[new.id] = new
    return new


def rule(
    id: str,
    *,
    coverage: Coverage,
    summary: str,
    group: str | None = None,
    severity: Severity | None = None,
    options: Sequence[str] = (),
) -> Callable[[Check], Check]:
    """Register the decorated function as the rule that decides lens `id`.

    `coverage` is `full` when the rule decides the whole lens and
    `partial` when the rest is judged by a review. `summary` is one
    line for `--list`. Group and severity come from the lens.
    `options` names every key the rule reads with `Project.option`, so
    a key it does not read exits 2 before the run.
    """

    def wrap(check: Check) -> Check:
        origin: Origin = "local" if _collecting is not None else "guideline"
        register(
            make(id, check, coverage=coverage, summary=summary, group=group, severity=severity, origin=origin, options=options)
        )
        return check

    return wrap


def load() -> None:
    """Import every shipped rule module, which registers its rules; modules starting with `_` are skipped."""
    package = importlib.import_module("arch_check.rules")
    for info in pkgutil.iter_modules(package.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"arch_check.rules.{info.name}")


def order(r: Rule) -> tuple[int, str, int]:
    """Catalog order: the group's position, then the id, the shipped rule before a local one."""
    return list(GROUPS).index(r.group), r.id, 0 if r.origin == "guideline" else 1


def rules() -> list[Rule]:
    """Every shipped rule, in catalog order."""
    load()
    return sorted(RULES.values(), key=order)


def load_local(root: Path, dirs: Sequence[str]) -> list[Rule]:
    """Run every `*.py` of the project's `local` directories and return the rules they register.

    A file starting with `_` is skipped. A file that fails to import,
    or a rule that breaks the catalog, raises `RegistryError` naming the
    file. A local rule may take a lens no shipped rule decides, or add
    to one a shipped rule decides in part; it never takes a lens a
    shipped rule decides whole.
    """
    global _collecting
    shipped = {r.id: r for r in rules()}
    found: list[Rule] = []
    for rel in dirs:
        directory = root / rel
        if not directory.is_dir():
            raise RegistryError(f"local rule directory {rel} does not exist")
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            where = path.relative_to(root).as_posix()
            digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
            name = f"arch_check_local_{digest}_{path.stem}"
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise RegistryError(f"{where}: cannot be loaded as a Python module")
            module = importlib.util.module_from_spec(spec)
            before = len(found)
            _collecting = found
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except RegistryError as e:
                raise RegistryError(f"{where}: {e}") from e
            except Exception as e:
                raise RegistryError(f"{where}: fails to import: {type(e).__name__}: {e}") from e
            finally:
                _collecting = None
                sys.modules.pop(name, None)
            for r in found[before:]:
                other = shipped.get(r.id)
                if other is not None and other.coverage == "full":
                    raise RegistryError(f"{where}: {r.id} is decided whole by the shipped rule; a local rule cannot take it")
    return found
