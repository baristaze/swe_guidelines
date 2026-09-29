"""The files that decide what the migrate task does to a database.

The deploy runs the migrate task only when a fingerprint of these files
changes (deployment/migration-inputs.json, read by the environment module).
A file that shapes the migration and is missing from the list would let a
release skip a migration the database lacks, so the list is held here to
the code: every migration file, and every module of this repository the
migrate command imports.
"""

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "deployment" / "migration-inputs.json"


def patterns() -> list[str]:
    return json.loads(INPUTS.read_text())["paths"]


def covered(path: str) -> bool:
    """Terraform's `fileset` semantics: `*` within one segment, `**` across
    any number, none included."""
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns())


def test_every_migration_file_is_an_input() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "om/migrations"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert tracked, "git lists no migration files"
    missing = [path for path in tracked if not covered(path)]
    assert missing == [], f"migration files outside deployment/migration-inputs.json: {missing}"


def test_every_module_the_migrate_command_imports_is_an_input() -> None:
    """The runner, the logins, the roles, and the settings the URLs come
    from; the migrations directory's env.py imports nothing beyond them. A
    fresh interpreter, so what other tests imported does not count."""
    probe = (
        "import json, sys\n"
        "import acme.om.storage.migrate\n"
        "print(json.dumps([m.__file__ for m in list(sys.modules.values())"
        " if getattr(m, '__file__', None)]))\n"
    )
    files = json.loads(
        subprocess.run(
            [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    )
    ours = sorted(
        str(Path(f).resolve().relative_to(ROOT))
        for f in files
        if Path(f).resolve().is_relative_to(ROOT) and ".venv" not in Path(f).parts
    )
    assert "om/src/acme/om/storage/migrate.py" in ours
    missing = [path for path in ours if not covered(path)]
    assert missing == [], f"modules the migrate command imports, outside the inputs: {missing}"


def test_the_inputs_hold_no_compiled_or_stray_files() -> None:
    """A pattern by extension, so a developer's __pycache__ never changes the
    fingerprint a plan from a laptop computes."""
    for pattern in patterns():
        assert not pattern.endswith("**"), f"{pattern} would match compiled files"
