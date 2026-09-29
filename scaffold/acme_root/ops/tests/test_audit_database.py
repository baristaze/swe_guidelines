"""The audit tools against the local Postgres, end to end: a database of the
run's own is made and migrated, seeded at a small scale, read through a
plan and the inventory, driven by the call counter, and dropped. The seed
is written against the schema, so this is what says it still fits."""

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from auditdb import create, drop, exists
from explain import inventory, plans, rows
from seed import FIXED, seed

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
NAME = "audit_tools_selftest"
HEAVY = FIXED["heavy_org"]


@pytest.fixture
def database() -> Iterator[str]:
    """Made and dropped outside the test's loop: each step runs its own, as
    the command does."""
    if asyncio.run(exists(NAME)):
        drop(NAME)
    create(NAME)
    try:
        yield NAME
    finally:
        drop(NAME)


async def test_a_run_makes_seeds_reads_and_drops_its_own_database(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed(database, 0.002)
    statements = tmp_path / "statements.sql"
    statements.write_text(
        f"-- name: open list, team\n-- scope: {HEAVY}\n"
        f"SELECT id FROM core.tasks WHERE org_id = '{HEAVY}' AND status = 'open' "
        "AND deleted_at IS NULL ORDER BY rank, id LIMIT 51;\n"
        "-- name: generic, first\n-- scope: system\n-- params: 1\nSELECT $1::int;\n"
        "-- name: generic, second\n-- scope: system\n-- params: 2\nSELECT $1::int + 1;\n"
        "-- name: the tenant fence holds\n-- scope: system\n"
        "DELETE FROM queue.work_items WHERE status = 'done';\n"
    )
    await plans(database, statements)
    await inventory(database)
    await rows(database, f"SELECT slug FROM core.orgs WHERE id = '{HEAVY}'")
    printed = capsys.readouterr().out
    assert "#### open list, team (runtime" in printed
    assert "#### the tenant fence holds (system" in printed
    assert printed.count(", generic plan)") == 2  # a prepared statement never outlives its plan
    assert "| core.tasks |" in printed and "| activity.events |" in printed
    assert printed.rstrip().endswith("heavy")

    results = tmp_path / "calls.json"
    ran = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            str(ROOT / "ops" / "audit" / "dbcalls.py"),
            "run",
            database,
            "--only",
            "health",
            "--out",
            str(results),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "0 flows failed" in ran.stdout
    folded = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(ROOT / "ops" / "audit" / "dbcalls.py"), "summary", str(results)],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    summary = folded.stdout
    assert "| cli | bootstrap (acme-api bootstrap) | 1 |" in summary
    assert "| health | GET /healthz | 1 | 0 | 0 | none | 200 |" in summary


async def test_the_seed_refuses_a_database_not_named_for_an_audit() -> None:
    with pytest.raises(SystemExit):
        await seed("acme", 0.001)
