"""Helpers the contracts and context rules share.

The layers by module name, the classes a rule reads by their name's
suffix (`*Interface`, `*Impl`), the operations of an interface, the
names an annotation spells, and the stages and scopes of the context
module. Nothing here registers a rule.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from arch_check.project import Function, Project, SourceFile, classes, dotted, is_under, last, methods, parameters

STAGES = ("RequestContext", "IdentityContext", "OpContext", "OperatorContext")
"""The four stages, by the names the guideline gives them (OpContext, Stages)."""

REQUEST_STAGE = "RequestContext"
STAGE_MODULE = "om.opcontext"
"""The module that declares the stages and the scopes, below the product package."""

HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def segments(name: str) -> list[str]:
    return name.split(".")


def in_om_storage(project: Project, module: str) -> bool:
    """Whether a module is storage: `<pkg>.om.storage...` or `<pkg>.om.<ns>.storage...`."""
    om = project.sub("om")
    if not is_under(module, om):
        return False
    rest = segments(module[len(om) + 1 :]) if module != om else []
    return rest[:1] == ["storage"] or rest[1:2] == ["storage"]


def namespace_of(project: Project, module: str) -> str | None:
    """The OM namespace a module belongs to (`tasks` for `<pkg>.om.tasks.impl.manager`), else None."""
    om = project.sub("om")
    if not is_under(module, om) or module == om:
        return None
    first = segments(module[len(om) + 1 :])[0]
    if first in {"storage", "base", "root", "opcontext", "exceptions"}:
        return None
    return first


def in_manager_impl(project: Project, module: str) -> bool:
    """Whether a module is under `<pkg>.om.<ns>.impl`, a namespace's business impl."""
    ns = namespace_of(project, module)
    return ns is not None and is_under(module, f"{project.sub('om')}.{ns}.impl")


def service_part(project: Project, module: str) -> str | None:
    """The part of a service module below its process: `routers` for `<pkg>.services.api.routers.tasks`."""
    services = project.sub("services")
    if not is_under(module, services):
        return None
    rest = segments(module[len(services) + 1 :]) if module != services else []
    return rest[1] if len(rest) > 1 else None


def classes_named(project: Project, suffix: str, *prefixes: str) -> Iterator[tuple[SourceFile, ast.ClassDef]]:
    """Every class whose name ends in `suffix`, under `prefixes` or the whole product."""
    for file, tree in project.trees(*(prefixes or (project.package,))):
        for cls in classes(tree):
            if cls.name.endswith(suffix):
                yield file, cls


def public(fn: Function) -> bool:
    return not fn.name.startswith("_")


def is_static(fn: Function) -> bool:
    return any(last(dotted(d)) == "staticmethod" for d in fn.decorator_list)


def arguments(fn: Function) -> list[ast.arg]:
    """The parameters of a method after `self` or `cls`, in declaration order (the variadic ones left out)."""
    a = fn.args
    out = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    return out if is_static(fn) else out[1:]


def names_in(node: ast.AST | None) -> set[str]:
    """Every bare and dotted-last name an annotation spells, string annotations parsed."""
    found: set[str] = set()
    if node is None:
        return found
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            try:
                inner = ast.parse(sub.value, mode="eval")
            except SyntaxError:
                continue
            found |= names_in(inner)
    return found


def head_name(node: ast.AST | None) -> str | None:
    """The one name an annotation is, `ctx: OpContext` or `ctx: "OpContext"`; None for anything wider."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(node, ast.Name | ast.Attribute):
        return last(dotted(node))
    return None


def union_members(node: ast.AST | None) -> list[str]:
    """The names a top-level annotation is: `A | None` and `Optional[A]` give `A` and `None`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*union_members(node.left), *union_members(node.right)]
    if isinstance(node, ast.Subscript) and last(dotted(node.value)) in {"Optional", "Union"}:
        inner = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        return [m for e in inner for m in union_members(e)]
    if isinstance(node, ast.Constant) and node.value is None:
        return ["None"]
    name = head_name(node)
    return [name] if name else []


def body_without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def is_ellipsis_body(fn: Function) -> bool:
    """Whether a function body is an optional docstring and `...`, or the docstring alone."""
    rest = body_without_docstring(fn.body)
    if not rest:
        return True
    return (
        len(rest) == 1
        and isinstance(rest[0], ast.Expr)
        and isinstance(rest[0].value, ast.Constant)
        and rest[0].value.value is Ellipsis
    )


def has_docstring(fn: Function) -> bool:
    return ast.get_docstring(fn) is not None


def stage_module(project: Project) -> SourceFile | None:
    return project.module(f"{project.package}.{STAGE_MODULE}")


def stage_classes(project: Project) -> dict[str, ast.ClassDef]:
    """The classes of the stage module, by name."""
    file = stage_module(project)
    tree = project.tree(file) if file else None
    return {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)} if tree else {}


def declared_fields(cls: ast.ClassDef) -> dict[str, ast.AST]:
    """The names a class body declares: annotated fields and properties, each with its node."""
    out: dict[str, ast.AST] = {}
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            last(dotted(d)) in {"property", "cached_property"} for d in node.decorator_list
        ):
            out[node.name] = node
    return out


def calls(node: ast.AST) -> Iterator[ast.Call]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def called_name(call: ast.Call) -> str | None:
    """The last name of what a call calls: `OpContext` for `opcontext.OpContext(...)`."""
    return last(dotted(call.func)) if isinstance(call.func, ast.Name | ast.Attribute) else None


def enclosing(tree: ast.Module) -> dict[ast.AST, str]:
    """Each node's enclosing definition as a dotted qualname (`Class.method`), `<module>` at the top."""
    out: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                out[child] = ".".join(scope) or "<module>"
                visit(child, [*scope, child.name])
            else:
                out[child] = ".".join(scope) or "<module>"
                visit(child, scope)

    visit(tree, [])
    return out


def interface_methods(cls: ast.ClassDef) -> list[Function]:
    """The public methods an interface class declares."""
    return [m for m in methods(cls) if public(m)]


def first_parameter_name(fn: Function) -> str | None:
    args = arguments(fn)
    return args[0].arg if args else None


def init_of(cls: ast.ClassDef) -> Function | None:
    return next((m for m in methods(cls) if m.name == "__init__"), None)


def signature_names(fn: Function) -> list[str]:
    return [p.name for p in parameters(fn)]
