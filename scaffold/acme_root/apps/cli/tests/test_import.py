"""`acme import` over the in-process API and a scripted channel: the file
uploaded and its import started, then followed as the worker's steps land
(run here through the managers, as the worker runs them) until it stops;
the line each outcome ends on, and its exit code."""

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from api_support import OWNER, seed_request
from cli_support import Stack

from acme.apps.cli import imports, main
from acme.client.client import ApiClient
from acme.client.envelopes import EntityChanged
from acme.client.realtime import State
from acme.client.types import ImportView
from acme.om.base import new_id
from acme.om.orchestrations.types.orchestration import OrchestrationStatus


def worker_steps(stack: Stack) -> imports.OpenChannel:
    """A channel whose pushes are the worker's steps: after the first open,
    each step of the org's newest import is taken through the managers and
    pushed, as the socket would carry it."""

    async def channel(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        await on_first_open()
        managers = stack.container.managers
        owner = await managers.tenancy.authenticate(
            seed_request(), stack.session_token(OWNER["email"])
        )
        ctx = await managers.tenancy.service_context(seed_request(), owner.org_id, owner.user_id)
        (record,) = (await managers.tasks.get_imports(ctx, 1)).items
        seq = 0
        while record.status is OrchestrationStatus.RUNNING:
            record = await managers.tasks.step_import(ctx, record)
            seq += 1
            yield EntityChanged(
                kind="orchestrations.orchestration.updated",
                target_id=record.id,
                seq=seq,
                actor_id=owner.user_id,
            )

    return channel


@pytest.fixture
def follows_the_worker(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    channel = worker_steps(stack)

    async def follow(client: ApiClient, import_id: UUID) -> ImportView:
        return await imports.follow(client, import_id, channel=channel)

    monkeypatch.setattr(main, "follow", follow)


@pytest.mark.usefixtures("follows_the_worker")
def test_an_import_is_uploaded_started_and_followed_to_its_end(
    stack: Stack, tmp_path: Path
) -> None:
    rows = tmp_path / "tasks.csv"
    rows.write_text("title,due_on\n" + "".join(f"Task {n},\n" for n in range(1, 151)) + ",\n")
    result = stack.acme("import", str(rows))
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-3:] == [
        "created 0 of ?, skipped 0",
        "created 100 of 150, skipped 0",
        "imported: created 150 of 150, skipped 0",
    ]
    listed = stack.acme("ls", "--json")
    assert '"Task 150"' in listed.output


@pytest.mark.usefixtures("follows_the_worker")
def test_a_file_that_is_not_csv_fails_the_import_and_exits_1(stack: Stack, tmp_path: Path) -> None:
    rows = tmp_path / "tasks.csv"
    rows.write_text("name,notes\nno,title\n")
    result = stack.acme("import", str(rows))
    assert result.exit_code == 1
    assert result.output.splitlines()[-1] == "failed: no title column (created 0 of ?, skipped 0)"


def view(**fields: object) -> ImportView:
    return ImportView.model_validate(
        {
            "id": str(new_id()),
            "file_id": str(new_id()),
            "status": "running",
            "total": 50,
            "cursor": 10,
            "created": 10,
            "skipped": 0,
            "row_errors": [],
            "park_reason": None,
            "fail_reason": None,
            "created_at": "2026-09-25T09:00:00Z",
            "updated_at": "2026-09-25T09:00:00Z",
            "finished_at": None,
            "created_by": str(new_id()),
            **fields,
        }
    )


def test_a_parked_import_says_where_it_stopped_and_what_lifts_it() -> None:
    parked = imports.outcome(view(status="parked", park_reason="plan_limit"))
    assert parked.startswith("parked at row 11: the plan's active tasks are full")
    assert "Settings, Billing" in parked and "Resume" in parked
    assert imports.outcome(view(status="failed", fail_reason="too_many_rows")) == (
        "failed: too many rows (created 10 of 50, skipped 0)"
    )
