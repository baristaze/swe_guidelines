"""Imports and the archive over the live app, on the local store: the CSV
file's upload started under its own purpose, the import started naming it
(202, under the idempotency record), read, listed, and resumed; the file a
purpose other than the import's refused; the steps the worker would take
run through the managers, so the import reads parked on Free's bound; and an
archived task listed apart from the done list and restored."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import seed_request, sign_in

from acme.om.base import utcnow
from acme.om.opcontext import OpContext
from acme.om.orchestrations.types.orchestration import OrchestrationStatus
from acme.om.tasks.types.task import TaskStatus
from acme.services.api.container import AppContainer

CSV = b"title,notes\n" + b"".join(f"Task {n},\n".encode() for n in range(1, 16))


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    data: bytes = CSV,
    name: str = "tasks.csv",
    content_type: str = "text/csv",
) -> httpx.Response:
    """The whole upload a client makes against a store that cannot presign."""
    started = await client.post(
        "/v1/tasks/imports/files",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"name": name, "content_type": content_type, "size_bytes": len(data)},
    )
    if started.status_code != 201:
        return started
    file_id = started.json()["id"]
    put = await client.put(f"/v1/media/files/{file_id}/content", headers=headers, content=data)
    assert put.status_code == 200, put.text
    return await client.post(f"/v1/media/files/{file_id}/confirm", headers=headers)


async def start(client: httpx.AsyncClient, headers: dict[str, str], file_id: str) -> httpx.Response:
    return await client.post(
        "/v1/tasks/imports",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"file_id": file_id},
    )


@pytest.fixture
async def free_owner(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    """The owner of an org on Free, whose ten active tasks an import meets."""
    return await sign_in(client, container, plan=None)


async def worker_context(container: AppContainer, headers: dict[str, str]) -> OpContext:
    """The context the worker's claim builds for the person who asked."""
    ctx = await container.managers.tenancy.authenticate(
        seed_request(), headers["Authorization"].removeprefix("Bearer ")
    )
    return await container.managers.tenancy.service_context(seed_request(), ctx.org_id, ctx.user_id)


async def test_an_import_is_started_read_listed_and_parks_on_free(
    client: httpx.AsyncClient, container: AppContainer, free_owner: dict[str, str]
) -> None:
    stored = await upload(client, free_owner)
    assert stored.status_code == 200, stored.text
    assert (stored.json()["purpose"], stored.json()["status"]) == ("task_import", "stored")
    accepted = await start(client, free_owner, stored.json()["id"])
    assert accepted.status_code == 202, accepted.text
    started = accepted.json()
    assert started["status"] == "running" and started["file_id"] == stored.json()["id"]
    assert (started["cursor"], started["created"], started["total"]) == (0, 0, None)

    # The worker's steps, as it would take them.
    ctx = await worker_context(container, free_owner)
    record = await container.managers.orchestrations.get(ctx, UUID(started["id"]))
    while record.status is OrchestrationStatus.RUNNING:
        record = await container.managers.tasks.step_import(ctx, record)

    read = await client.get(f"/v1/tasks/imports/{started['id']}", headers=free_owner)
    assert read.status_code == 200, read.text
    parked = read.json()
    assert (parked["status"], parked["park_reason"]) == ("parked", "plan_limit")
    assert (parked["cursor"], parked["created"], parked["total"]) == (10, 10, 15)
    listed = await client.get("/v1/tasks/imports", headers=free_owner)
    assert [i["id"] for i in listed.json()["items"]] == [started["id"]]
    resumed = await client.post(f"/v1/tasks/imports/{started['id']}/resume", headers=free_owner)
    assert resumed.status_code == 200 and resumed.json()["status"] == "running"
    again = await client.post(f"/v1/tasks/imports/{started['id']}/resume", headers=free_owner)
    assert again.json()["status"] == "running", "a running import is answered as it is"


async def test_a_file_that_is_not_an_import_is_refused(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    refused = await upload(
        client, owner, b"%PDF-1.7", name="plan.pdf", content_type="application/pdf"
    )
    assert refused.status_code == 422, refused.text
    too_large = await client.post(
        "/v1/tasks/imports/files",
        headers={**owner, "Idempotency-Key": str(uuid4())},
        json={"name": "big.csv", "content_type": "text/csv", "size_bytes": 1024 * 1024 + 1},
    )
    assert too_large.status_code == 422, too_large.text
    task = await client.post("/v1/tasks", headers=owner, json={"title": "Ship"})
    attachment = await client.post(
        f"/v1/tasks/{task.json()['id']}/attachments",
        headers={**owner, "Idempotency-Key": str(uuid4())},
        json={"name": "rows.csv", "content_type": "text/csv", "size_bytes": 10},
    )
    assert attachment.status_code == 201, attachment.text
    not_an_import = await start(client, owner, attachment.json()["id"])
    assert not_an_import.status_code == 422, not_an_import.text
    assert (await client.get(f"/v1/tasks/imports/{uuid4()}", headers=owner)).status_code == 404


async def test_an_archived_task_leaves_the_done_list_and_is_restored(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    created = await client.post("/v1/tasks", headers=owner, json={"title": "Long done"})
    task_id = UUID(created.json()["id"])
    ctx = await worker_context(container, owner)
    storage = container.storage.get_tasks_storage()
    task = await storage.read_task(ctx.org_id, task_id)
    assert task is not None
    aged = task.model_copy(
        update={
            "status": TaskStatus.DONE,
            "updated_at": utcnow() - timedelta(days=100),
            "version": task.version + 1,
        }
    )
    await storage.update_task(ctx.org_id, aged, task.version, ())
    record = await container.managers.tasks.open_cleanup(ctx)
    assert record is not None
    await container.managers.tasks.step_cleanup(ctx, record)

    done = await client.get("/v1/tasks?status=done", headers=owner)
    assert done.json()["items"] == []
    archived = await client.get("/v1/tasks/archived", headers=owner)
    (shown,) = archived.json()["items"]
    assert shown["id"] == str(task_id) and shown["archived_at"] is not None
    read = await client.get(f"/v1/tasks/{task_id}", headers=owner)
    assert read.status_code == 200 and read.json()["archived_at"] is not None
    unnamed = await client.post(f"/v1/tasks/{task_id}/restore", headers=owner, json={})
    assert unnamed.status_code == 422
    stale = await client.post(
        f"/v1/tasks/{task_id}/restore",
        headers=owner,
        json={"expected_version": shown["version"] + 5},
    )
    assert stale.status_code == 412
    restored = await client.post(
        f"/v1/tasks/{task_id}/restore",
        headers=owner,
        json={"expected_version": shown["version"]},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None
    done = await client.get("/v1/tasks?status=done", headers=owner)
    assert [t["id"] for t in done.json()["items"]] == [str(task_id)]
    twice = await client.post(
        f"/v1/tasks/{task_id}/restore",
        headers=owner,
        json={"expected_version": restored.json()["version"]},
    )
    assert twice.status_code == 422, "a task that is not archived is not restored"
