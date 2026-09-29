"""Every transaction names its scope at the funnel, and the system scope is
passed only where the enumerated exceptions say.

`PgStorageBase._session_for` is the one place a Postgres impl opens a session,
and it takes the tenant. `EMPTY_UUID` as the tenant is the system scope: the
transaction that reads across tenants, which the policy lets through. That is
the one deliberate bypass, so it is read out of the source here rather than
trusted to review. This walks the syntax, not the call: a method that passes
`EMPTY_UUID` through a variable is not read, which is why the funnel's
argument is spelled at every call site and nowhere computed.
"""

import ast
from pathlib import Path

import pytest
from unit.test_storage_exceptions import STORAGE_EXCEPTIONS

import acme.om
from acme.om.storage.impl import pg_base

OM_ROOT = Path(acme.om.__path__[0])

IMPL_INTERFACES: dict[str, str] = {
    "TenancyStoragePostgresImpl": "TenancyStorageInterface",
    "WorkStoragePostgresImpl": "WorkStorageInterface",
    "EventStoragePostgresImpl": "EventStorageInterface",
    "OutboxStoragePostgresImpl": "OutboxStorageInterface",
    "IdempotencyStoragePostgresImpl": "IdempotencyStorageInterface",
    "MediaStoragePostgresImpl": "MediaStorageInterface",
    "OrchestrationsStoragePostgresImpl": "OrchestrationsStorageInterface",
}
"""Which interface each Postgres impl answers, so a method found in the source
can be held against the exceptions list, which names interfaces."""

SYSTEM_SCOPE_HELPERS: frozenset[tuple[str, str]] = frozenset(
    {
        # The global-table primitive. A `system`-scoped table carries no
        # policy and no tenant; every caller of it is in the exceptions list.
        ("PgStorageBase", "_upsert_global"),
    }
)
"""The function that passes the system scope and is no interface method,
with its reason above. Nothing else may, and a new entry is a decision."""

SYSTEM_SCOPE_WRITES: frozenset[tuple[str, str]] = frozenset(
    {
        # The exchange of a sign-in. It takes the tenant the session lands in,
        # so it is no tenant-less method, but the sign-in it ends is a row of
        # the system scope, which only the system scope writes. The one
        # transaction opens there and sets the tenant before its insert
        # (ADR 0037).
        ("TenancyStorageInterface", "exchange_sign_in"),
    }
)
"""Tenant methods that also open the system scope, each with its reason above.
A new entry is a decision too."""


def impl_modules() -> list[Path]:
    found = sorted(OM_ROOT.glob("*/storage/impl/postgres.py"))
    assert found, "no Postgres impls were found"
    return [*found, Path(pg_base.__file__)]


def funnel_calls(tree: ast.AST) -> list[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every `self._session_for(...)` in a module, with the function it is in."""
    found: list[tuple[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "_session_for"
            ):
                found.append((inner, node))
    return found


def enclosing_class(tree: ast.AST, function: ast.AST) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and function in ast.walk(node):
            return node.name
    raise AssertionError("a funnel call outside a class")


def test_every_funnel_call_names_its_scope() -> None:
    """The tenant is the `org_id` keyword, always spelled at the call site: a
    statement whose session is opened without one does not compile past here."""
    seen = 0
    for path in impl_modules():
        tree = ast.parse(path.read_text())
        for call, function in funnel_calls(tree):
            if function.name == "_session_for":
                continue  # the funnel's own definition
            scope = [argument for argument in call.args[1:]] + [
                keyword.value for keyword in call.keywords if keyword.arg == "org_id"
            ]
            assert scope, f"{path.name}:{function.name} opens a session with no tenant"
            assert isinstance(scope[0], ast.Name | ast.Attribute), (
                f"{path.name}:{function.name} computes its tenant; spell it"
            )
            seen += 1
    assert seen > 40, f"only {seen} funnel calls were read; the walk is not finding them"


def test_the_system_scope_reaches_the_funnel_only_where_it_is_named() -> None:
    found: set[tuple[str, str]] = set()
    for path in impl_modules():
        tree = ast.parse(path.read_text())
        for call, function in funnel_calls(tree):
            names = {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}
            if "EMPTY_UUID" in names:
                found.add((enclosing_class(tree, function), function.name))

    helpers = found & SYSTEM_SCOPE_HELPERS
    assert helpers == SYSTEM_SCOPE_HELPERS, (
        f"named but no longer passing the system scope: {SYSTEM_SCOPE_HELPERS - helpers}"
    )
    writes = {(IMPL_INTERFACES.get(c), m) for c, m in found} & SYSTEM_SCOPE_WRITES
    assert writes == SYSTEM_SCOPE_WRITES, (
        f"named but no longer passing the system scope: {SYSTEM_SCOPE_WRITES - writes}"
    )
    for class_name, method in sorted(found - SYSTEM_SCOPE_HELPERS):
        interface = IMPL_INTERFACES.get(class_name)
        assert interface is not None, f"{class_name}.{method} is in no interface"
        assert (interface, method) in STORAGE_EXCEPTIONS | SYSTEM_SCOPE_WRITES, (
            f"({interface}, {method}) reads across tenants and is not an enumerated exception"
        )


@pytest.mark.parametrize(
    ("interface", "method"),
    sorted(pair for pair in STORAGE_EXCEPTIONS if pair[0] != "TenancyStorageInterface"),
)
def test_every_enumerated_exception_takes_the_system_scope(interface: str, method: str) -> None:
    """The other half: a method that takes no tenant reads across tenants, so
    it passes `EMPTY_UUID` and does not quietly borrow one. The tenancy
    exceptions are left out because two of them write through the global
    primitive or read one row by a hash, and the source they pass through is
    read by the test above."""
    impl = next(name for name, value in IMPL_INTERFACES.items() if value == interface)
    path = next(p for p in impl_modules() if impl.lower().startswith(p.parent.parts[-3]))
    tree = ast.parse(path.read_text())
    calls = [
        call
        for call, function in funnel_calls(tree)
        if function.name == method and enclosing_class(tree, function) == impl
    ]
    assert calls, f"{impl}.{method} opens no session of its own"
    for call in calls:
        names = {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}
        assert "EMPTY_UUID" in names, f"{impl}.{method} takes a tenant it was not given"
