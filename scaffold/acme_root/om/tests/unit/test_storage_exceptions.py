"""Every storage method takes org_id first, except the declared exceptions,
each documented in place. Every manager method takes a context stage first,
except the declared exceptions; the transitions take a weaker stage and are
declared too, so a new principal-less method must say so where it is
declared to pass. A declaration (`tenantless`, `stageless`, `transition`,
from `acme.om.context`) sits beside the method on its interface, and these
tests read it there: a namespace a copy adds is held with no line of this
file changed.

These are signature tests. They read parameter names, annotations, and the
declarations, and nothing else, so what they prove is that the tenant is
offered to the query, never that the query uses it: a method could take
`org_id` and write a `WHERE` without it and pass here. The fence is the
predicate in the query, and its evidence is the cross-tenant cases of the
contract suites, each of which presents another tenant's identifier and
asserts that nothing is found and nothing changes.
`test_every_tenant_method_is_named_in_a_cross_tenant_case` below holds the
two halves to each other: it is the guard against a new storage method
arriving with no case, and it too reads names and not queries, so a name
listed with no case behind it passes. What the cases catch when a predicate
is taken out is recorded in `docs/runbooks/tenant-isolation.md`.
"""

import importlib
import inspect
import pkgutil

import acme.om
from acme.om.context import (
    STAGELESS,
    TENANTLESS,
    TRANSITION,
    IdentityContext,
    OperatorContext,
    RequestContext,
    TenantContext,
    declared,
)

import contracts

STAGES: tuple[type, ...] = (RequestContext, IdentityContext, TenantContext, OperatorContext)


def interfaces(*suffixes: str) -> list[type]:
    found: list[type] = []
    for module_info in pkgutil.walk_packages(acme.om.__path__, prefix="acme.om."):
        module = importlib.import_module(module_info.name)
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and name.endswith(suffixes)
                and obj.__module__ == module.__name__
                and name not in suffixes
            ):
                found.append(obj)
    return found


def operations(interface: type) -> list[tuple[str, list[inspect.Parameter]]]:
    result: list[tuple[str, list[inspect.Parameter]]] = []
    for name, member in inspect.getmembers(interface, inspect.iscoroutinefunction):
        if name.startswith("_"):
            continue
        params = list(inspect.signature(member).parameters.values())[1:]
        result.append((name, params))
    return result


def stage_of(param: inspect.Parameter) -> type | None:
    """The stage a first parameter is annotated with, by class or by name (a
    module under `from __future__ import annotations` leaves a string)."""
    annotation = param.annotation
    for stage in STAGES:
        if annotation is stage or annotation == stage.__name__:
            return stage
    return None


def tenantless_methods() -> frozenset[tuple[str, str]]:
    """Every storage method declared `tenantless`, by interface and name."""
    return frozenset(
        (interface.__name__, name)
        for interface in interfaces("StorageInterface")
        for name, _ in operations(interface)
        if declared(getattr(interface, name), TENANTLESS)
    )


def cross_tenant_cases() -> dict[str, frozenset[str]]:
    """The cross-tenant cases of each storage interface, gathered from every
    contract suite that declares them: a suite names the interface it covers
    (`INTERFACE`) beside its cases (`CROSS_TENANT_CASES`), and two suites
    that cover one interface add up."""
    found: dict[str, set[str]] = {}
    for module_info in pkgutil.walk_packages(contracts.__path__, prefix="contracts."):
        module = importlib.import_module(module_info.name)
        cases = getattr(module, "CROSS_TENANT_CASES", None)
        if cases is None:
            continue
        interface = getattr(module, "INTERFACE", None)
        assert inspect.isclass(interface), f"{module_info.name} names no INTERFACE for its cases"
        found.setdefault(interface.__name__, set()).update(cases)
    return {name: frozenset(cases) for name, cases in found.items()}


def test_storage_methods_take_org_id_first_except_the_declared_ones() -> None:
    for interface in interfaces("StorageInterface"):
        for name, params in operations(interface):
            key = (interface.__name__, name)
            method = getattr(interface, name)
            if [p.name for p in params[:1]] == ["org_id"]:
                assert not declared(method, TENANTLESS), f"{key} takes org_id and is tenantless"
                continue
            assert declared(method, TENANTLESS), f"{key} does not take org_id first"
            doc = method.__doc__ or ""
            assert doc.startswith(("Global", "Cross-tenant")), f"{key} lacks its reason"
    assert tenantless_methods(), "no storage method is declared tenantless at all"


def test_every_tenant_method_is_named_in_a_cross_tenant_case() -> None:
    """The other half of the rule above. A method that takes `org_id` has a
    contract case presenting another tenant's, so the fence is proven by a
    query that runs and not by a parameter that exists. This reads the names
    the suites declare, so it catches a method that arrives with no case; that
    the named case does what it says is the suites' own business, and what
    happens when a predicate goes is the negative control's."""
    by_interface = cross_tenant_cases()
    found = {interface.__name__ for interface in interfaces("StorageInterface")}
    assert set(by_interface) == found, "a storage interface with no contract suite named"
    for interface in interfaces("StorageInterface"):
        cases = by_interface[interface.__name__]
        for name, params in operations(interface):
            takes_tenant = [p.name for p in params[:1]] == ["org_id"]
            if takes_tenant:
                assert name in cases, f"({interface.__name__}, {name}) has no cross-tenant case"
            else:
                assert name not in cases, f"({interface.__name__}, {name}) takes no tenant"
        stale = cases - {name for name, _ in operations(interface)}
        assert not stale, f"{interface.__name__} names cases for gone methods: {sorted(stale)}"


def test_manager_methods_take_a_stage_first_except_the_declared_ones() -> None:
    for interface in interfaces("ManagerInterface", "RelayInterface"):
        for name, params in operations(interface):
            key = (interface.__name__, name)
            method = getattr(interface, name)
            if params and stage_of(params[0]) is not None:
                assert not declared(method, STAGELESS), f"{key} takes a stage and is stageless"
                continue
            assert declared(method, STAGELESS), f"{key} takes no context stage first"
            doc = method.__doc__ or ""
            assert "System-internal" in doc or name in ("relay", "relay_all"), (
                f"{key} lacks its reason"
            )


def test_the_transitions_are_the_only_methods_below_the_tenant_stage() -> None:
    """A method that takes `RequestContext` or `IdentityContext` produces a
    stronger stage; it is declared a transition, with its reason in the
    docstring, and nothing else is."""
    for interface in interfaces("ManagerInterface"):
        for name, params in operations(interface):
            key = (interface.__name__, name)
            method = getattr(interface, name)
            stage = stage_of(params[0]) if params else None
            below = stage is RequestContext or stage is IdentityContext
            assert declared(method, TRANSITION) == below, (
                f"{key} takes {stage} and {'is not' if below else 'is'} declared a transition"
            )
            if below:
                doc = method.__doc__ or ""
                assert doc.startswith("System-internal"), f"{key} lacks its reason"
