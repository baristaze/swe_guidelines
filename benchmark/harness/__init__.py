"""The benchmark harness: providers, scenarios, runtimes, capture, judges, results.

Every module here imports the standard library only at import time. The
provider SDKs, `pyyaml`, `jsonschema`, and `websockets` are imported
inside the functions that need them, so the tests at the repository
root can import the harness with nothing installed.
"""

from __future__ import annotations

__all__ = ["capture", "judge", "providers", "results", "runtime", "scenario"]
