"""Edge idempotency over the durable record: a retry replays, a reused key
with another body is refused, keys are personal, a request still running
answers 409 to its own retry, a failure after the row landed is followed by
a retry that finds the row, and a request that runs past the pending lease
loses the marker to the retry and cannot finish or release it."""

import asyncio
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from api_support import add_member, sign_in_as

from acme.om.idempotency.impl.manager import IdempotencyOptions
from acme.om.media.types.file import FilePurpose
from acme.om.opcontext import Role
from acme.services.api.container import AppContainer
from acme.services.api.gateway.ratelimit import RateLimited

FILES = "/v1/media/files"
BODY = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": 10}


async def started(
    client: httpx.AsyncClient, container: AppContainer, headers: dict[str, str]
) -> list[str]:
    """The ids of every upload the org started, pending or stored: the rows
    the creates landed. The file list shows stored files only, so the rows
    are read where they are kept."""
    org_id = UUID((await client.get("/v1/orgs/current", headers=headers)).json()["id"])
    rows = await container.storage.get_media_storage().read_files(
        org_id, FilePurpose.UPLOAD, None, None, None, 100
    )
    return [str(row.id) for row in rows]


async def test_a_retry_replays_the_stored_response(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    headers = {**owner, "Idempotency-Key": "file-1"}
    first = await client.post(FILES, headers=headers, json=BODY)
    assert first.status_code == 201, first.text
    second = await client.post(FILES, headers=headers, json=BODY)
    assert second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.headers["content-type"] == first.headers["content-type"]
    assert second.json() == first.json()
    assert await started(client, container, owner) == [first.json()["id"]]


async def test_a_reused_key_with_another_body_is_refused(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    headers = {**owner, "Idempotency-Key": "file-1"}
    assert (await client.post(FILES, headers=headers, json=BODY)).status_code == 201
    reused = await client.post(FILES, headers=headers, json={**BODY, "size_bytes": 11})
    assert reused.status_code == 422, reused.text
    assert reused.json()["error"]["code"] == "validation_failed"
    assert "Idempotent-Replayed" not in reused.headers


async def test_a_refusal_is_replayed_too(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    headers = {**owner, "Idempotency-Key": "blank-1"}
    first = await client.post(FILES, headers=headers, json={**BODY, "name": "a/b.pdf"})
    assert first.status_code == 422 and first.json()["error"]["code"] == "validation_failed"
    second = await client.post(FILES, headers=headers, json={**BODY, "name": "a/b.pdf"})
    assert second.status_code == 422
    assert second.headers["Idempotent-Replayed"] == "true"
    # The replayed refusal names the replaying request, as its header does.
    assert second.json()["error"]["request_id"] == second.headers["x-request-id"]
    assert second.json()["error"]["request_id"] != first.json()["error"]["request_id"]
    assert {k: v for k, v in second.json()["error"].items() if k != "request_id"} == {
        k: v for k, v in first.json()["error"].items() if k != "request_id"
    }


async def test_a_key_longer_than_the_cap_is_refused(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    longest = await client.post(FILES, headers={**owner, "Idempotency-Key": "k" * 255}, json=BODY)
    assert longest.status_code == 201, longest.text
    too_long = await client.post(FILES, headers={**owner, "Idempotency-Key": "k" * 256}, json=BODY)
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["error"]["code"] == "validation_failed"
    assert "idempotency-key" in too_long.json()["error"]["message"]
    assert await started(client, container, owner) == [longest.json()["id"]]


async def test_an_empty_key_is_refused(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """A header present with nothing in it is a malformed request, not a key
    and not an absence: honoured, it would replay every same-body request of
    the caller for the retention as the first one."""
    headers = {**owner, "Idempotency-Key": ""}
    empty = await client.post(FILES, headers=headers, json=BODY)
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"]["code"] == "validation_failed"
    assert "idempotency-key" in empty.json()["error"]["message"]
    assert await started(client, container, owner) == []


async def test_keys_are_personal_inside_a_tenant(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", org_id)
    first = await client.post(FILES, headers={**owner, "Idempotency-Key": "k"}, json=BODY)
    second = await client.post(FILES, headers={**bob, "Idempotency-Key": "k"}, json=BODY)
    assert first.status_code == 201 and second.status_code == 201
    assert "Idempotent-Replayed" not in second.headers
    assert second.json()["id"] != first.json()["id"]


async def test_a_request_still_running_answers_409_to_its_retry(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = container.managers.media.create_file

    async def slow_create(ctx, file):
        entered.set()
        await release.wait()
        return await original(ctx, file)

    monkeypatch.setattr(container.managers.media, "create_file", slow_create)
    headers = {**owner, "Idempotency-Key": "slow-1"}
    first = asyncio.create_task(client.post(FILES, headers=headers, json=BODY))
    await asyncio.wait_for(entered.wait(), timeout=5)

    retry = await client.post(FILES, headers=headers, json=BODY)
    assert retry.status_code == 409, retry.text
    assert retry.json()["error"]["code"] == "idempotency_in_progress"

    release.set()
    assert (await first).status_code == 201
    replay = await client.post(FILES, headers=headers, json=BODY)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"


async def test_a_failure_is_not_an_outcome_the_retry_runs_again(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first attempt fails before anything lands; the marker is released,
    # not finished with a 500, so the retry runs the request again on the same
    # id and the third call replays the success.
    original = container.managers.media.create_file
    failed = False

    async def fail_once(ctx, file):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("the database went away")
        return await original(ctx, file)

    monkeypatch.setattr(container.managers.media, "create_file", fail_once)
    headers = {**owner, "Idempotency-Key": "flaky-1"}
    first = await client.post(FILES, headers=headers, json=BODY)
    assert first.status_code == 500 and failed
    retry = await client.post(FILES, headers=headers, json=BODY)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers
    replay = await client.post(FILES, headers=headers, json=BODY)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert await started(client, container, owner) == [retry.json()["id"]]


async def test_a_429_is_not_an_outcome_the_retry_runs_again(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 429 is an answer about now: the marker is released, not finished with
    # the 429, so the retry runs the request instead of replaying the refusal.
    original = container.managers.media.create_file
    limited = False

    async def limit_once(ctx, file):
        nonlocal limited
        if not limited:
            limited = True
            raise RateLimited(timedelta(seconds=1))
        return await original(ctx, file)

    monkeypatch.setattr(container.managers.media, "create_file", limit_once)
    headers = {**owner, "Idempotency-Key": "limited-1"}
    first = await client.post(FILES, headers=headers, json=BODY)
    assert first.status_code == 429 and limited
    retry = await client.post(FILES, headers=headers, json=BODY)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers


async def test_a_failure_after_the_row_landed_does_not_create_twice(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The create committed, then the attempt failed before it answered (a 5xx
    # between the commit and the response). The release keeps the marker with
    # its id and clears only the attempt, so the retry re-arms it at once, no
    # lease to wait out, and reruns on the same id: the create finds its own
    # row and returns it, and the retry answers with that one row.
    original = container.managers.media.create_file
    failed = False

    async def fail_after_the_row_landed(ctx, file):
        nonlocal failed
        created = await original(ctx, file)
        if not failed:
            failed = True
            raise RuntimeError("the connection dropped after the commit")
        return created

    monkeypatch.setattr(container.managers.media, "create_file", fail_after_the_row_landed)
    headers = {**owner, "Idempotency-Key": "landed-1"}
    first = await client.post(FILES, headers=headers, json=BODY)
    assert first.status_code == 500 and failed
    [landed] = await started(client, container, owner)  # the row landed before the failure

    retry = await client.post(FILES, headers=headers, json=BODY)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers
    assert retry.json()["id"] == landed, "the retry answers with the row that landed"
    assert await started(client, container, owner) == [landed]
    replay = await client.post(FILES, headers=headers, json=BODY)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == retry.json()


async def test_without_a_key_every_request_creates(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    first = await client.post(FILES, headers=owner, json=BODY)
    second = await client.post(FILES, headers=owner, json=BODY)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


async def test_a_crash_between_the_create_and_finish_does_not_create_twice(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The create committed, then the process died before the outcome landed on
    # the marker. The retry takes the abandoned marker over once its lease has
    # passed and runs the request again on the id the marker carries, so the
    # create finds its own row and returns it instead of making a second one.
    manager = container.managers.idempotency
    monkeypatch.setattr(manager, "_options", IdempotencyOptions(pending_ttl=timedelta(0)))
    original_finish = manager.finish
    crashed = False

    async def crash_once(ctx, key, attempt_id, status, body):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("the process died before finish")
        return await original_finish(ctx, key, attempt_id, status, body)

    monkeypatch.setattr(manager, "finish", crash_once)
    headers = {**owner, "Idempotency-Key": "crash-1"}
    first = await client.post(FILES, headers=headers, json=BODY)
    assert first.status_code == 500 and crashed

    retry = await client.post(FILES, headers=headers, json=BODY)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers
    assert await started(client, container, owner) == [retry.json()["id"]]
    replay = await client.post(FILES, headers=headers, json=BODY)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == retry.json()


async def test_an_attempt_that_runs_past_the_pending_lease_loses_the_marker(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first attempt is still running when its pending lease passes. The
    # retry takes the marker over and creates on the marker's id; the first
    # attempt then finds its own id already written, returns the row as
    # stored, and its finish is refused because the retry holds the marker.
    # One row exists, both callers hold it, and the retry's outcome replays.
    manager = container.managers.idempotency
    monkeypatch.setattr(manager, "_options", IdempotencyOptions(pending_ttl=timedelta(0)))
    original_create = container.managers.media.create_file
    entered, release = asyncio.Event(), asyncio.Event()
    held = False

    async def slow_once(ctx, file):
        nonlocal held
        if not held:
            held = True
            entered.set()
            await release.wait()
        return await original_create(ctx, file)

    monkeypatch.setattr(container.managers.media, "create_file", slow_once)
    headers = {**owner, "Idempotency-Key": "slow-2"}
    first = asyncio.create_task(client.post(FILES, headers=headers, json=BODY))
    await asyncio.wait_for(entered.wait(), timeout=5)

    retry = await client.post(FILES, headers=headers, json=BODY)
    assert retry.status_code == 201, retry.text
    assert "Idempotent-Replayed" not in retry.headers

    release.set()
    slow = await first
    assert slow.status_code == 201, slow.text
    assert slow.json() == retry.json(), "the slow attempt found the row the retry created"
    assert await started(client, container, owner) == [retry.json()["id"]]
    replay = await client.post(FILES, headers=headers, json=BODY)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == retry.json()
