"""A stage above the request stage is produced only by a transition. The
type is the fence at every call site; at the construction sites it is this
test: a static scan of every source tree enumerates every site that
constructs `IdentityContext`, `OpContext`, or `OperatorContext`, or calls
`build_context`, and fails when a site appears that is not listed here.
The list is the tenancy manager's transitions and the helper they use.
Test fakes under `tests/` are outside the scan."""

import ast
from collections import Counter
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

STAGES = frozenset({"IdentityContext", "OpContext", "OperatorContext"})
CONSTRUCTORS = STAGES | {"build_context"}
CLASS_LEVEL_BUILDERS = frozenset({"model_validate", "model_construct", "model_copy"})

TRANSITIONS = "acme.om.tenancy.impl.manager"
ALLOWED: Counter[tuple[str, str, str]] = Counter(
    {
        # The one helper that assembles a tenant context from its parts.
        ("acme.om.opcontext", "build_context", "OpContext"): 1,
        # The transitions, and the seeding that produces the principal it runs under.
        (TRANSITIONS, "TenancyManagerImpl.bootstrap", "build_context"): 1,
        (TRANSITIONS, "TenancyManagerImpl.add_member", "build_context"): 1,
        (TRANSITIONS, "TenancyManagerImpl.authenticate_login", "IdentityContext"): 1,
        (TRANSITIONS, "TenancyManagerImpl.authenticate", "build_context"): 2,
        (TRANSITIONS, "TenancyManagerImpl.admit_operator", "OperatorContext"): 1,
        (TRANSITIONS, "TenancyManagerImpl.resume", "build_context"): 1,
        (TRANSITIONS, "TenancyManagerImpl.service_context", "build_context"): 1,
        (TRANSITIONS, "TenancyManagerImpl.service_contexts", "build_context"): 1,
        # The one service_contexts mints, for a tenant a purge across tenants
        # found a row of.
    }
)
"""(module, enclosing definition, what is constructed) -> how many times."""


def source_files() -> Iterator[tuple[str, Path]]:
    for pattern in SOURCE_TREES:
        for src in sorted(REPO.glob(pattern)):
            for path in sorted(src.rglob("*.py")):
                parts = list(path.relative_to(src).with_suffix("").parts)
                if parts[-1] == "__init__":
                    parts.pop()
                yield ".".join(parts), path


def constructed(node: ast.Call) -> str | None:
    """What a call constructs, when it is a stage or the helper: `OpContext(...)`,
    `opcontext.OpContext(...)`, `build_context(...)`, and the class-level
    `OpContext.model_validate(...)` and its siblings."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in CONSTRUCTORS:
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr in CONSTRUCTORS:
            return func.attr
        if func.attr in CLASS_LEVEL_BUILDERS and isinstance(func.value, ast.Name):
            if func.value.id in STAGES:
                return func.value.id
    return None


class SiteCollector(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.sites: Counter[tuple[str, str, str]] = Counter()

    def _enter(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        name = constructed(node)
        if name is not None:
            self.sites[(self.module, ".".join(self.scope) or "<module>", name)] += 1
        self.generic_visit(node)


def construction_sites() -> Counter[tuple[str, str, str]]:
    sites: Counter[tuple[str, str, str]] = Counter()
    for module, path in source_files():
        collector = SiteCollector(module)
        collector.visit(ast.parse(path.read_text(), filename=str(path)))
        sites.update(collector.sites)
    return sites


def test_only_the_transitions_construct_a_stage_above_the_request_stage() -> None:
    sites = construction_sites()
    unexpected = sites - ALLOWED
    assert not unexpected, f"new construction sites: {dict(unexpected)}"
    stale = ALLOWED - sites
    assert not stale, f"listed sites that no longer exist: {dict(stale)}"
    assert sites == ALLOWED


def test_the_scan_sees_every_source_tree() -> None:
    modules = {module for module, _ in source_files()}
    assert {
        "acme.om.opcontext",
        "acme.om.tenancy.impl.manager",
        "acme.services.api.gateway.auth",
        "acme.workers.maintenance.loop",
        "acme.infra.root",
        "acme.apps.cli.config",
        "acme.client.client",
    } <= modules, sorted(modules)


def test_the_scan_recognises_every_way_to_construct_a_stage() -> None:
    tree = ast.parse(
        "OpContext(a=1)\n"
        "opcontext.IdentityContext(b=2)\n"
        "build_context(rctx)\n"
        "OperatorContext.model_validate({})\n"
        "OpContext.model_copy(ctx)\n"
        "RequestContext(c=3)\n"
        "ctx.model_copy(update={})\n"
    )
    seen = [constructed(node) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert [name for name in seen if name is not None] == [
        "OpContext",
        "IdentityContext",
        "build_context",
        "OperatorContext",
        "OpContext",
    ]
