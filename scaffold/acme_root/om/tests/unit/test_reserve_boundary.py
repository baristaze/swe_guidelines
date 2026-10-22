"""The mark of a holder's call is ambient state, the one beside the request id's,
and it is held to the same kind of boundary, checked here rather than written
down: admission alone marks a call, and the storage funnel alone reads the
mark, to choose the pool's reserve. A reader anywhere else would be deciding
something from ambient state, which is what the context is for.

A static scan of the source files; nothing here imports the modules it reads.
"""

import ast
import importlib.util
from collections.abc import Iterator
from pathlib import Path

PACKAGES = ("acme.om", "acme.infra", "acme.services", "acme.workers", "acme.apps")

HOME = "acme.om.storage.reserve"
"""Where the variable lives, and the only module that touches it."""

ONLY_CALLER = {
    "as_holder_call": "acme.services.api.gateway.admission",
    "in_holder_call": "acme.om.storage.impl.pg_base",
}
"""Each function of the home module, and the one module that calls it."""


def sources() -> Iterator[tuple[str, Path]]:
    for package in PACKAGES:
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            continue  # a distribution this test run does not install
        root = Path(next(iter(spec.submodule_search_locations)))
        for path in sorted(root.rglob("*.py")):
            parts = [package, *path.relative_to(root).with_suffix("").parts]
            if parts[-1] == "__init__":
                parts.pop()
            yield ".".join(parts), path


def names_used(tree: ast.AST) -> set[str]:
    """Every name the module reads, bare or as an attribute."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_the_mark_is_set_by_admission_alone_and_read_by_the_funnel_alone() -> None:
    strays: list[str] = []
    for module, path in sources():
        if module == HOME:
            continue
        used = names_used(ast.parse(path.read_text()))
        if "holder_call_var" in used:
            strays.append(f"{module} touches holder_call_var")
        strays.extend(
            f"{module} calls {name}"
            for name, caller in ONLY_CALLER.items()
            if name in used and module != caller
        )
    assert strays == [], (
        f"the mark of a holder's call leaves its boundary: {strays}. Admission "
        "marks a call, the funnel reads the mark, and nothing else decides from it."
    )


def test_the_only_callers_still_call() -> None:
    """The scan above passes on a name nothing uses; this keeps it honest."""
    callers = {module: path for module, path in sources() if module in ONLY_CALLER.values()}
    for name, caller in ONLY_CALLER.items():
        if caller in callers:
            assert name in names_used(ast.parse(callers[caller].read_text())), (caller, name)
