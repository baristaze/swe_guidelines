#!/usr/bin/env python3
"""Run arch-check from a checkout, without installing it.

`python3 checkers/arch_check.py [ARGS]` puts `checkers/src` first on
`sys.path` and calls the same `main` as the `arch-check` console script,
so a review skill can run the copy that ships inside the plugin.
"""

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    print("arch-check: error: needs Python 3.11 or later", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from arch_check.cli import main

if __name__ == "__main__":
    sys.exit(main())
