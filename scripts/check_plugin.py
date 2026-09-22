"""Validate the plugin manifest with Claude Code, strictly but for one warning.

`claude plugin validate .` reads the marketplace manifest, never
`plugin.json`, so the plugin itself goes unvalidated. Validated on its
own with `--strict`, it fails on one warning: the repository's CLAUDE.md
sits at the plugin root, and a plugin does not load it. That file is the
contributors' instructions, not plugin context, and it stays. So this
runs the validation without `--strict` and fails on an error or on any
warning but that one.
"""

from __future__ import annotations

import subprocess
import sys

MANIFEST = ".claude-plugin/plugin.json"
EXPECTED = "CLAUDE.md at the plugin root is not loaded as project context"


def problems(output: str) -> list[str]:
    """The warning and error lines of a validation, less the one expected."""
    lines = [line.strip() for line in output.splitlines()]
    flagged = [line for line in lines if line.startswith("\u276f")]
    return [line for line in flagged if EXPECTED not in line]


def main() -> int:
    run = subprocess.run(["claude", "plugin", "validate", MANIFEST], capture_output=True, text=True)
    output = run.stdout + run.stderr
    left = problems(output)
    if run.returncode != 0 or left:
        print(output, file=sys.stderr)
        for line in left:
            print(f"check_plugin: {line}", file=sys.stderr)
        return 1
    print(f"plugin ok: {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
