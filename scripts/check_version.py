#!/usr/bin/env python3
"""Check that every copy of the release version agrees with the plugin manifest.

`.claude-plugin/plugin.json` is the one source. The copies it is checked
against:
- `.claude-plugin/marketplace.json`: the `version` of every plugin entry;
- `CHANGELOG.md`: the first `## MAJOR.MINOR.PATCH` heading, the latest
  release (an `## Unreleased` heading above it is fine);
- `README.md`, `docs/adopting.md`, and `checkers/README.md`: every
  `vMAJOR.MINOR.PATCH` tag they tell a project to pin;
- `checkers/pyproject.toml`: the `version` of the arch-check
  distribution;
- `checkers/src/arch_check/__init__.py`: its `__version__`.

`--tag <name>` also checks a release tag: it must be `v` followed by
the version in plugin.json. The release workflow passes the tag it
runs on, so a tag that disagrees with the manifest fails.

Exit status is non-zero when any copy disagrees. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence

from _common import ROOT, parser

PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"
ADOPTING = ROOT / "docs" / "adopting.md"
CHECKERS_README = ROOT / "checkers" / "README.md"
CHECKERS_PYPROJECT = ROOT / "checkers" / "pyproject.toml"
CHECKERS_INIT = ROOT / "checkers" / "src" / "arch_check" / "__init__.py"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b")
TAG = re.compile(r"\bv(\d+\.\d+\.\d+)\b")
# The first `version = "..."` line of checkers/pyproject.toml, which is
# under [project]; the scripts run on Python 3.10, which has no tomllib.
PROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]*)"', re.MULTILINE)
DUNDER_VERSION = re.compile(r'^__version__\s*=\s*"([^"]*)"', re.MULTILINE)


def source() -> str:
    return str(json.loads(PLUGIN.read_text(encoding="utf-8")).get("version", ""))


def check(version: str, errors: list[str]) -> None:
    for i, plugin in enumerate(json.loads(MARKETPLACE.read_text(encoding="utf-8")).get("plugins", [])):
        found = str(plugin.get("version", ""))
        if found != version:
            errors.append(f"{MARKETPLACE.relative_to(ROOT)}: plugins[{i}].version is {found!r}, plugin.json says {version!r}")
    for ln, line in enumerate(CHANGELOG.read_text(encoding="utf-8").splitlines(), start=1):
        m = RELEASE_HEADING.match(line)
        if m:
            if m.group(1) != version:
                errors.append(f"{CHANGELOG.relative_to(ROOT)}:{ln}: latest release is {m.group(1)}, plugin.json says {version}")
            break
    else:
        errors.append(f"{CHANGELOG.relative_to(ROOT)}: no release heading")
    for doc in (ADOPTING, CHECKERS_README, README):
        for ln, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            for m in TAG.finditer(line):
                if m.group(1) != version:
                    errors.append(f"{doc.relative_to(ROOT)}:{ln}: pins v{m.group(1)}, plugin.json says {version}")
    for path, pattern, name in ((CHECKERS_PYPROJECT, PROJECT_VERSION, "version"), (CHECKERS_INIT, DUNDER_VERSION, "__version__")):
        found_version = pattern.search(path.read_text(encoding="utf-8"))
        if found_version is None:
            errors.append(f"{path.relative_to(ROOT)}: no {name}")
        elif found_version.group(1) != version:
            errors.append(f"{path.relative_to(ROOT)}: {name} is {found_version.group(1)}, plugin.json says {version}")


def main(argv: Sequence[str] = ()) -> int:
    cli = parser(__doc__)
    cli.add_argument("--tag", help="a release tag, such as v1.2.3, that must match plugin.json")
    tag = cli.parse_args(list(argv)).tag
    errors: list[str] = []
    version = source()
    if not SEMVER.match(version):
        errors.append(f"{PLUGIN.relative_to(ROOT)}: version {version!r} is not MAJOR.MINOR.PATCH")
    else:
        check(version, errors)
        if tag is not None and tag != f"v{version}":
            errors.append(f"tag {tag!r} does not match plugin.json, which says v{version}")
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} version mismatch(es)")
        return 1
    print(f"version ok: {version}" + (f", tag {tag}" if tag is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
