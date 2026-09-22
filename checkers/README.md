# arch-check

`arch-check` is the static checker of the guideline. Many lenses are
syntactic: an import direction, a class shape, a signature, a banned
call. A review by an agent decides those slowly, at a cost, and now
and then wrongly. A parser decides them in a second, offline, the same
way every time. So the checker takes the mechanical lenses, and the
review skills keep the judgment calls.

It reads source with `ast` and never imports the code it checks. It
depends on nothing but the Python standard library, version 3.11 or
later.

## Running it

A project pins it to the guideline release it follows:

```bash
uvx --python 3.14 --from "git+https://github.com/baristaze/swe_guidelines@v0.29.0#subdirectory=checkers" arch-check
```

`--python` names the Python the project pins in `.python-version`.
The checker parses with its own interpreter's grammar, so on an older
Python than the project pins it refuses to run and exits 2, rather
than misread newer syntax as a file that does not parse.

From a checkout of this repository, or from the copy inside the
installed plugin, it runs without installing:

```bash
python3 checkers/arch_check.py --root path/to/project
```

The flags:

| Flag | Does |
|------|------|
| `PATHS...` | Report findings only under these paths. The whole project is still read. |
| `--root DIR` | The project root. The default is the nearest directory whose `pyproject.toml` has `[tool.arch-check]`, else the current one. |
| `--package NAME` | The product's import root, over the configured one. |
| `--group om,storage` | Only the rules of these lens groups. |
| `--rule CON-10,CON-12` | Only these rules. |
| `--format text\|json` | `path:line:col: RULE message` lines, or one JSON document. |
| `--list` | Print every rule: id, group, coverage, severity, origin, summary. |

## Exit status

- `0`: clean.
- `1`: findings.
- `2`: a configuration or usage error. Bad TOML, an ADR that does not
  exist, an unknown rule id or option key, a `src` or `exclude` glob
  that is empty, absolute, or climbs out of the root, a local rule
  that fails to load.

A file that does not parse is a `PARSE` finding, never a crash. A
broken inline ignore is an `IGNORE` finding.

## Rules and lenses

A rule's id is the id of the lens it decides. `CON-12` the rule is
`CON-12` the lens. Its severity is the lens's severity. Its coverage
says how much of the lens it decides: `full` means the whole lens, and
`partial` means the rule decides a named mechanical part and a review
judges the rest. A lens with no rule is judged by a review alone.

A rule ships only when it is deterministic and rarely wrong on a tree
shaped the way the guideline prescribes. A rule that would have to
guess stays with the review. Partial coverage is often the honest
answer.

## Configuration

The configuration is `[tool.arch-check]` in the project's root
`pyproject.toml`. Every key has a default that matches the monorepo
layout the guideline prescribes, so a scaffolded project names only
its package:

```toml
[tool.arch-check]
package = "acme"
# src = ["om/src", "infra/src", "integrations/src", "gateway/src", "services/*/src",
#        "workers/*/src", "apps/*/src", "clients/*/src", "ops/src"]
# exclude = ["**/migrations/**"]
# local = ["tools/arch_check"]
```

The default `src` is every Python distribution of the layout in
Monorepo Folder Structure. `src` and `exclude` are globs relative to
the root. `**` spans directories; `*` stays inside one.

A rule that needs the project's own names reads them as options, one
table per rule id. Each rule documents its keys and defaults them to
the names the guideline uses. A key the rule does not read exits 2,
whether or not the rule runs. A value of the wrong type exits 2 when
the rule runs:

```toml
[tool.arch-check.options.CTX-26]
sites = ["om/src/acme/om/tenancy/impl/manager.py"]
```

With no table at all, the checker still runs. The package is
`--package`, or the one package directory under `om/src/`, and nothing
is disabled or excepted. So a review can run it on a project that
never adopted it.

## Exceptions

Turning a rule off, or letting one file break it, is a deviation from
the guideline. A deviation is written down as an ADR before the checker
accepts it. Each entry names an ADR file, and that file must exist, or
the run exits 2.

```toml
[[tool.arch-check.disable]]
rule = "OM-09"
adr = "docs/adr/0003-no-mixins.md"
reason = "one line"

[[tool.arch-check.exception]]
rule = "CON-12"
path = "om/src/acme/om/legacy/*.py"
adr = "docs/adr/0012-legacy-hook.md"
reason = "one line"
```

One line can carry its own exception. The comment names the rule and
an ADR number, and `docs/adr/NNNN-*.md` must exist. In a Python file
it is a real comment; the same text inside a string or a docstring is
not an ignore:

```python
from acme.services.api import app  # arch-check: ignore[CON-12] ADR-0012
```

An exception that matches nothing is itself a finding. So is an
inline ignore on a line the rule does not flag. Two exceptions that
match the same finding are both in use. An exception never
outlives the code it excused.

## Adding a rule

A rule is one module under `src/arch_check/rules/`. The loader imports
every module there; there is nothing else to wire.

```python
from collections.abc import Iterator

from arch_check.model import Violation
from arch_check.project import Project, base_names, classes, last
from arch_check.registry import rule

LIFECYCLE = {"Trackable", "SoftDeletable"}


@rule("OM-06", coverage="partial", summary="No event or audit entry composes Trackable or SoftDeletable.")
def append_only_records_carry_identity(project: Project) -> Iterator[Violation]:
    for file, tree in project.trees(project.sub("om")):
        for cls in classes(tree):
            if cls.name.endswith(("Event", "AuditEntry")) and LIFECYCLE & {last(b) for b in base_names(cls)}:
                yield Violation.at(file.rel, cls, f"{cls.name} composes a lifecycle mixin; it is Identifiable only")
```

The registration refuses an id no lens has, and an id a shipped
rule already decides. Group and severity come
from the lens. A rule that reads options names their keys in the
registration, `@rule("STO-05", options=("sql_dir",), ...)`, and reads
each with `project.option`; a key the registration does not name
exits 2 before the run. `Project` holds the parsed tree: the Python files under
the source roots, each module's name, its imports resolved to absolute
names, and any other file of the repository by glob. `project.py` also
holds the `ast` helpers for classes, bases, decorators, and
signatures.

A rule comes with tests in `tests/test_arch_check_<group>.py`: a tree
that passes and a tree that fails, built with
`tests/arch_check_fixtures.py`. Before it ships, it runs clean on a
real project built the way the guideline prescribes. A finding there
is a defect in that project or a defect in the rule; it is never left
standing.

## Project-local rules

A project can keep rules of its own. They live in its tree, in the
directories `local` names:

```toml
[tool.arch-check]
package = "acme"
local = ["tools/arch_check"]
```

Every `*.py` file there is loaded and registers rules with the same
`@rule` a shipped rule uses, `options=` included. A file whose name starts with `_` is
skipped. Each file stands alone: it imports `arch_check` and the
standard library, never a sibling. A file that fails to load stops the
run with exit 2 and its path.

A local rule names a lens that exists and a coverage. It can decide a
lens no shipped rule decides, or add to one a shipped rule decides in
part. It can never take a lens a shipped rule decides whole. The
listing and the JSON mark it `local`; a shipped rule is `guideline`.

Write a local rule when the check needs the project's own logic, not
only its names. A different package name or source layout is
configuration, not a rule. And a rule two projects write the same way
belongs in this package.
