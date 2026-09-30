"""Every engine the tree builds hides its parameters.

A statement that fails is an exception, and SQLAlchemy writes the values
bound to it into the exception's text unless the engine says not to. Those
values are a tenant's words and a person's address, and the text goes into a
traceback, a log line under the request's id, and an event in the tracker.
So each engine is built with `hide_parameters=True`: the ones the processes
hold, the migration runner's, and the audit tools'. The tests' own engines,
which run what the tests wrote, are left out.
"""

import ast
import os
from collections.abc import Iterator
from pathlib import Path

from acme.om.storage.impl.postgres import engine_for
from acme.om.storage.settings import RolePool

ROOT = Path(__file__).resolve().parents[3]
BUILDERS = {"create_engine", "create_async_engine"}
LEFT_OUT = {"tests", ".venv", "node_modules", ".git"}


def engines() -> Iterator[tuple[str, ast.Call]]:
    """Every call that builds an engine, with where it is, outside the tests
    and what a tool installed."""
    for folder, folders, files in os.walk(ROOT):
        folders[:] = sorted(name for name in folders if name not in LEFT_OUT)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(folder) / name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and called(node) in BUILDERS:
                    yield f"{path.relative_to(ROOT).as_posix()}:{node.lineno}", node


def called(call: ast.Call) -> str:
    function = call.func
    return function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")


def hides(call: ast.Call) -> bool:
    return any(
        keyword.arg == "hide_parameters"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in call.keywords
    )


def test_every_engine_outside_the_tests_hides_its_parameters() -> None:
    found = dict(engines())
    assert any(where.startswith("om/src/") for where in found), (
        "the scan no longer sees the engines"
    )
    assert [where for where, call in found.items() if not hides(call)] == []


def test_a_role_engine_is_built_hiding_its_parameters() -> None:
    pool = RolePool(size=1, checkout_timeout_seconds=1.0, statement_timeout_seconds=1.0)
    engine = engine_for("postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none", pool)
    assert engine.sync_engine.hide_parameters is True
