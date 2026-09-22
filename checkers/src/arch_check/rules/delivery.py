"""The delivery lenses a parser can decide: layout, images, tooling, the client stack, exceptions, logs, ADRs, versions.

The layout rules read directories and TOML with the standard library.
The Dockerfile and version rules read single lines, and only lines
that decide the question alone: an instruction, a tag, a declared
version. Compose files, workflows, and Terraform are never read as a
structure, since the standard library parses neither YAML nor HCL.
"""

from __future__ import annotations

import ast
import builtins
import re
import sys
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project, SourceFile, base_names, classes, dotted, is_under, last
from arch_check.registry import rule
from arch_check.rules._text_util import (
    Instruction,
    call_name,
    const_str,
    dockerfile,
    imported_names,
    is_dir,
    is_dockerfile,
    is_file,
    kwarg,
    load_json,
    load_toml,
    make_targets,
    module_matches,
    npm_dependencies,
    resolved,
    subdirs,
    subtable,
    unparseable,
    walk,
    workspace_members,
)

# --- DEL-07


@rule(
    "DEL-07",
    coverage="partial",
    summary="One distribution under om/, none under deployment/ or scripts/, no worker under services/ or the reverse.",
)
def grouped_by_role(project: Project) -> Iterator[Violation]:
    """The OM is one distribution, and each role folder holds its own role.

    `om/` holds exactly one `pyproject.toml`, at `om/pyproject.toml`.
    No `pyproject.toml` under `deployment/` or `scripts/`. No file under
    `services/` is a module of `<pkg>.workers`, and none under
    `workers/` is a module of `<pkg>.services`. Domain code outside
    `om/` and the choice of top-level folders are judged.
    """
    if is_dir(project, "om"):
        for rel in walk(project, "om", names=("pyproject.toml",)):
            if rel != "om/pyproject.toml":
                yield Violation(rel, 1, 1, "a second distribution under om/; the OM is one distribution")
    for folder in ("deployment", "scripts"):
        for rel in walk(project, folder, names=("pyproject.toml",)):
            yield Violation(rel, 1, 1, f"a Python distribution under {folder}/; application code lives in its role folder")
    wrong = {"services": project.sub("workers"), "workers": project.sub("services")}
    reported: set[str] = set()
    for f in project.python_files:
        top, _, rest = f.rel.partition("/")
        owner = f"{top}/{rest.split('/')[0]}"
        if top in wrong and is_under(f.module, wrong[top]) and owner not in reported:
            reported.add(owner)
            yield Violation(
                f.rel, 1, 1, f"{f.module} sits under {top}/; a worker lives under workers/, a service under services/"
            )


# --- DEL-08

SYS_PATH_EDITS = {"insert", "append", "extend"}


def edits_sys_path(tree: ast.AST) -> ast.AST | None:
    for node in ast.walk(tree):
        func = node.func if isinstance(node, ast.Call) else None
        if isinstance(func, ast.Attribute) and func.attr in SYS_PATH_EDITS and dotted(func.value) == "sys.path":
            return node
        if isinstance(node, ast.AugAssign) and dotted(node.target) == "sys.path":
            return node
    return None


def members(project: Project) -> list[str]:
    """The Python distributions: the uv workspace members, else the parents of the source roots that hold a `pyproject.toml`."""
    found = workspace_members(project)
    if found is not None:
        return found
    return sorted({project.rel(p.parent) for p in project.src_roots if (p.parent / "pyproject.toml").is_file()})


@rule(
    "DEL-08",
    coverage="partial",
    summary="Every distribution has src/<root> and a tests/ sibling; one root package, not a stdlib name; no sys.path edits.",
)
def src_layout(project: Project) -> Iterator[Violation]:
    """Every Python distribution uses the src layout with tests beside it, under one product root package.

    For each uv workspace member (else each parent of a source root
    that holds a `pyproject.toml`): `src/` and `tests/` exist; `src/`
    holds one package, the configured root package; no `tests/`
    folder, `test_*.py`, or `conftest.py` inside `src/`. The root
    package is not a standard-library module name. No `conftest.py` in
    the tree edits `sys.path`. Other test imports that resolve to the
    source tree are judged.
    """
    root = project.package.split(".")[0]
    if root in sys.stdlib_module_names:
        yield Violation("pyproject.toml", 1, 1, f"the root package {root!r} shadows a standard-library module")
    for member in members(project):
        where = f"{member}/pyproject.toml"
        if not is_dir(project, f"{member}/src"):
            yield Violation(where, 1, 1, f"{member} has no src/; every distribution uses the src/<root> layout")
            continue
        if not is_dir(project, f"{member}/tests"):
            yield Violation(where, 1, 1, f"{member} has no tests/ beside src/")
        packages = [
            p.name
            for p in (project.root / member / "src").iterdir()
            if p.is_dir() and not p.name.startswith((".", "_")) and not p.name.endswith((".egg-info", ".dist-info"))
        ]
        if packages != [root]:
            listed = ", ".join(sorted(packages)) or "nothing"
            yield Violation(where, 1, 1, f"{member}/src holds {listed}; it holds the one root package {root!r}")
        for rel in walk(project, f"{member}/src", names=("test_*.py", "*_test.py", "conftest.py")):
            yield Violation(rel, 1, 1, "a test inside src/; tests live in the tests/ sibling")
        for d in (project.root / member / "src").rglob("tests"):
            if d.is_dir() and not any(part in {".venv", "node_modules"} for part in d.parts):
                yield Violation(project.rel(d), 1, 1, "a tests/ folder inside src/; tests live in the tests/ sibling")
    for rel in walk(project, names=("conftest.py",)):
        text = project.read(rel)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        node = edits_sys_path(tree)
        if node is not None:
            yield Violation.at(rel, node, "conftest.py edits sys.path; tests run against the installed package")


# --- DEL-09


@rule(
    "DEL-09",
    coverage="partial",
    summary="Each service and worker has a console script and a main.py; a worker has no routers or types, a service has both.",
)
def one_project_shape(project: Project) -> Iterator[Violation]:
    """Workers and services share one project shape.

    Each folder under `services/` and `workers/` has a `pyproject.toml`
    with a `[project.scripts]` entry and a
    `src/<root>/<role>/<name>/main.py`. A worker has no `routers/` or
    `types/` package; a service has both. The subcommands a service's
    binary offers are judged.
    """
    root = project.package.split(".")[0]
    for role in ("services", "workers"):
        for folder in subdirs(project, role):
            where = f"{folder}/pyproject.toml"
            data = load_toml(project, where)
            if data is None:
                broken = unparseable(project, where)
                yield broken or Violation(folder, 1, 1, f"{folder} has no pyproject.toml; a {role[:-1]} is a distribution")
                continue
            if not subtable(data, "project").get("scripts"):
                yield Violation(where, 1, 1, f"{folder} declares no [project.scripts] console entry point")
            packages = sorted(p for p in (project.root / folder / "src" / root / role).glob("*") if p.is_dir())
            packages = [p for p in packages if not p.name.startswith(("_", "."))]
            if not any((p / "main.py").is_file() for p in packages):
                yield Violation(where, 1, 1, f"{folder} has no src/{root}/{role}/<name>/main.py behind its entry point")
            for p in packages:
                has = {n: (p / n).is_dir() for n in ("routers", "types")}
                if role == "workers":
                    for name, present in has.items():
                        if present:
                            yield Violation(project.rel(p / name), 1, 1, f"a worker with {name}/; workers serve no API")
                elif (p / "main.py").is_file():
                    for name, present in has.items():
                        if not present:
                            yield Violation(where, 1, 1, f"{project.rel(p)} has no {name}/; a service has routers and types")


# --- DEL-10

UNPRIVILEGED = re.compile(r"(^|[/-])(unprivileged|nonroot|rootless)([:@-]|$)|:nonroot")


def image_of(args: str) -> str:
    """The image a `FROM` names: flags such as `--platform` dropped, `AS name` dropped."""
    words = [w for w in args.split() if not w.startswith("--")]
    return words[0] if words else ""


def stage_alias(args: str) -> str | None:
    """The name a `FROM ... AS name` gives its stage, lowercased; None when it names none."""
    words = [w for w in args.split() if not w.startswith("--")]
    return words[2].lower() if len(words) >= 3 and words[1].upper() == "AS" else None


LOCKED_ENV = re.compile(r"\bUV_(FROZEN|LOCKED)(=|\s+)[\"']?(?!0\b|false\b)[^\s\"']")


def stage_user(stages: list[list[Instruction]], index: int, listed: list[str]) -> tuple[Instruction | None, bool, str]:
    """(its last `USER`, base is non-root by name, base image) of a stage, following `FROM <earlier stage>` there."""
    steps = stages[index]
    base = image_of(steps[0].args)
    users = [s for s in steps if s.keyword == "USER"]
    if users:
        return users[-1], False, base
    earlier = earlier_stage(stages, index)
    if earlier is not None:
        return stage_user(stages, earlier, listed)
    nonroot = bool(UNPRIVILEGED.search(base)) or base.split("@")[0] in listed or base.split(":")[0] in listed
    return None, nonroot, base


def earlier_stage(stages: list[list[Instruction]], index: int) -> int | None:
    """The index of the earlier stage a stage is built `FROM`, or None when its base is an image."""
    aliases = {stage_alias(stages[i][0].args): i for i in range(index)}
    return aliases.get(image_of(stages[index][0].args).lower())


def stage_healthcheck(stages: list[list[Instruction]], index: int) -> Instruction | None:
    """The last `HEALTHCHECK` of a stage, following `FROM <earlier stage>` there as `stage_user` does."""
    checks = [s for s in stages[index] if s.keyword == "HEALTHCHECK"]
    if checks:
        return checks[-1]
    earlier = earlier_stage(stages, index)
    return stage_healthcheck(stages, earlier) if earlier is not None else None


LOCKED_FLAG = re.compile(r"(?<![\w-])--(frozen|locked)(?![\w-])")
"""`--frozen` or `--locked` as a flag of its own, never the head of `--frozen-lockfile`."""


@rule(
    "DEL-10",
    options=("unprivileged_bases",),
    coverage="partial",
    summary="Dockerfiles live in deployment/docker/, build in two stages, install locked, run non-root, declare a HEALTHCHECK.",
)
def dockerfiles(project: Project) -> Iterator[Violation]:
    """Images are built alike from one folder.

    Every `Dockerfile`, `*.Dockerfile`, or `Dockerfile.*` in the tree
    sits in `deployment/docker/`. Each has at least two `FROM` lines;
    its final stage runs as a `USER` that is not `root` or `0`, set in
    that stage or in the earlier stage it is built `FROM`, unless its
    base image is non-root by name (`-unprivileged`, `nonroot`) or
    listed; its final stage declares a `HEALTHCHECK`, in that stage or
    in the earlier stage it is built `FROM`; and every `uv sync` passes
    `--frozen` or `--locked`, or runs after `UV_FROZEN` or `UV_LOCKED`
    is set in its stage or the earlier stage it is built `FROM`, and
    every `pnpm install` passes `--frozen-lockfile`. An `npm install` of
    the project (`npm ci` instead), a `yarn` without `--frozen-lockfile`
    or `--immutable`, and a `pip install` that neither requires hashes
    nor pins each package with `==` are unlocked too. A
    `*.dockerignore` file is not a Dockerfile. What the healthcheck
    probes and the shared entrypoint are judged.

    Option `[tool.arch-check.options.DEL-10]`: `unprivileged_bases`
    (default `[]`), image names whose default user is not root.
    """
    listed: list[str] = project.option("DEL-10", "unprivileged_bases", [], {"unprivileged_bases"})
    for rel in walk(project, names=("Dockerfile", "*.Dockerfile", "Dockerfile.*")):
        if not is_dockerfile(rel.rpartition("/")[2]):
            continue
        if not rel.startswith("deployment/docker/"):
            yield Violation(rel, 1, 1, "a Dockerfile outside deployment/docker/; every image is built from there")
        steps = dockerfile(project, rel)
        froms = [s for s in steps if s.keyword == "FROM"]
        if len(froms) < 2:
            yield Violation(rel, froms[0].line if froms else 1, 1, "a single-stage image; an image builds in two stages")
        if froms:
            stages: list[list[Instruction]] = []
            for s in steps:
                if s.keyword == "FROM":
                    stages.append([s])
                elif stages:
                    stages[-1].append(s)
            user, nonroot_base, base = stage_user(stages, len(stages) - 1, listed)
            if user is not None and user.args.split(":")[0].strip() in {"root", "0"}:
                yield Violation(rel, user.line, 1, "the final stage runs as root; the process runs non-root")
            elif user is None and not nonroot_base:
                yield Violation(rel, froms[-1].line, 1, f"the final stage on {base} sets no USER; the process runs non-root")
            check = stage_healthcheck(stages, len(stages) - 1)
        else:
            stages, check = [], None
        if check is None or check.args.strip().upper() == "NONE":
            yield Violation(rel, 1, 1, "no HEALTHCHECK in the final stage; an image declares one against /healthz")
        # an `ENV UV_FROZEN=1` holds in its stage and in every stage built `FROM` it, never in a stage on an image
        locked_at_end: list[bool] = []
        for index, stage in enumerate(stages):
            earlier = earlier_stage(stages, index)
            locked_env = locked_at_end[earlier] if earlier is not None else False
            for s in stage:
                if s.keyword in {"ENV", "ARG"} and LOCKED_ENV.search(s.args):
                    locked_env = True
                if s.keyword != "RUN":
                    continue
                locked = locked_env or bool(LOCKED_FLAG.search(s.args)) or bool(LOCKED_ENV.search(s.args))
                if re.search(r"\buv\s+sync\b", s.args) and not locked:
                    yield Violation(rel, s.line, 1, "`uv sync` without --frozen or --locked; an image installs from the lock")
                if re.search(r"\bpnpm\s+(install|i)\b", s.args) and "--frozen-lockfile" not in s.args:
                    yield Violation(rel, s.line, 1, "`pnpm install` without --frozen-lockfile; an image installs from the lock")
                for message in unlocked_installs(s.args):
                    yield Violation(rel, s.line, 1, message)
            locked_at_end.append(locked_env)


NPM_INSTALL = re.compile(r"\bnpm\s+(install|i)\b(?P<rest>[^&|;\n]*)")
YARN_INSTALL = re.compile(r"\byarn(\s+install)?[ \t]*(?=$|&|\||;|\n)|\byarn\s+install\b[^&|;\n]*", re.MULTILINE)
PIP_INSTALL = re.compile(r"\b(?:uv\s+)?pip3?\s+install\b(?P<rest>[^&|;\n]*)")
NPM_VALUE_FLAGS = frozenset(
    {"--prefix", "--registry", "--cache", "--userconfig", "--omit", "--include", "--workspace", "-w", "--tag"}
)
PIP_VALUE_FLAGS = frozenset(
    {
        "-t",
        "--target",
        "--prefix",
        "--root",
        "--src",
        "-i",
        "--index-url",
        "--extra-index-url",
        "-f",
        "--find-links",
        "-c",
        "--constraint",
        "--cache-dir",
        "--trusted-host",
        "--python",
        "--python-version",
        "--platform",
        "--implementation",
        "--abi",
        "--only-binary",
        "--no-binary",
        "--index-strategy",
        "--resolution",
        "--prerelease",
    }
)
"""The flags of `npm install` and `pip install` (`uv pip install` too) whose next word is their value, not a package."""
EXACT_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def packages(words: list[str], value_flags: frozenset[str]) -> list[str]:
    """The words of an install that name a package: flags dropped, and the value after a flag that takes one."""
    out: list[str] = []
    skip = False
    for w in words:
        if skip:
            skip = False
        elif w.startswith("-"):
            skip = w in value_flags
        else:
            out.append(w)
    return out


def npm_pinned(spec: str) -> bool:
    """Whether an npm package spec names an exact version: `tool@1.2.3`, never `tool@latest` or `tool@^1.2`."""
    _, at, version = spec.lstrip("@").rpartition("@")
    return bool(at) and EXACT_VERSION.match(version) is not None


def unlocked_installs(args: str) -> list[str]:
    """The installs of one RUN line that read no lock: `npm install` of the
    project (not `npm ci`), `yarn` without --frozen-lockfile or --immutable,
    and a `pip install` that neither requires hashes nor pins each package
    it names with `==`."""
    out: list[str] = []
    for m in NPM_INSTALL.finditer(args):
        named = packages(m.group("rest").split(), NPM_VALUE_FLAGS)
        # `npm install -g tool@1.2.3` installs one pinned tool, not the project
        if not named or not all(npm_pinned(w) for w in named):
            out.append("`npm install` without a lock; an image runs `npm ci`, or installs a tool at a pinned version")
    for m in YARN_INSTALL.finditer(args):
        if "--frozen-lockfile" not in m.group(0) and "--immutable" not in m.group(0):
            out.append("`yarn` without --frozen-lockfile or --immutable; an image installs from the lock")
    for m in PIP_INSTALL.finditer(args):
        rest = m.group("rest")
        if "--require-hashes" in rest:
            continue
        words = rest.split()
        if "-r" in words or "--requirement" in words or any(w.startswith("--requirement=") for w in words):
            out.append("`pip install -r` without --require-hashes; an image installs from a locked, hashed list")
            continue
        if any("==" not in w for w in packages(words, PIP_VALUE_FLAGS)):
            out.append("`pip install` of an unpinned package; an image pins each one with == or requires hashes")
    return out


# --- DEL-11

LINT_TABLES = ("ruff", "mypy", "pyright", "pyrefly", "black", "isort")
LINT_FILES = ("ruff.toml", ".ruff.toml", "pyrightconfig.json", "mypy.ini", ".mypy.ini")


@rule(
    "DEL-11",
    options=("python_workspace", "typescript_workspace"),
    coverage="partial",
    summary="The workspaces are declared at the root, lint and type config live only there, and the Makefile has check.",
)
def workspace_tooling_at_the_root(project: Project) -> Iterator[Violation]:
    """Workspace tooling lives at the root, and `make check` is the fast gate.

    The root `pyproject.toml` declares `[tool.uv.workspace]`. When an
    `apps/*/package.json` exists, the root has `package.json` and
    `pnpm-workspace.yaml`. No member `pyproject.toml` carries a lint or
    type-check table, and no lint or type-check file sits below the
    root. The `Makefile` has a `check` target. What `check` runs and
    what CI adds are judged.

    Options `[tool.arch-check.options.DEL-11]`: `python_workspace`
    (default `"uv"`) and `typescript_workspace` (default `"pnpm"`), the
    workspace tools. A recorded substitution is told to the checker
    here, not with a `disable`, which is a deviation. With another
    tool named, the root declaration of that workspace is judged.
    """
    keys = {"python_workspace", "typescript_workspace"}
    python_tool = project.option("DEL-11", "python_workspace", "uv", keys)
    typescript_tool = project.option("DEL-11", "typescript_workspace", "pnpm", keys)
    if python_tool == "uv" and workspace_members(project) is None:
        yield Violation("pyproject.toml", 1, 1, "the root pyproject.toml declares no [tool.uv.workspace]")
    if project.files("apps/*/package.json"):
        for rel in ("package.json", "pnpm-workspace.yaml"):
            if rel == "pnpm-workspace.yaml" and typescript_tool != "pnpm":
                continue
            if not is_file(project, rel):
                yield Violation(rel, 1, 1, f"no root {rel}; one TypeScript workspace declares the apps")
    for rel in walk(project, names=("pyproject.toml",)):
        if rel == "pyproject.toml":
            continue
        broken = unparseable(project, rel)
        if broken is not None:
            yield broken
            continue
        tool = subtable(load_toml(project, rel), "tool")
        for name in LINT_TABLES:
            if name in tool:
                yield Violation(rel, 1, 1, f"[tool.{name}] in a member; lint and type config live at the root")
    for rel in walk(project, names=LINT_FILES):
        if "/" in rel:
            yield Violation(rel, 1, 1, "a lint or type-check config below the root; it lives at the root")
    if is_file(project, "Makefile") and "check" not in make_targets(project):
        yield Violation("Makefile", 1, 1, "no `check` target; `make check` is the fast gate")


# --- DEL-12 and DEL-13

OTHER_FRAMEWORKS = ("next", "nuxt", "vue", "svelte", "solid-js", "webpack", "parcel", "gatsby", "astro")
OTHER_SCOPES = ("@remix-run/", "@angular/", "@sveltejs/", "@builder.io/qwik")
OTHER_STATE = (
    "redux",
    "@reduxjs/toolkit",
    "react-redux",
    "mobx",
    "mobx-react",
    "mobx-react-lite",
    "mobx-state-tree",
    "jotai",
    "recoil",
    "valtio",
    "swr",
    "@apollo/client",
    "urql",
)


def browser_apps(project: Project) -> Iterator[tuple[str, set[str] | Violation]]:
    """Each app manifest with the packages it names, or the finding that it does not parse."""
    for rel in project.files("apps/*/package.json"):
        yield rel, unparseable(project, rel) or npm_dependencies(load_json(project, rel))


@rule(
    "DEL-12",
    options=("framework", "bundler"),
    coverage="partial",
    summary="Every browser app depends on the framework and the bundler (react, vite) and on no other; apps/cli is Python.",
)
def react_on_vite(project: Project) -> Iterator[Violation]:
    """Browser apps are React on Vite, and the CLI is Python.

    Each `apps/*/package.json`, together with the root `package.json`,
    names the framework and the bundler, and the app names none of the
    other frameworks or bundlers (Next, Nuxt, Remix, Angular, Vue,
    Svelte, Solid, webpack, Parcel, and the like). `apps/cli`, when it
    exists, has a `pyproject.toml` and no `package.json`. Which hosts
    the bundle calls is judged.

    Options `[tool.arch-check.options.DEL-12]`: `framework` (default
    `"react"`) and `bundler` (default `"vite"`), the npm packages the
    guideline names. A substitution recorded in an ADR, another view
    library for React, is told to the checker here, and the checker
    then treats the substitute as the named technology. A `disable`
    would record it as a deviation, which it is not.
    """
    keys = {"framework", "bundler"}
    framework = project.option("DEL-12", "framework", "react", keys)
    bundler = project.option("DEL-12", "bundler", "vite", keys)
    root_deps = npm_dependencies(load_json(project, "package.json"))
    broken = unparseable(project, "package.json")
    if broken is not None:
        yield broken
    for rel, deps in browser_apps(project):
        if rel == "apps/cli/package.json":
            continue
        if isinstance(deps, Violation):
            yield deps
            continue
        for need in (framework, bundler):
            if need not in deps | root_deps:
                yield Violation(rel, 1, 1, f"no {need} dependency; a browser app is {framework} on {bundler}")
        for dep in sorted(deps):
            if dep in {framework, bundler}:
                continue
            if dep in OTHER_FRAMEWORKS or dep.startswith(OTHER_SCOPES):
                yield Violation(rel, 1, 1, f"depends on {dep}; a browser app is {framework} on {bundler} and nothing else")
    cli = "apps/cli"
    if is_dir(project, cli) and (is_file(project, f"{cli}/package.json") or not is_file(project, f"{cli}/pyproject.toml")):
        yield Violation("apps/cli", 1, 1, "apps/cli is not a Python distribution; the CLI is Python")


@rule(
    "DEL-13",
    options=("server_state", "client_state"),
    coverage="partial",
    summary="No browser app depends on a state library other than TanStack Query and Zustand.",
)
def query_and_zustand(project: Project) -> Iterator[Violation]:
    """Server state lives in TanStack Query and client state in Zustand, and nothing else.

    No `apps/*/package.json` names another state or data library
    (Redux, MobX, Jotai, Recoil, Valtio, SWR, Apollo, urql) unless it
    is the one a recorded substitution names. The key factory, and what
    a store or a realtime handler holds, are judged.

    Options `[tool.arch-check.options.DEL-13]`: `server_state` (default
    `"@tanstack/react-query"`) and `client_state` (default
    `"zustand"`), the npm packages the guideline names. A substitution
    recorded in an ADR is told to the checker here, and the checker
    then treats the substitute as the named technology. A `disable`
    would record it as a deviation, which it is not.
    """
    keys = {"server_state", "client_state"}
    server = project.option("DEL-13", "server_state", "@tanstack/react-query", keys)
    client = project.option("DEL-13", "client_state", "zustand", keys)
    for rel, deps in browser_apps(project):
        if isinstance(deps, Violation):
            yield deps
            continue
        for dep in sorted(deps):
            if dep in OTHER_STATE and dep not in {server, client}:
                yield Violation(rel, 1, 1, f"depends on {dep}; server state is {server}, client state {client}")


# --- DEL-18 and DEL-29

EXCEPTION_BUILTINS = frozenset(
    n for n in dir(builtins) if isinstance(getattr(builtins, n), type) and issubclass(getattr(builtins, n), BaseException)
)
ROOTS = frozenset({"PlatformException", "InfraException"})


def class_bases(project: Project, *prefixes: str) -> dict[str, set[str]]:
    """Class name to the last segments of its bases, over every class under `prefixes`; one name unions its classes."""
    out: dict[str, set[str]] = {}
    for _, tree in project.trees(*prefixes):
        for cls in classes(tree):
            out.setdefault(cls.name, set()).update(n for n in (last(b) for b in base_names(cls)) if n)
    return out


def reaches(name: str, bases: dict[str, set[str]], targets: frozenset[str], seen: set[str] | None = None) -> bool:
    """Whether a class name reaches one of `targets` through the bases named in `bases`."""
    if name in targets:
        return True
    seen = seen if seen is not None else set()
    if name in seen:
        return False
    seen.add(name)
    return any(reaches(b, bases, targets, seen) for b in bases.get(name, ()))


def platform_prefixes(project: Project) -> tuple[str, ...]:
    return tuple(project.sub(n) for n in ("om", "infra", "integrations", "gateway", "services", "workers"))


@rule(
    "DEL-18",
    coverage="partial",
    summary="Every platform exception roots at PlatformException or InfraException; the OM imports no web framework.",
)
def exceptions_root_at_the_platform(project: Project) -> Iterator[Violation]:
    """Every platform exception carries a status and a code by its root, and the OM never formats HTTP.

    Every public class under `<pkg>.om`, `infra`, `integrations`,
    `gateway`, `services`, or `workers` that derives from a built-in
    exception (followed by name across those packages) derives from
    `PlatformException` or `InfraException`. A class whose name starts
    with `_` is private and exempt. Nothing under `<pkg>.om` imports
    `fastapi` or `starlette`. Shapes, and unavailable versus 500, are
    judged.
    """
    prefixes = platform_prefixes(project)
    bases = class_bases(project, *prefixes)
    for file, tree in project.trees(*prefixes):
        for cls in classes(tree):
            if cls.name.startswith("_") or cls.name in ROOTS:
                continue
            if reaches(cls.name, bases, EXCEPTION_BUILTINS) and not reaches(cls.name, bases, ROOTS):
                yield Violation.at(
                    file.rel, cls, f"{cls.name} is an exception rooted at neither PlatformException nor InfraException"
                )
    for file in project.modules_under(project.sub("om")):
        for imp in project.imports(file):
            if any(is_under(imp.module, w) for w in ("fastapi", "starlette")):
                yield Violation.at(file.rel, imp.node, f"imports {imp.module}; the OM never formats HTTP")


def has_field(cls: ast.ClassDef, name: str) -> bool:
    for stmt in cls.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return True
    return False


def except_names(handler: ast.ExceptHandler) -> list[tuple[ast.expr, str]]:
    if handler.type is None:
        return []
    items = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [(e, n) for e in items if (n := dotted(e))]


@rule(
    "DEL-29",
    coverage="partial",
    summary="InfraException declares http_status and code, and nothing outside infra catches an infra exception by name.",
)
def infra_exception_root(project: Project) -> Iterator[Violation]:
    """Infra has its own exception root, and a boundary translates it by its fields.

    When `<pkg>.infra` exists, a class `InfraException` is defined in
    it and declares `http_status` and `code`. Outside `<pkg>.infra`, no
    `except` names `InfraException`, or a class of infra derived from
    it, imported from `<pkg>.infra`. What an impl raises on a driver
    error is judged.
    """
    infra = project.sub("infra")
    files = project.modules_under(infra)
    if not files:
        return
    roots = [(f, c) for f, t in project.trees(infra) for c in classes(t) if c.name == "InfraException"]
    if not roots:
        yield Violation(files[0].rel, 1, 1, "infra defines no InfraException; infra has its own exception root")
    for file, cls in roots:
        for name in ("http_status", "code"):
            if not has_field(cls, name):
                yield Violation.at(file.rel, cls, f"InfraException declares no {name}; it carries a status and a code")
    bases = class_bases(project, infra)
    family = {name for name in bases if reaches(name, bases, frozenset({"InfraException"}))} | {"InfraException"}
    for file, tree in project.trees():
        if is_under(file.module, infra):
            continue
        names = imported_names(project, file)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            for expr, written in except_names(node):
                full = resolved(written, names)
                # the root itself is what a boundary catches; a subclass is a name from the other side
                if full and is_under(full, infra) and last(full) in family and last(full) != "InfraException":
                    yield Violation.at(file.rel, expr, f"catches {last(full)} by name; a boundary translates by status and code")


# --- DEL-19


def boot(project: Project, file: SourceFile, patterns: list[str]) -> bool:
    return any(module_matches(file.module, f"{project.package}.{p}") for p in patterns)


BOOT_MODULES = [
    "infra.observability",
    "services.*.main",
    "services.*.app",
    "services.*.container",
    "workers.*.main",
    "workers.*.container",
    "apps.*.main",
    "ops.main",
]
LOGGING_LIBRARIES = ("structlog", "loguru", "eliot", "logbook")


def logging_objects(tree: ast.AST, names: dict[str, str]) -> set[str]:
    """The names a module binds to something built by `logging`: `log = logging.getLogger(__name__)`."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.Call)
            and is_under(call_name(node.value, names) or "", "logging")
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out.update(n for t in targets if (n := dotted(t)))
    return out


def is_logging_receiver(node: ast.expr, names: dict[str, str], bound: set[str]) -> bool:
    """Whether an expression is a `logging` object: `logging.root`, `logging.getLogger(...)`, or a name bound to one."""
    if isinstance(node, ast.Call):
        return is_under(call_name(node, names) or "", "logging")
    name = dotted(node)
    return bool(name) and (name in bound or is_under(resolved(name, names) or "", "logging"))


@rule(
    "DEL-19",
    options=("boot_modules", "library"),
    coverage="partial",
    summary="Loggers come from getLogger(__name__); only the boot modules configure logging; no second logging library.",
)
def standard_logging(project: Project) -> Iterator[Violation]:
    """Every module logs through `logging.getLogger(__name__)`, and logging is configured once, at boot.

    Outside the boot modules: every `logging.getLogger(...)` passes
    `__name__`, and nothing calls `basicConfig`, `dictConfig`, or
    `fileConfig`, or calls `addHandler`, `setLevel`, or `setFormatter`
    on a `logging` object (`logging.root`, a `logging.getLogger(...)`,
    or a name the module bound to one). Nothing imports `structlog`,
    `loguru`, `eliot`, or `logbook`. How the request id reaches a line
    is judged.

    Options `[tool.arch-check.options.DEL-19]`: `boot_modules` (default
    `["infra.observability", "services.*.main", "services.*.app",
    "services.*.container", "workers.*.main", "workers.*.container",
    "apps.*.main", "ops.main"]`), module names below the root package,
    `*` spanning one segment, where the app container's boot configures
    logging; and `library` (default `"logging"`), the logging library.
    A substitution recorded in an ADR is told to the checker here, and
    that library is then not a second one. A `disable` would record it
    as a deviation, which it is not.
    """
    keys = {"boot_modules", "library"}
    patterns = project.option("DEL-19", "boot_modules", BOOT_MODULES, keys)
    library = project.option("DEL-19", "library", "logging", keys)
    others = [lib for lib in LOGGING_LIBRARIES if lib != library]
    configure = {"addHandler", "setLevel", "setFormatter"}
    for file, tree in project.trees():
        for imp in project.imports(file):
            if any(is_under(imp.module, lib) for lib in others):
                yield Violation.at(file.rel, imp.node, f"imports {imp.module}; {library} is the platform logger")
        if boot(project, file, patterns):
            continue
        names = imported_names(project, file)
        bound = logging_objects(tree, names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            full = call_name(node, names) or ""
            if full == "logging.getLogger":
                arg = node.args[0] if node.args else None
                if not (isinstance(arg, ast.Name) and arg.id == "__name__"):
                    yield Violation.at(file.rel, node, "getLogger without __name__; every module logs under its own name")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in configure
                and is_logging_receiver(node.func.value, names, bound)
            ):
                yield Violation.at(file.rel, node, f"calls {node.func.attr}; logging is configured once, at boot")
            elif full in {"logging.basicConfig", "logging.config.dictConfig", "logging.config.fileConfig"}:
                yield Violation.at(file.rel, node, f"calls {full}; logging is configured once, at boot")


# --- DEL-20


@rule(
    "DEL-20",
    options=("metrics",),
    coverage="partial",
    summary="No second metrics system is imported and no tracer or metrics interface wraps the vendor API.",
)
def telemetry_used_directly(project: Project) -> Iterator[Violation]:
    """Traces and metrics go through OpenTelemetry and the Prometheus client directly.

    Nothing imports `statsd`, `datadog`, or `newrelic`, and no class is
    named `*TracerInterface`, `*MetricsInterface`,
    `*TelemetryInterface`, `PlatformTracer`, or `PlatformMetrics`. A
    branch on whether tracing is configured is judged.

    Option `[tool.arch-check.options.DEL-20]`: `metrics` (default
    `"prometheus_client"`), the metrics library's import name. A
    substitution recorded in an ADR is told to the checker here, and
    that library is then not a second metrics system. A `disable` would
    record it as a deviation, which it is not.
    """
    metrics = project.option("DEL-20", "metrics", "prometheus_client", {"metrics"})
    others = [lib for lib in ("statsd", "datadog", "newrelic") if not is_under(lib, metrics) and not is_under(metrics, lib)]
    wrapper = re.compile(r"^(\w*(Tracer|Metrics|Telemetry)Interface|Platform(Tracer|Metrics))$")
    for file, tree in project.trees():
        for imp in project.imports(file):
            if any(is_under(imp.module, lib) for lib in others):
                yield Violation.at(file.rel, imp.node, f"imports {imp.module}; metrics are {metrics}, used directly")
        for cls in classes(tree):
            if wrapper.match(cls.name):
                yield Violation.at(file.rel, cls, f"{cls.name} wraps the telemetry API; it is used directly")


# --- DEL-23

ADR_FILE = re.compile(r"^(\d{4})-[^/]+\.md$")
CITED = re.compile(r"\bADR[- ]?(\d{4})\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SECTIONS = ("context", "decision", "consequences")
HEADING_NUMBER = re.compile(r"^(\d+(\.\d+)*[.)]?\s+)")


def in_order(headings: list[str]) -> bool:
    """Whether headings starting with Context, Decision, and Consequences appear in that order, others between them."""
    want = 0
    for text in headings:
        words = HEADING_NUMBER.sub("", text).split()
        if want < len(SECTIONS) and words and words[0].rstrip(":") == SECTIONS[want]:
            want += 1
    return want == len(SECTIONS)


CODE_FILES = ("*.py", "*.ts", "*.tsx", "*.js", "*.mjs", "*.toml", "*.tf", "*.sh", "Makefile", "*.Dockerfile", "Dockerfile")


@rule(
    "DEL-23",
    coverage="partial",
    summary="Each ADR is numbered once, dated, with Context, Decision, Consequences; every ADR number cited in code exists.",
)
def adrs_numbered_and_cited(project: Project) -> Iterator[Violation]:
    """Decisions are ADRs under `docs/adr/`, and code cites them by a number that exists.

    Every `docs/adr/NNNN-*.md` carries a date (`YYYY-MM-DD`) and
    headings whose first word, after any number, is Context, Decision,
    and Consequences, in that order (`## Context and Problem
    Statement` and `## 1. Context` count), and
    no number is used twice. Every `ADR NNNN` or `ADR-NNNN` in code,
    config, and workflows names a file that exists. Whether an
    exception to a rule has its ADR is judged.
    """
    numbers: dict[str, str] = {}
    for rel in project.files("docs/adr/*.md"):
        m = ADR_FILE.match(rel.rpartition("/")[2])
        if not m:
            continue
        if m.group(1) in numbers:
            yield Violation(rel, 1, 1, f"ADR {m.group(1)} is also {numbers[m.group(1)]}; a number names one decision")
            continue
        numbers[m.group(1)] = rel
        lines = project.lines(rel)
        if not any(DATE.search(line) for line in lines):
            yield Violation(rel, 1, 1, "an ADR with no date; a record is dated")
        heads = [line.lstrip("#").strip().lower() for line in lines if line.startswith("#")]
        if not in_order(heads):
            yield Violation(rel, 1, 1, "an ADR without Context, Decision, and Consequences headings in that order")
    scanned = list(walk(project, names=CODE_FILES)) + project.files(".github/workflows/*.yml", ".github/workflows/*.yaml")
    for rel in scanned:
        for number, text in enumerate(project.lines(rel), start=1):
            for m in CITED.finditer(text):
                if m.group(1) not in numbers:
                    yield Violation(rel, number, m.start() + 1, f"cites ADR {m.group(1)}, and docs/adr has no {m.group(1)}-*.md")


# --- DEL-26

PRE_RELEASE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)*(?:[-.]?(?:rc|alpha|beta|dev|pre|preview)\d*|(?:a|b)\d+)(?![A-Za-z0-9])")
GIT_SHA = re.compile(r"[0-9a-f]{7,40}")
"""A tag that is a commit id (`7a91c0d`): its digits and letters are hex, never a version and a pre-release suffix."""
DIGEST = re.compile(r"@sha256:[0-9a-f]+")
VERSION = re.compile(r"^\d+(?:\.\d+)*")


def image_tag(image: str) -> tuple[str, str]:
    """(repository, tag) of an image reference; the tag is empty when there is none."""
    image = DIGEST.sub("", image)
    head, _, tail = image.rpartition("/")
    if ":" in tail:
        name, _, tag = tail.partition(":")
        return (f"{head}/{name}" if head else name), tag
    return image, ""


def agree(a: str, b: str) -> bool:
    """Whether two dotted versions name the same release, one as a prefix of the other: 3.14 and 3.14.2 agree."""
    x, y = a.split("."), b.split(".")
    n = min(len(x), len(y))
    return x[:n] == y[:n]


@rule(
    "DEL-26",
    coverage="partial",
    summary="No declared version is a pre-release, and the Python and Node pins agree with the images and CI steps.",
)
def stable_versions_agree(project: Project) -> Iterator[Violation]:
    """Dependencies run on stable releases, and two declarations of one runtime agree.

    No pre-release (`rc`, `alpha`, `beta`, `a1`, `b2`, `dev`, `pre`) in
    `.python-version`, `.nvmrc`, `requires-python`, `packageManager`,
    `engines.node`, a Dockerfile `FROM` tag, a compose `image:` tag, a
    workflow `python-version:` or `node-version:`, or a Terraform
    `required_version`. `.python-version` agrees with every `python`
    image tag and every literal `python-version:` in a workflow, and
    `.nvmrc` with every `node` image and `node-version:`. Whether a
    release is the latest stable, and the lock files, are judged.
    """
    declared: list[tuple[str, int, str]] = []  # (file, line, version text)
    pins: dict[str, tuple[str, int, str]] = {}
    for kind, rel in (("python", ".python-version"), ("node", ".nvmrc")):
        for number, text in enumerate(project.lines(rel), start=1):
            value = text.split("#")[0].strip().lstrip("v")
            if value:
                declared.append((rel, number, value))
                pins.setdefault(kind, (rel, number, value))
                break
    for rel in walk(project, names=("pyproject.toml",)):
        for number, text in enumerate(project.lines(rel), start=1):
            m = re.match(r'^\s*requires-python\s*=\s*"([^"]*)"', text)
            if m:
                declared.append((rel, number, m.group(1)))
    for rel in walk(project, names=("package.json",)):
        for number, text in enumerate(project.lines(rel), start=1):
            m = re.match(r'^\s*"(packageManager|node)"\s*:\s*"([^"]*)"', text)
            if m:
                declared.append((rel, number, m.group(2)))
    images: list[tuple[str, int, str, str]] = []  # (file, line, repository, tag)
    for rel in walk(project, names=("Dockerfile", "*.Dockerfile", "Dockerfile.*")):
        if not is_dockerfile(rel.rpartition("/")[2]):
            continue
        for s in dockerfile(project, rel):
            if s.keyword == "FROM":
                repo, tag = image_tag(image_of(s.args))
                images.append((rel, s.line, repo, tag))
    for rel in walk(project, names=("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")):
        for number, text in enumerate(project.lines(rel), start=1):
            m = re.match(r"^\s*image:\s*[\"']?([^\"'\s#]+)", text)
            if m:
                repo, tag = image_tag(m.group(1))
                images.append((rel, number, repo, tag))
    declared.extend((rel, line, tag) for rel, line, _, tag in images if tag)
    steps: list[tuple[str, int, str, str]] = []  # (file, line, kind, version)
    for rel in project.files(".github/workflows/*.yml", ".github/workflows/*.yaml"):
        for number, text in enumerate(project.lines(rel), start=1):
            m = re.match(r"^\s*(python|node)-version:\s*[\"']?([0-9][^\"'\s#]*)[\"']?\s*(#.*)?$", text)
            if m:
                steps.append((rel, number, m.group(1), m.group(2)))
                declared.append((rel, number, m.group(2)))
    for rel in walk(project, names=("*.tf",)):
        for number, text in enumerate(project.lines(rel), start=1):
            m = re.match(r'^\s*required_version\s*=\s*"([^"]*)"', text)
            if m:
                declared.append((rel, number, m.group(1)))
    for rel, line, text in declared:
        m = None if GIT_SHA.fullmatch(text) else PRE_RELEASE.search(text)
        if m:
            yield Violation(rel, line, 1, f"{text!r} is a pre-release; a dependency runs on a stable release")
    for kind, (pin_rel, _, pin) in pins.items():
        pinned = VERSION.match(pin)
        if not pinned:
            continue
        for rel, line, repo, tag in images:
            got = VERSION.match(tag)
            if repo.rpartition("/")[2] == kind and got and not agree(pinned.group(0), got.group(0)):
                yield Violation(rel, line, 1, f"{repo}:{tag} disagrees with {pin_rel} ({pin}); one runtime, one version")
        for rel, line, step_kind, version in steps:
            got = VERSION.match(version)
            if step_kind == kind and got and not agree(pinned.group(0), got.group(0)):
                yield Violation(rel, line, 1, f"{kind}-version {version} disagrees with {pin_rel} ({pin})")


# --- DEL-35

ID_LABEL = re.compile(r"^(id|ids|\w+_ids?)$")
METRICS = {"Counter", "Histogram", "Gauge", "Summary", "Info", "Enum"}


@rule(
    "DEL-35",
    coverage="partial",
    summary="No Prometheus metric declares an id as a label name.",
)
def bounded_labels(project: Project) -> Iterator[Violation]:
    """Metric labels are bounded: a template, a status, an outcome, never an id.

    A `prometheus_client` metric built with literal label names
    (`labelnames=` or the third argument) names no label `id`, `ids`,
    or one ending in `_id` or `_ids`. The outcome counters and each
    worker's `/metrics` port are judged.
    """
    for file, tree in project.trees():
        names = imported_names(project, file)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            full = call_name(node, names) or ""
            if not (is_under(full, "prometheus_client") and last(full) in METRICS):
                continue
            labels = kwarg(node, "labelnames")
            if labels is None and len(node.args) >= 3:
                labels = node.args[2]
            if isinstance(labels, ast.List | ast.Tuple):
                for elt in labels.elts:
                    label = const_str(elt)
                    if label and ID_LABEL.match(label):
                        yield Violation.at(file.rel, elt, f"label {label!r} is unbounded; a label is never an id")
