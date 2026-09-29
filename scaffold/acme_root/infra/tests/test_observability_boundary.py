"""The request id in the log lines is ambient state, and ambient state is what
the context exists to replace. So the one context variable the platform keeps
is held to a boundary, checked here rather than written down: it is read only
by the sinks that need it for free, and an entry point that sets it gives the
token back in a `finally`, so it never outlives the unit of work.

A static scan of the source files; nothing here imports the modules it reads.
"""

import ast
import importlib.util
from collections.abc import Iterator
from pathlib import Path

VARIABLE = "request_id_var"
CAUSE_VARIABLE = "caused_by_request_id_var"
"""The request that caused the work, on the lines of a run that a handoff
started. It is the request id's sibling and it lives under the same boundary,
so the same two scans run over it."""

READER = "acme.infra.observability"
"""The log filter and the error tagger live here. A reader anywhere else is
deciding something from ambient state, which is what the context is for."""

PACKAGES = ("acme.om", "acme.infra", "acme.services", "acme.workers", "acme.apps")


def package_root(name: str) -> Path | None:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def sources() -> Iterator[tuple[str, Path]]:
    for package in PACKAGES:
        root = package_root(package)
        if root is None:  # a distribution this test run does not install
            continue
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root).with_suffix("")
            parts = [package, *relative.parts]
            if parts[-1] == "__init__":
                parts.pop()
            yield ".".join(parts), path


def calls(tree: ast.AST, attribute: str, variable: str = VARIABLE) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == variable
    ]


def functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def test_the_request_id_is_read_only_where_the_log_lines_are_written() -> None:
    readers = [
        module
        for module, path in sources()
        if calls(ast.parse(path.read_text()), "get") and module != READER
    ]
    assert readers == [], (
        f"{VARIABLE} is read outside {READER}: {readers}. The request id an "
        "operation acts on is the one on its context, never the ambient one."
    )


def test_an_entry_point_that_sets_the_request_id_gives_the_token_back() -> None:
    leaked = []
    for module, path in sources():
        tree = ast.parse(path.read_text())
        for function in functions(tree):
            if not calls(function, "set"):
                continue
            returned = any(
                calls(handler, "reset")
                for node in ast.walk(function)
                if isinstance(node, ast.Try)
                for handler in node.finalbody
            )
            if not returned:
                leaked.append(f"{module}.{function.name}")
    assert leaked == [], (
        f"{VARIABLE} is set without a reset in a finally: {leaked}. A request "
        "id that outlives its unit of work is worse than none: it is wrong."
    )


def test_the_causing_request_is_read_only_where_the_log_lines_are_written() -> None:
    readers = [
        module
        for module, path in sources()
        if calls(ast.parse(path.read_text()), "get", CAUSE_VARIABLE) and module != READER
    ]
    assert readers == [], (
        f"{CAUSE_VARIABLE} is read outside {READER}: {readers}. The cause an "
        "operation acts on is `caused_by_request_id` on its context."
    )


def test_an_entry_point_that_sets_the_causing_request_gives_the_token_back() -> None:
    leaked = []
    for module, path in sources():
        tree = ast.parse(path.read_text())
        for function in functions(tree):
            if not calls(function, "set", CAUSE_VARIABLE):
                continue
            returned = any(
                calls(handler, "reset", CAUSE_VARIABLE)
                for node in ast.walk(function)
                if isinstance(node, ast.Try)
                for handler in node.finalbody
            )
            if not returned:
                leaked.append(f"{module}.{function.name}")
    assert leaked == [], (
        f"{CAUSE_VARIABLE} is set without a reset in a finally: {leaked}. A "
        "cause that outlives its run names the wrong request on the next one."
    )
