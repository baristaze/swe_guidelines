# arch-check

`arch-check` is the guideline's static checker. Many lenses are
syntactic: an import direction, a class shape, a signature, a banned
call. A parser decides those in a second, offline, the same way every
time. So the checker takes the mechanical lenses, and the review skills
keep the judgment calls.

It reads the project's source with `ast` and never imports it. The one
code of the project it runs is the project's own rules, the files under
`local`, and `--no-local` leaves them out. It needs Python 3.11 or later
and nothing else, so it runs in any CI.

## Running it

A project pins the guideline release it follows:

```bash
uvx --python 3.14 --from "git+https://github.com/baristaze/swe_guidelines@v0.57.0#subdirectory=checkers" arch-check
```

`--python` names the Python the project pins in `.python-version`. On
an older one the checker exits 2, rather than misread newer syntax.
From a checkout of this repository, or the installed plugin, it runs
without installing:

```bash
python3 checkers/arch_check.py --root path/to/project
```

| Flag | Does |
|------|------|
| `PATHS...` | Report findings only under these paths. The whole project is still read. |
| `--root DIR` | The project root. The default is the nearest directory whose `pyproject.toml` has `[tool.arch-check]`, else the current one. |
| `--package NAME` | The product's import root, over the configured one. |
| `--group om,storage` | Only the rules of these lens groups. |
| `--rule CON-10,CON-12` | Only these rules. |
| `--format text\|json` | `path:line:col: RULE message` lines, or one JSON document. |
| `--list` | Print every rule: id, group, coverage, severity, origin, summary. |
| `--no-local` | Leave the project's own rules out, so no file of the project runs. For a tree someone else wrote. |

## Exit status

- `0`: clean.
- `1`: findings.
- `2`: a configuration or usage error, a rule that raises, or a rule
  that runs past 60 seconds. A rule that fails is an `ERROR` finding
  that names it, and every other rule still runs.

A file that does not parse is a `PARSE` finding, never a crash. A
broken inline ignore is an `IGNORE` finding.

<!-- agents-only
A configuration error is bad TOML, a missing ADR, an unknown rule id or
option key, a `src` or `exclude` glob that is empty, absolute, or climbs
out of the root, a local rule that fails to load, or an option value
that is not a name or a non-empty list of names. The 60-second budget
needs a timer signal, so it holds on macOS and Linux and not on Windows.
-->

## Rules and lenses

A rule's id is the id of the lens it decides, and its severity is the
lens's. Its coverage is `full` when it decides the whole lens, or
`partial` when it decides a named part and a review judges the rest. A
rule ships only when it is deterministic and rarely wrong on a tree in
the guideline's shape. A rule that would have to guess stays with the
review.

A rule may name a place it leaves to the review, as CON-01 names a
manager interface past twenty operations. That is no finding, and the
exit status never reads it. The text report marks its line `to judge:`,
and the JSON report lists it under the rule, as `to_judge`.

## Configuration

The configuration is `[tool.arch-check]` in the root `pyproject.toml`.
Every key defaults to the layout the guideline prescribes, so a
scaffolded project names only its package:

```toml
[tool.arch-check]
package = "acme"
# src = ["om/src", "infra/src", "integrations/src", "gateway/src", "services/*/src",
#        "workers/*/src", "apps/*/src", "clients/*/src", "ops/src"]
# exclude = ["**/migrations/**"]
# local = ["tools/arch_check"]
```

`src` and `exclude` are globs from the root: `**` spans directories,
and `*` stays inside one. A rule that needs the project's own names
reads them as options, one table per rule id, each defaulting to the
name the guideline uses. A key the rule does not read exits 2:

```toml
[tool.arch-check.options.CTX-26]
sites = ["om/src/acme/om/tenancy/impl/manager.py"]

[tool.arch-check.options.CON-01]
review_threshold = 24   # a manager interface past this many operations is named for a review; 20 by default
```

`review_threshold` is no limit: no count of operations is a finding.
`max_operations` is another name of the same key, and a table sets one
of the two.

With no table at all, the checker still runs, on the one package under
`om/src/`. So a review can run it on a project that never adopted it.

## Exceptions

Turning a rule off, or letting one file break it, is a deviation, and
its ADR comes first. Each entry's `adr` names a Markdown file under
`docs/adr/` that exists:

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

One line can carry its own exception, in a real comment that names the
rule and an ADR number whose `docs/adr/NNNN-*.md` exists:

```python
from acme.services.api import app  # arch-check: ignore[CON-12] ADR-0012
```

An exception that matches nothing is itself a finding, and so is an
inline ignore on a line the rule does not flag. So an exception never
outlives the code it excused.

## Adding a rule

A rule is one module under `src/arch_check/rules/`. The loader imports
every module there, so there is nothing else to wire.

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

The registration refuses an id no lens has, and an id a shipped rule
already decides. The group and the severity come from the lens. A rule
that reads options names their keys in the registration,
`@rule("STO-05", options=("sql_dir",), ...)`, and reads each with
`project.option`. For a place it names and leaves to the review, a rule
yields `ToJudge.at(...)`, with a violation's arguments. `Project` holds
the parsed tree, and `project.py` the `ast` helpers.

A rule comes with tests in `tests/test_arch_check_<group>.py`: a tree
that passes and one that fails, built with
`tests/arch_check_fixtures.py`. Before it ships, it runs clean on a real
project in the guideline's shape. A finding there is a defect in the
project or in the rule, and it is never left standing.

## Project-local rules

A project keeps rules of its own in the directories `local` names:

```toml
[tool.arch-check]
package = "acme"
local = ["tools/arch_check"]
```

Every `*.py` file there registers rules with the same `@rule`, unless
its name starts with `_`. Each file imports `arch_check` and the
standard library, never a sibling. A local rule names a lens that
exists and a coverage. It may decide a lens no shipped rule decides, or
add to one a shipped rule decides in part, and never take one a shipped
rule decides whole. The listing marks it `local`.

Write one when a check needs the project's own logic, not only its
names; names are options. A rule two projects write the same way
belongs in this package.

A local rule is code, and the checker runs it. It imports every file
under `local` before it does anything else, so `--list` and a run that
exits 2 run them too. In the project's own gate that is right: the tree
is the project's. On a tree someone else wrote, such as a branch checked
out for review, pass `--no-local`. No file of the tree runs then, and the
listing and `rules_run` hold the shipped rules alone. The review skills
pass it, and judge the lenses a local rule decides themselves.

<!-- agents-only
With `--no-local` in a project that names `local`, a `disable`, an
`exception`, an `options` table, or an inline ignore may name any lens a
local rule may take: one no shipped rule decides, or one a shipped rule
decides in part. It is accepted unread. An option key the shipped rule
does not read is accepted under such a lens, and an exception or an
ignore for one is never reported as matching nothing, since the findings
it may excuse are missing. `--rule` still takes only a rule that runs.
-->
