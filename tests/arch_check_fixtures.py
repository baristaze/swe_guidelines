"""Tiny guideline-shaped projects for the arch-check tests, and a way to run the CLI on one.

`write_project` builds the monorepo layout the guideline prescribes for
a product named `acme`: the uv workspace, `om` with one namespace in
the full namespace shape, `infra` with its exception root, one service
and one worker with their entry points and containers, one ADR, the
Makefile and CI gate, the nine operational skills, the READMEs, and
`llms.txt`. Every shipped rule passes on it. Each test adds, overrides,
or drops the files its rule reads. `check` runs `arch_check.cli.main` on the tree
and returns the exit status and what it printed.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from arch_check.cli import main
from arch_check.rules.ops import SKILLS

WORKSPACE = '[tool.uv.workspace]\nmembers = ["om", "infra", "services/*", "workers/*"]\n\n'
PYPROJECT = WORKSPACE + '[tool.arch-check]\npackage = "acme"\n'
ADR = "docs/adr/0001-root-package.md"


def distribution(name: str, *, deps: tuple[str, ...] = (), script: str | None = None) -> str:
    """A member `pyproject.toml`: a name, its dependencies, and an optional console script."""
    text = f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = {list(deps)!r}\n'.replace("'", '"')
    if script:
        text += f'\n[project.scripts]\n{name} = "{script}"\n'
    return text


def storage_impl(tech: str, ns: str = "Tasks") -> str:
    """One impl of a namespace storage interface; every storage interface has two."""
    return f"from .. import {ns}StorageInterface\n\n\nclass {ns}Storage{tech}Impl({ns}StorageInterface):\n    pass\n"


CONTAINER = (
    "class Container:\n    async def start(self) -> None:\n        pass\n\n    async def close(self) -> None:\n        pass\n"
)

BASE: dict[str, str] = {
    # the object model: one namespace in the shape every namespace has
    "om/pyproject.toml": distribution("acme-om", deps=("acme-infra",)),
    "om/README.md": "# Object model\n\nThe nouns of acme. Each namespace has its own page.\n",
    "om/tests/__init__.py": "",
    "om/src/acme/om/__init__.py": "",
    "om/src/acme/om/base.py": (
        "from datetime import UTC, datetime\nfrom uuid import UUID, uuid7\n\nfrom pydantic import BaseModel, ConfigDict\n\n\n"
        "def new_id() -> UUID:\n    return uuid7()\n\n\n"
        "def utcnow() -> datetime:\n    return datetime.now(UTC)\n\n\n"
        'class Platform(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n\n'
        'PROVENANCE_FIELDS = frozenset({"created_at", "created_by", "deleted_at", "deleted_by"})\n'
        "EMPTY_UUID = UUID(int=0)\n"
    ),
    "om/src/acme/om/root.py": "from acme.infra import InfraRoot\n",
    "om/src/acme/om/tasks/README.md": "# Tasks\n\nA task is one unit of work.\n",
    "om/src/acme/om/tasks/__init__.py": "from .manager import TasksManagerInterface\n",
    "om/src/acme/om/tasks/manager.py": "from abc import ABC\n\n\nclass TasksManagerInterface(ABC):\n    pass\n",
    "om/src/acme/om/tasks/impl/__init__.py": "",
    "om/src/acme/om/tasks/impl/manager.py": "from ...root import build\n",
    "om/src/acme/om/tasks/types/__init__.py": "",
    "om/src/acme/om/tasks/storage/__init__.py": "from abc import ABC\n\n\nclass TasksStorageInterface(ABC):\n    pass\n",
    "om/src/acme/om/tasks/storage/impl/__init__.py": "",
    "om/src/acme/om/tasks/storage/impl/memory.py": storage_impl("Memory"),
    "om/src/acme/om/tasks/storage/impl/postgres.py": storage_impl("Postgres"),
    "om/src/acme/om/tasks/storage/tables/__init__.py": "",
    # tenancy: the swimlane every OM has
    "om/src/acme/om/tenancy/README.md": "# Tenancy\n\nAn org and the people in it.\n",
    "om/src/acme/om/tenancy/__init__.py": "from .manager import TenancyManagerInterface\n",
    "om/src/acme/om/tenancy/manager.py": "from abc import ABC\n\n\nclass TenancyManagerInterface(ABC):\n    pass\n",
    "om/src/acme/om/tenancy/impl/__init__.py": "",
    "om/src/acme/om/tenancy/types/__init__.py": "",
    "om/src/acme/om/tenancy/storage/__init__.py": "from abc import ABC\n\n\nclass TenancyStorageInterface(ABC):\n    pass\n",
    "om/src/acme/om/tenancy/storage/impl/__init__.py": "",
    "om/src/acme/om/tenancy/storage/impl/memory.py": storage_impl("Memory", "Tenancy"),
    "om/src/acme/om/tenancy/storage/impl/postgres.py": storage_impl("Postgres", "Tenancy"),
    "om/src/acme/om/tenancy/storage/tables/__init__.py": "",
    # the storage root: two impls, every member built in the constructor
    "om/src/acme/om/storage/__init__.py": (
        "from abc import ABC, abstractmethod\n\nfrom acme.om.tasks.storage import TasksStorageInterface\n"
        "from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl\n"
        "from acme.om.tasks.storage.impl.postgres import TasksStoragePostgresImpl\n"
        "from acme.om.tenancy.storage import TenancyStorageInterface\n"
        "from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl\n"
        "from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl\n\n\n"
        "class StorageInterface(ABC):\n"
        "    @abstractmethod\n    def get_tasks_storage(self) -> TasksStorageInterface: ...\n\n"
        "    @abstractmethod\n    def get_tenancy_storage(self) -> TenancyStorageInterface: ...\n\n"
        "    @abstractmethod\n    async def healthcheck(self) -> bool: ...\n\n"
        "    @abstractmethod\n    async def close(self) -> None: ...\n\n\n"
        "class StoragePostgresImpl(StorageInterface):\n"
        "    def __init__(self) -> None:\n        self._tasks = TasksStoragePostgresImpl()\n"
        "        self._tenancy = TenancyStoragePostgresImpl()\n\n"
        "    def get_tasks_storage(self) -> TasksStorageInterface:\n        return self._tasks\n\n"
        "    def get_tenancy_storage(self) -> TenancyStorageInterface:\n        return self._tenancy\n\n"
        "    async def healthcheck(self) -> bool:\n        return True\n\n"
        "    async def close(self) -> None:\n        pass\n\n\n"
        "class StorageMemoryImpl(StorageInterface):\n"
        "    def __init__(self) -> None:\n        self._tasks = TasksStorageMemoryImpl()\n"
        "        self._tenancy = TenancyStorageMemoryImpl()\n\n"
        "    def get_tasks_storage(self) -> TasksStorageInterface:\n        return self._tasks\n\n"
        "    def get_tenancy_storage(self) -> TenancyStorageInterface:\n        return self._tenancy\n\n"
        "    async def healthcheck(self) -> bool:\n        return True\n\n"
        "    async def close(self) -> None:\n        pass\n"
    ),
    # infra: its own exception root
    "infra/pyproject.toml": distribution("acme-infra"),
    "infra/README.md": "# Infra\n\nThe clients of the platform services.\n",
    "infra/tests/__init__.py": "",
    "infra/src/acme/infra/__init__.py": (
        'class InfraException(Exception):\n    http_status = 500\n    code = "infra"\n\n\nclass InfraRoot:\n    pass\n'
    ),
    "infra/src/acme/infra/cache/__init__.py": "",
    # one service and one worker, each a distribution with an entry point
    "services/api/pyproject.toml": distribution("acme-api", deps=("acme-om",), script="acme.services.api.main:main"),
    "services/api/README.md": "# API\n\nThe public API of acme.\n",
    "services/api/tests/__init__.py": "",
    "services/api/src/acme/services/api/__init__.py": "from acme.om.root import build\n",
    "services/api/src/acme/services/api/container.py": CONTAINER,
    "services/api/src/acme/services/api/main.py": "def main() -> None:\n    pass\n",
    "services/api/src/acme/services/api/routers/__init__.py": "",
    "services/api/src/acme/services/api/types/__init__.py": "",
    "workers/maintenance/pyproject.toml": distribution(
        "acme-maintenance", deps=("acme-om",), script="acme.workers.maintenance.main:main"
    ),
    "workers/maintenance/README.md": "# Maintenance\n\nThe worker that sweeps.\n",
    "workers/maintenance/tests/__init__.py": "",
    "workers/maintenance/src/acme/workers/maintenance/__init__.py": "from acme.om import tasks\n",
    "workers/maintenance/src/acme/workers/maintenance/container.py": CONTAINER,
    "workers/maintenance/src/acme/workers/maintenance/main.py": "def main() -> None:\n    pass\n",
    # the records, the gates, and the operational surface
    "Makefile": "check:\n\tuv run pytest\n\nopenapi:\n\tuv run acme-api openapi\n",
    ".github/workflows/ci.yml": "jobs:\n  check:\n    steps:\n      - run: make openapi && git diff --exit-code\n",
    ADR: (
        "# 1. One root package\n\nDate: 2026-01-01\n\n## Context\n\nOne product.\n\n"
        "## Decision\n\nOne root package.\n\n## Consequences\n\nShort imports.\n"
    ),
    **{f".claude/skills/{name}/SKILL.md": f"---\nname: {name}\n---\n" for name in SKILLS},
    "llms.txt": (
        "# acme\n\n> The acme platform.\n\n## Users\n\n- [Object model](om/README.md): the nouns\n\n"
        "## Operators\n\n- [Infra](infra/README.md): the platform services\n\n"
        "## Developers\n\n- [API](services/api/README.md): the public API\n"
    ),
}


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_project(root: Path, files: Mapping[str, str | None] | None = None, *, pyproject: str | None = PYPROJECT) -> Path:
    """Write the base tree under `root`, then `files` over it; a `None` text drops a base file, `pyproject=None` writes none."""
    if pyproject is not None:
        write(root, "pyproject.toml", pyproject)
    for rel, text in {**BASE, **(files or {})}.items():
        if text is not None:
            write(root, rel, text)
    return root


def check(root: Path, *args: str) -> tuple[int, str, str]:
    """Run the CLI on `root`; (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--root", str(root), *args])
    return code, out.getvalue(), err.getvalue()


def check_json(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """Run the CLI with `--format json`; (exit status, the parsed report)."""
    code, out, err = check(root, "--format", "json", *args)
    assert out, err
    return code, json.loads(out)


def rules_found(report: dict[str, Any]) -> list[tuple[str, str, int]]:
    """(rule, path, line) of every finding in a JSON report, in report order."""
    return [(f["rule"], f["path"], f["line"]) for f in report["findings"]]
