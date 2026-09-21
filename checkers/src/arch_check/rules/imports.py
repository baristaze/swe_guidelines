"""Import direction: the lower layers never import an upper one.

Two lenses state it, so two rules share this module. Both read every
import statement of a file, the ones inside a function or under
`TYPE_CHECKING` included, and resolve relative imports first.

CON-12 (calls flow downward): nothing under `<pkg>.om` or `<pkg>.infra`
imports `<pkg>.services` or `<pkg>.workers`, which is how a manager
would reach a `*ServiceInterface`; no OM storage module imports a
manager. A callback handed down is judged by the review. A bare
`*ServiceInterface` name is not judged: an integration such as
`TaxServiceInterface` from `<pkg>.integrations` is a lower dependency.

CON-10 (upper layers depend on lower layers): nothing under
`<pkg>.infra` imports `<pkg>.om`; a router or a service module imports
no storage impl, table, or manager impl; a manager impl imports no
storage impl or table. A session read through an interface is judged.
"""

from __future__ import annotations

from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Import, Project, SourceFile, is_under
from arch_check.registry import rule
from arch_check.rules._contracts_util import in_manager_impl, in_om_storage, namespace_of, service_part


def offending(project: Project, file: SourceFile, forbidden: tuple[str, ...]) -> Iterator[tuple[Import, str]]:
    """Each import of `file` that reaches a forbidden package, with the name it reaches."""
    for imp in project.imports(file):
        hit = next((t for t in imp.targets() if any(is_under(t, f) for f in forbidden)), None)
        if hit is not None:
            yield imp, hit


def manager_modules(project: Project, target: str) -> bool:
    """Whether a module name is business code: `<pkg>.om.root`, or a namespace's `manager` or `impl`."""
    om = project.sub("om")
    if is_under(target, f"{om}.root"):
        return True
    ns = namespace_of(project, target)
    return ns is not None and any(is_under(target, f"{om}.{ns}.{part}") for part in ("manager", "impl"))


@rule(
    "CON-12",
    coverage="partial",
    summary="OM and infra import no service or worker; OM storage imports no manager.",
)
def calls_flow_downward(project: Project) -> Iterator[Violation]:
    """Nothing under `<pkg>.om` or `<pkg>.infra` imports `<pkg>.services`
    or `<pkg>.workers`. No OM storage module (`<pkg>.om.storage...`,
    `<pkg>.om.<ns>.storage...`) imports `<pkg>.om.root`, a namespace's
    `manager` or `impl` module, or a name ending in `ManagerInterface` or
    `ManagerImpl`."""
    upper = (project.sub("services"), project.sub("workers"))
    for file in project.modules_under(project.sub("om"), project.sub("infra")):
        for imp, hit in offending(project, file, upper):
            yield Violation.at(
                file.rel, imp.node, f"{file.module} imports {hit}; a lower layer never imports a service or a worker"
            )
    for file in project.modules_under(project.sub("om")):
        if in_om_storage(project, file.module):
            for imp in project.imports(file):
                named = next((n for n in imp.names if n.endswith(("ManagerInterface", "ManagerImpl"))), None)
                manager = next((t for t in imp.targets() if manager_modules(project, t)), None) or named
                if manager is not None:
                    yield Violation.at(file.rel, imp.node, f"{file.module} imports {manager}; storage never calls a manager")


def storage_internals(project: Project, target: str) -> bool:
    """Whether a module name is a storage impl or a table package, of the storage root or of a namespace."""
    if not in_om_storage(project, target):
        return False
    parts = target.split(".")
    return "impl" in parts[parts.index("storage") :] or "tables" in parts[parts.index("storage") :]


EXEMPT_SERVICE_MODULES = frozenset({"root", "container"})
"""The service modules that wire impls: the services root and the container."""


@rule(
    "CON-10",
    coverage="partial",
    summary="Infra imports no OM; routers and services import no storage internals or manager impl; managers no storage impl.",
)
def infra_never_imports_the_om(project: Project) -> Iterator[Violation]:
    """Nothing under `<pkg>.infra` imports `<pkg>.om`. Modules under
    `<pkg>.services.<process>.routers`, `.services`, and `.impl` (the
    services root and the container left out) import no storage impl,
    no table module, and no `<pkg>.om.<ns>.impl`. Modules under
    `<pkg>.om.<ns>.impl` import no storage impl and no table module."""
    om = (project.sub("om"),)
    for file in project.modules_under(project.sub("infra")):
        for imp, hit in offending(project, file, om):
            yield Violation.at(file.rel, imp.node, f"{file.module} imports {hit}; infra imports nothing from the OM")
    for file in project.modules_under(project.sub("services")):
        if service_part(project, file.module) not in {"routers", "services", "impl"}:
            continue
        if file.module.rpartition(".")[2] in EXEMPT_SERVICE_MODULES:
            continue
        for imp in project.imports(file):
            internal = next(
                (t for t in imp.targets() if storage_internals(project, t) or in_manager_impl(project, t)),
                None,
            )
            if internal is not None:
                yield Violation.at(
                    file.rel, imp.node, f"{file.module} imports {internal}; the network layer depends on interfaces only"
                )
    for file in project.modules_under(project.sub("om")):
        if not in_manager_impl(project, file.module):
            continue
        for imp in project.imports(file):
            internal = next((t for t in imp.targets() if storage_internals(project, t)), None)
            if internal is not None:
                yield Violation.at(
                    file.rel, imp.node, f"{file.module} imports {internal}; a manager depends on the storage interface"
                )
