"""One small repository tree, valid under every checker, that each test bends.

The scripts locate their inputs through module-level paths derived from
`ROOT`; `repo` builds the tree under a temporary directory and points
each imported script at it, so a test edits a file and calls `main()`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# The benchmark harness is importable the same way, so `test_benchmark_*.py`
# reads `harness.*` with nothing installed: every module there imports the
# standard library only at import time.
BENCHMARK = Path(__file__).resolve().parent.parent / "benchmark"
if str(BENCHMARK) not in sys.path:
    sys.path.insert(0, str(BENCHMARK))

# The static checker is importable from its source tree, so
# `test_arch_check_*.py` reads `arch_check` without installing it. It
# needs Python 3.11 (`tomllib`); those modules skip themselves on 3.10.
CHECKERS = Path(__file__).resolve().parent.parent / "checkers" / "src"
if str(CHECKERS) not in sys.path:
    sys.path.insert(0, str(CHECKERS))

GUIDELINE = """\
# Software Design and Architecture Guidelines

## Contents

<!-- toc -->
- [Interfaces](#interfaces)
  - [Principles](#principles)
- [The Storage Layer](#the-storage-layer)
  - [Principles](#principles-1)
  - [Tables](#tables)
<!-- /toc -->

## Interfaces

### Principles

Every layer talks to the next through an interface. See
[The Storage Layer](#the-storage-layer) and [its principles](#principles-1).

``` python
# not a heading: fenced code
```

## The Storage Layer

### Principles

Storage is behind an interface.

### Tables

One table per entity.
"""

LENSES_README = """\
# Lenses

## Groups

| Group id | File    | Covers                              |
|----------|---------|-------------------------------------|
| `om`     | `om.md` | Interfaces: interfaces, principles  |

## Lens format

Ids are the group prefix plus a two-digit number.
"""

LENS_OM = """\
# Object Model

Group id: `om`.

## OM-01 Interfaces first

**Principle.** Every layer talks to the next through an interface.

**Source.** Interfaces, Principles.

**Look for.** Direct calls across a layer boundary.

**Violation.** A concrete class is imported where an interface is
declared.

**Severity.** high

## OM-02 Tables are per entity

**Principle.** One table per entity.

**Source.** The Storage Layer, Tables; Principles.

**Look for.** Tables holding two entities.

**Violation.** A table with a discriminator column.

**Severity.** medium
"""

TEMPLATE = """\
---
name: arch-review-{group}
description: "Review code through the {title} lenses. Covers {covers}. Use as one leg of arch-review-full."
allowed-tools: Read, Grep, Bash(git diff:*)
---

# arch-review-{group}

Read `${CLAUDE_SKILL_DIR}/../../lenses/{group}.md` and the guideline at
`${CLAUDE_SKILL_DIR}/../../architecture.md`. Take the scope from
`git diff`. Never edit, stage, or commit.
"""

REVIEW_FULL = """\
---
name: arch-review-full
description: "Full review: runs arch-review-om and merges the report."
allowed-tools: Read, Agent
---

# arch-review-full

Run `arch-review-om` on the scope and merge the reports.
"""

PLUGIN = '{"name": "swe-guidelines", "version": "1.2.3"}\n'
MARKETPLACE = '{"name": "swe-guidelines", "plugins": [{"name": "swe-guidelines", "version": "1.2.3"}]}\n'
CHANGELOG = "# Changelog\n\n## Unreleased\n\n## 1.2.3 (2026-01-01)\n\n- First.\n\n## 1.2.2 (2025-12-01)\n\n- Older.\n"
CHECKERS_PYPROJECT = '[project]\nname = "swe-guidelines-arch-check"\nversion = "1.2.3"\nrequires-python = ">=3.11"\n'
CHECKERS_README = "# arch-check\n\nPin it: `git+https://example.com/swe_guidelines@v1.2.3#subdirectory=checkers`.\n"
CHECKERS_INIT = '"""arch-check."""\n\n__version__ = "1.2.3"\n'
ADOPTING = "# Adopting\n\nAdd the marketplace from a tag: `git#v1.2.3`, pinned at `v1.2.3`.\n"
README = (
    "# Software Design and Architecture Guidelines\n\n"
    "See [the lenses](lenses/README.md#groups): 2 lenses in one group. Install from `v1.2.3`.\n"
)

SCAFFOLD = """\
---
name: arch-scaffold-thing
description: "Create a thing the way the guideline prescribes."
allowed-tools: Read, Write, Bash(make check)
---

# arch-scaffold-thing

## Input

`<name>`

## Created

| File | Holds |
|------|-------|
| `thing.py` | the thing |

## Changed

| File | Change |
|------|--------|
| `root.py` | one getter |

## Procedure

1. Write the thing.
2. Run `make check`.

## Output

One line.
"""


def render_template(group: str, title: str, covers: str) -> str:
    return TEMPLATE.replace("{group}", group).replace("{title}", title).replace("{covers}", covers)


class Repo:
    """A fixture tree plus the scripts pointed at it."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.monkeypatch = monkeypatch

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def edit(self, rel: str, old: str, new: str) -> None:
        text = self.read(rel)
        assert old in text, f"{rel} does not contain {old!r}"
        self.write(rel, text.replace(old, new))

    def script(self, name: str):
        """Import a script and repoint every path it derives from ROOT."""
        mod = importlib.import_module(name)
        for attr, value in vars(mod).items():
            if isinstance(value, Path) and attr.isupper():
                rel = value.relative_to(SCRIPTS.parent)
                self.monkeypatch.setattr(mod, attr, self.root / rel)
        return mod


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    r = Repo(tmp_path, monkeypatch)
    r.write("architecture.md", GUIDELINE)
    r.write("README.md", README)
    r.write("CHANGELOG.md", CHANGELOG)
    r.write("docs/adopting.md", ADOPTING)
    r.write("lenses/README.md", LENSES_README)
    r.write("lenses/om.md", LENS_OM)
    r.write("skills/_template/review.SKILL.md", TEMPLATE)
    r.write(
        "skills/arch-review-om/SKILL.md",
        render_template("om", "Object Model", "Interfaces: interfaces, principles"),
    )
    r.write("skills/arch-review-full/SKILL.md", REVIEW_FULL)
    r.write("skills/arch-scaffold-thing/SKILL.md", SCAFFOLD)
    r.write(".claude-plugin/plugin.json", PLUGIN)
    r.write(".claude-plugin/marketplace.json", MARKETPLACE)
    r.write("checkers/pyproject.toml", CHECKERS_PYPROJECT)
    r.write("checkers/README.md", CHECKERS_README)
    r.write("checkers/src/arch_check/__init__.py", CHECKERS_INIT)
    return r
