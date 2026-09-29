"""The update copy is one of two calls, decided by what it carries. A copy
whose every value is constructed of the field's own type (a timestamp, an id,
a status) is `model_copy(update=...)`. A copy that carries a dump, the caller's
fields above all, is `Entity.model_validate({**current.model_dump(), ...})`,
because `model_copy` does not validate and leaves a dumped value object a
plain dict. This scan holds the rule over every source tree.

What the scan sees: a `model_copy` call whose `update` is a dict literal with
a `**` entry that is a `.model_dump(...)` call; a `model_copy` whose `update`
is a name, or a dict literal spreading a name, that the same function bound
to a `.model_dump(...)` call or to a dict literal spreading one; and, as
text, the spelling `model_copy(update={**`, which the guideline refuses in a
snippet outright, since a spread inside a `model_copy` reads as a dump
whatever it carries. What it does not see: a dump that reaches the copy
through a call, a parameter, an attribute, or a name bound outside the
function. Test fakes under `tests/` are outside the scan."""

import ast
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SOURCE_TREES = (
    "om/src",
    "infra/src",
    "services/*/src",
    "workers/*/src",
    "apps/*/src",
    "clients/*/src",
)
FORBIDDEN_SPELLING = "model_copy(update={**"


def source_files() -> Iterator[Path]:
    for pattern in SOURCE_TREES:
        for src in sorted(REPO.glob(pattern)):
            yield from sorted(src.rglob("*.py"))


def _is_model_dump_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_dump"
    )


def _spreads_a_dump(node: ast.AST, dumped_names: set[str]) -> bool:
    """A dict literal with a `**` entry that is a dump call or a name bound to one."""
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values, strict=True):
        if key is not None:
            continue
        if _is_model_dump_call(value):
            return True
        if isinstance(value, ast.Name) and value.id in dumped_names:
            return True
    return False


def _names_bound_to_a_dump(function: ast.AST) -> set[str]:
    """The names the function binds to a dump call or to a dict literal spreading one."""
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and (
            _is_model_dump_call(value) or _spreads_a_dump(value, names)
        ):
            names.add(target.id)
    return names


def _update_argument(call: ast.Call) -> ast.AST | None:
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "model_copy"):
        return None
    for keyword in call.keywords:
        if keyword.arg == "update":
            return keyword.value
    return None


def copies_of_a_dump(tree: ast.AST) -> list[int]:
    """Line numbers of every `model_copy(update=...)` in the tree whose update
    carries a dump the scan can see."""
    found: list[int] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        dumped = _names_bound_to_a_dump(function)
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            update = _update_argument(node)
            if update is None:
                continue
            if _spreads_a_dump(update, dumped) or (
                isinstance(update, ast.Name) and update.id in dumped
            ):
                found.append(node.lineno)
    return found


def test_no_model_copy_carries_a_dump() -> None:
    sites: list[str] = []
    for path in source_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        sites.extend(f"{path.relative_to(REPO)}:{line}" for line in copies_of_a_dump(tree))
    assert not sites, f"a copy that carries a dump is model_validate over the dicts: {sites}"


def test_no_source_spells_a_spread_inside_model_copy() -> None:
    sites = [
        f"{path.relative_to(REPO)}:{number}"
        for path in source_files()
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if FORBIDDEN_SPELLING in line
    ]
    assert not sites, f"the guideline refuses this spelling: {sites}"


def test_the_scan_sees_every_source_tree() -> None:
    seen = {str(path.relative_to(REPO)) for path in source_files()}
    assert {
        "om/src/acme/om/media/impl/manager.py",
        "om/src/acme/om/tenancy/impl/manager.py",
        "infra/src/acme/infra/base.py",
        "services/api/src/acme/services/api/services/impl/media.py",
        "workers/maintenance/src/acme/workers/maintenance/loop.py",
        "apps/cli/src/acme/apps/cli/config.py",
        "clients/python/src/acme/client/client.py",
    } <= seen, sorted(seen)


def test_the_scan_recognises_a_dump_however_it_is_bound() -> None:
    tree = ast.parse(
        "def a(current, caller):\n"
        "    return current.model_copy(update={**caller.model_dump(), 'v': 1})\n"
        "def b(current, caller):\n"
        "    update: dict[str, object] = {**caller.model_dump(exclude=X), 'v': 1}\n"
        "    return current.model_copy(update=update)\n"
        "def c(current, caller):\n"
        "    fields = caller.model_dump()\n"
        "    return current.model_copy(update={**fields, 'v': 1})\n"
        "def d(current, caller):\n"
        "    changes = {'v': 1}\n"
        "    return current.model_copy(update=changes)\n"
        "def e(current, now):\n"
        "    return current.model_copy(update={'updated_at': now})\n"
        "def f(current, caller):\n"
        "    return Entity.model_validate({**current.model_dump(), **caller.model_dump()})\n"
    )
    assert copies_of_a_dump(tree) == [2, 5, 8]
