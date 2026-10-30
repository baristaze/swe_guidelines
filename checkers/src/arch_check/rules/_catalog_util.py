"""CON-07's catalog: every tunable number is declared once, in the
project's parameters catalog, and written nowhere else.

A project opts in by naming its catalog (`[tool.arch-check.options.CON-07]
catalog`); with none named, nothing here runs. Then:

- A module or class constant that is a number, a duration, a tuple of
  them, or a literal multiple of a unit's name (`21 * GIB`) is the
  catalog's.
- A settings field's numeric default comes from the catalog; an options,
  bounds, or limits class holds no numeric default of its own, and no other
  class a field whose name says it tunes.
- A keyword argument or a parameter's default whose name says it tunes
  (`timeout`, `retries`, `limit`, `*_seconds`, `max_*`) takes no number
  written in place, and neither do `asyncio`'s waits or a `timedelta`. A
  wire type's bound (`Field`, `StringConstraints`) is its contract, and is
  left alone.
- The TypeScript the `typescript` globs name, and the Terraform variables
  the `terraform` glob names, are held the same way: a top-level numeric
  constant or a timer, and a numeric default no catalog entry's `source`
  names, or one that differs from it.
- Every catalog entry names what it is, and, with `review = "required"`,
  carries a person's review.

A number that tunes nothing carries `# fixed: <why>` on its line (`//` in
TypeScript); zero, one, and minus one are taken as what they say."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

from arch_check.config import glob_match
from arch_check.model import Violation
from arch_check.project import Project

FIXED = re.compile(r"(#|//)\s*fixed:\s*\S")
"""The mark of a number that tunes nothing, with its reason."""

TUNES = re.compile(
    r"(^|_)(timeout|timeouts|retries|retry|attempts|limit|limits|interval|ttl|backoff|delay|"
    r"seconds|ms|millis|minutes|hours|days|lease|window|batch|cap|budget|deadline|grace|"
    r"heartbeat|stagger|expires|expiry|lifetime|page|size)($|_)|^(max|min)_",
    re.IGNORECASE,
)
"""A keyword or parameter name that says its number tunes something."""

TIMERS = frozenset({"sleep", "timeout", "wait_for", "timeout_at"})
"""`asyncio`'s waits, whose last argument is a duration."""

CONTRACTS = frozenset({"Field", "StringConstraints", "constr", "conint", "confloat", "conlist", "Query", "Path"})
"""What a wire type or a route declares as its contract: a bound the schema
publishes, not a number an operator tunes."""

TS_CONST = re.compile(r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=\s*(.+?);?\s*$")
TS_NUMBER = re.compile(r"^[-+*/()\s\d_.e]+$")
TS_TIMER = re.compile(r"\b(setTimeout|setInterval)\([^;]*?,\s*(\d[\d_]*)\s*\)")
TS_PROP = re.compile(
    r"\b(staleTime|gcTime|refetchInterval|retryDelay|retry|timeout|timeoutMs|delayMs|"
    r"intervalMs|pollInterval)\s*:\s*(\d[\d_]*)\b"
)

TF_VARIABLE = re.compile(r'variable\s+"([^"]+)"\s*\{(.*?)\n\}', re.S)
TF_DEFAULT = re.compile(r"^\s*default\s*=\s*(-?\d[\d_.]*)\s*$", re.M)


def numeric(node: ast.AST | None) -> bool:
    """A number written in place: a literal, arithmetic of literals, a tuple
    of them, or a `timedelta` of them. A name, an attribute, or a call to
    anything else reads its number from somewhere."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int | float) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return numeric(node.operand)
    if isinstance(node, ast.BinOp):
        # `21 * GIB` is a number written in place as much as `21 * 1024**3`:
        # a literal beside a unit's name, or beside another literal.
        sides = (node.left, node.right)
        if all(numeric(side) for side in sides):
            return True
        scaled = isinstance(node.op, ast.Mult | ast.Div | ast.FloorDiv | ast.Pow)
        return scaled and any(numeric(side) for side in sides) and any(unit_name(s) for s in sides)
    if isinstance(node, ast.Tuple | ast.List):
        return bool(node.elts) and all(numeric(e) for e in node.elts)
    if isinstance(node, ast.Call) and callee(node) == "timedelta":
        return all(numeric(a) for a in node.args) and all(numeric(k.value) for k in node.keywords)
    return False


def unit_name(node: ast.AST) -> bool:
    """A constant's name in upper case, as a unit or another bound is."""
    if isinstance(node, ast.Attribute):
        return node.attr.isupper()
    return isinstance(node, ast.Name) and node.id.lstrip("_").isupper()


def trivial(node: ast.AST) -> bool:
    """Zero, one, or minus one: none, once, or all."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return isinstance(node.operand, ast.Constant) and node.operand.value == 1
    return isinstance(node, ast.Constant) and node.value in (0, 1) and not isinstance(node.value, bool)


def callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def fixed(lines: list[str], node: ast.AST) -> bool:
    """Whether the node's first or last line carries the mark."""
    first = getattr(node, "lineno", 0)
    last = getattr(node, "end_lineno", first) or first
    return any(FIXED.search(lines[n - 1]) for n in {first, last} if 0 < n <= len(lines))


def base_names(cls: ast.ClassDef) -> set[str]:
    found: set[str] = set()
    for base in cls.bases:
        if isinstance(base, ast.Name):
            found.add(base.id)
        elif isinstance(base, ast.Attribute):
            found.add(base.attr)
    return found


def field_default(node: ast.AnnAssign) -> ast.AST | None:
    """The default a field is given: its value, or a `Field(default=...)`'s."""
    value = node.value
    if isinstance(value, ast.Call) and callee(value) == "Field":
        for keyword in value.keywords:
            if keyword.arg == "default":
                return keyword.value
        return value.args[0] if value.args else None
    return value


def constants(tree: ast.Module) -> Iterator[tuple[str, ast.AST, ast.AST]]:
    """Each module or class constant: its name, its statement, its value."""
    scopes: list[list[ast.stmt]] = [tree.body]
    scopes += [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    for body in scopes:
        for node in body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    yield target.id, node, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    yield node.target.id, node, node.value


def matches(rel: str, globs: list[str]) -> bool:
    return any(glob_match(glob, rel) for glob in globs if glob)


def python_findings(project: Project, catalog: str, generated: list[str], exclude: list[str]) -> Iterator[Violation]:
    for file, tree in project.trees():
        if file.rel == catalog or file.rel in generated or matches(file.rel, exclude):
            continue
        text = project.read(file.rel) or ""
        lines = text.splitlines()
        # A value reported as a constant or a field is not reported again as a
        # timedelta written in place.
        seen: set[int] = set()
        for name, node, value in constants(tree):
            seen.add(id(value))
            if name.isupper() and numeric(value) and not fixed(lines, node):
                yield Violation.at(
                    file.rel,
                    node,
                    f"{name} is a number written in place; it is the catalog's ({catalog}), or `# fixed: <why>`",
                )
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            kind = (
                "settings"
                if cls.name.endswith("Settings") or "BaseSettings" in base_names(cls)
                else "options"
                if cls.name.endswith(("Options", "Bounds", "Limits"))
                else None
            )
            for node in cls.body:
                if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                    continue
                if kind is None and not TUNES.search(node.target.id):
                    continue
                default = field_default(node)
                if default is not None:
                    seen.add(id(default))
                if default is not None and numeric(default) and not trivial(default) and not fixed(lines, node):
                    yield Violation.at(
                        file.rel,
                        node,
                        f"{cls.name}.{node.target.id} defaults to a number written in place; "
                        + (
                            "a setting's default is the catalog's"
                            if kind == "settings"
                            else "a field's is a parameter's `.default`, or a live parameter read where it is used"
                        ),
                    )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = callee(node)
                if name in CONTRACTS:
                    continue
                for keyword in node.keywords if name != "timedelta" else ():
                    if (
                        keyword.arg
                        and TUNES.search(keyword.arg)
                        and numeric(keyword.value)
                        and not trivial(keyword.value)
                        and not fixed(lines, keyword.value)
                    ):
                        yield Violation.at(
                            file.rel,
                            keyword.value,
                            f"{name}({keyword.arg}=...) takes a number written in place; read it from the catalog",
                        )
                if (
                    name in TIMERS
                    and node.args
                    and numeric(node.args[-1])
                    and not trivial(node.args[-1])
                    and not fixed(lines, node)
                ):
                    yield Violation.at(
                        file.rel,
                        node,
                        f"{name}() waits a number written in place; read it from the catalog",
                    )
                if (
                    name == "timedelta"
                    and id(node) not in seen
                    and numeric(node)
                    and not all(trivial(k.value) for k in node.keywords)
                    and not fixed(lines, node)
                ):
                    yield Violation.at(file.rel, node, "a timedelta written in place; read it from the catalog")
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                arguments = node.args
                positional = arguments.posonlyargs + arguments.args
                pairs = list(
                    zip(
                        positional[len(positional) - len(arguments.defaults) :],
                        arguments.defaults,
                        strict=True,
                    )
                )
                pairs += [(a, d) for a, d in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True) if d is not None]
                for argument, default in pairs:
                    if TUNES.search(argument.arg) and numeric(default) and not trivial(default) and not fixed(lines, default):
                        yield Violation.at(
                            file.rel,
                            default,
                            f"{node.name}({argument.arg}=...) defaults to a number written in place; read it from the catalog",
                        )


def typescript_findings(project: Project, patterns: list[str], generated: list[str]) -> Iterator[Violation]:
    for rel in project.files(*patterns):
        if rel in generated or rel.endswith((".test.ts", ".test.tsx", ".d.ts")) or "/node_modules/" in rel:
            continue
        for number, line in enumerate((project.read(rel) or "").splitlines(), 1):
            if FIXED.search(line):
                continue
            const = TS_CONST.match(line)
            if const and TS_NUMBER.match(const.group(2).rstrip(";")) and any(c.isdigit() for c in const.group(2)):
                yield Violation(
                    rel,
                    number,
                    1,
                    f"{const.group(1)} is a number written in place; read it from the catalog",
                )
            for found in (*TS_TIMER.finditer(line), *TS_PROP.finditer(line)):
                if found.group(2) not in ("0", "1"):
                    yield Violation(
                        rel,
                        number,
                        found.start() + 1,
                        f"{found.group(1)} takes a number written in place; read it from the catalog",
                    )


def catalog_entries(project: Project, catalog: str) -> list[tuple[ast.Call, dict[str, ast.AST], int]]:
    """Every `Parameter(...)` of the catalog, with its keywords."""
    text = project.read(catalog)
    if text is None:
        return []
    found: list[tuple[ast.Call, dict[str, ast.AST], int]] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and callee(node) == "Parameter":
            keywords: dict[str, ast.AST] = {k.arg: k.value for k in node.keywords if k.arg}
            found.append((node, keywords, node.lineno))
    return found


def catalog_findings(project: Project, catalog: str, review: bool) -> Iterator[Violation]:
    for _, keywords, line in catalog_entries(project, catalog):
        name = keywords.get("name")
        label = str(name.value) if isinstance(name, ast.Constant) else f"line {line}"
        required = ["name", "default", "unit", "applies", "summary"]
        applies = keywords.get("applies")
        if not (isinstance(applies, ast.Name) and applies.id == "CODE"):
            required += ["minimum", "maximum"]
        missing = [k for k in required if k not in keywords]
        if missing:
            yield Violation(catalog, line, 1, f"{label} names no {', '.join(missing)}")
        if review and not isinstance(keywords.get("review"), ast.Call):
            yield Violation(
                catalog,
                line,
                1,
                f"{label} is not reviewed: its `review` names who looked, and when",
            )


def terraform_findings(project: Project, catalog: str, pattern: str) -> Iterator[Violation]:
    sources: dict[str, tuple[str, float]] = {}
    for _, keywords, _ in catalog_entries(project, catalog):
        source, default = keywords.get("source"), keywords.get("default")
        if isinstance(source, ast.Constant) and isinstance(source.value, str) and source.value:
            try:
                value = float(ast.literal_eval(default)) if default is not None else float("nan")
            except ValueError:
                value = float("nan")
            named = keywords.get("name")
            sources[source.value] = (str(named.value) if isinstance(named, ast.Constant) else source.value, value)
    for rel in project.files(pattern):
        text = project.read(rel) or ""
        for variable in TF_VARIABLE.finditer(text):
            found = TF_DEFAULT.search(variable.group(2))
            if found is None or FIXED.search(variable.group(2)):
                continue
            line = text[: variable.start()].count("\n") + 1
            key = f"{rel}#{variable.group(1)}"
            if key not in sources:
                yield Violation(
                    rel,
                    line,
                    1,
                    f"variable {variable.group(1)} defaults to a number no parameter names",
                )
            elif float(found.group(1).replace("_", "")) != sources[key][1]:
                said = f"{sources[key][0]} says {sources[key][1]:g}"
                yield Violation(
                    rel,
                    line,
                    1,
                    f"variable {variable.group(1)} defaults to {found.group(1)}; {said}",
                )
