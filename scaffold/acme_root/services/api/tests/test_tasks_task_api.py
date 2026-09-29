import base64
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import add_member, sign_in_as

from acme.om.base import PROVENANCE_FIELDS
from acme.om.exceptions import ValidationFailed
from acme.om.opcontext import Role
from acme.om.tasks.types.filter import OpenTaskCursor
from acme.om.tasks.types.task import TaskStatus
from acme.services.api.container import AppContainer
from acme.services.api.services.impl.tasks import decode_cursor
from acme.services.api.types.tasks import UpdateTaskRequest


async def add(
    client: httpx.AsyncClient, headers: dict[str, str], title: str, **extra: object
) -> dict:
    created = await client.post("/v1/tasks", headers=headers, json={"title": title, **extra})
    assert created.status_code == 201, created.text
    return created.json()


async def titles(
    client: httpx.AsyncClient, headers: dict[str, str], **params: str | int
) -> list[str]:
    listed = await client.get("/v1/tasks", headers=headers, params=params)
    assert listed.status_code == 200, listed.text
    return [t["title"] for t in listed.json()["items"]]


def if_match(task: dict) -> dict[str, str]:
    """The precondition a write carries: the version as read, as an entity tag."""
    return {"If-Match": f'"{task["version"]}"'}


async def patch(
    client: httpx.AsyncClient, headers: dict[str, str], task: dict, **changes: object
) -> dict:
    """An update from the task as held: it names the version it carries."""
    patched = await client.patch(
        f"/v1/tasks/{task['id']}", headers={**headers, **if_match(task)}, json=changes
    )
    assert patched.status_code == 200, patched.text
    return patched.json()


async def move(
    client: httpx.AsyncClient, headers: dict[str, str], task: dict, after_id: str | None
) -> httpx.Response:
    body = {"after_id": after_id, "expected_version": task["version"]}
    return await client.post(f"/v1/tasks/{task['id']}/move", headers=headers, json=body)


async def test_task_round_trip(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    created = await client.post(
        "/v1/tasks",
        headers={**owner, "Idempotency-Key": "task-1"},
        json={"title": "Write the scaffold", "notes": "and test it"},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "open" and task["assignee_id"] is None and task["deleted_at"] is None
    assert task["version"] == 1

    replay = await client.post(
        "/v1/tasks",
        headers={**owner, "Idempotency-Key": "task-1"},
        json={"title": "Write the scaffold", "notes": "and test it"},
    )
    assert replay.status_code == 201 and replay.json()["id"] == task["id"]
    assert replay.headers["Idempotent-Replayed"] == "true"

    listed = await client.get("/v1/tasks", headers=owner)
    assert listed.json() == {"items": [task], "next_cursor": None}

    fetched = await client.get(f"/v1/tasks/{task['id']}", headers=owner)
    assert fetched.status_code == 200 and fetched.json()["title"] == "Write the scaffold"

    done = await client.patch(
        f"/v1/tasks/{task['id']}", headers={**owner, "If-Match": '"1"'}, json={"status": "done"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done" and done.json()["notes"] == "and test it"
    assert done.json()["updated_at"] > task["updated_at"] and done.json()["version"] == 2
    assert await titles(client, owner) == []
    assert await titles(client, owner, status="done") == ["Write the scaffold"]

    deleted = await client.delete(f"/v1/tasks/{task['id']}", headers={**owner, "If-Match": '"2"'})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_at"] is not None and deleted.json()["version"] == 3
    assert (await client.get(f"/v1/tasks/{task['id']}", headers=owner)).status_code == 404
    assert await titles(client, owner, status="done") == []


async def paged(
    client: httpx.AsyncClient, headers: dict[str, str], status: str, limit: int
) -> tuple[list[str], int]:
    """Every title of a list the way a client pages it: until the cursor is null."""
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        listed = await client.get("/v1/tasks", headers=headers, params=params)
        assert listed.status_code == 200, listed.text
        page = listed.json()
        seen += [t["title"] for t in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            return seen, pages


async def test_both_lists_page_by_cursor(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    for i in range(5):
        task = await add(client, owner, f"d{i}")
        await patch(client, owner, task, status="done")
    for i in range(5):
        await add(client, owner, f"o{i}")
    assert await paged(client, owner, "done", 2) == (["d4", "d3", "d2", "d1", "d0"], 3)
    assert await paged(client, owner, "open", 2) == (["o4", "o3", "o2", "o1", "o0"], 3)

    # A cursor is opaque and belongs to one list: the open list's on the done
    # list, or the reverse, is refused like a made-up one.
    first_open = (
        await client.get("/v1/tasks", headers=owner, params={"status": "open", "limit": 2})
    ).json()
    crossed = await client.get(
        "/v1/tasks", headers=owner, params={"status": "done", "cursor": first_open["next_cursor"]}
    )
    assert crossed.status_code == 422 and crossed.json()["error"]["code"] == "validation_failed"


async def test_a_client_paging_at_the_clamp_retrieves_every_task(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    # 201 open and 201 done tasks, one past the clamp of 200: the page the
    # clamp cut says a page follows, and the 201st task is on it.
    for i in range(201):
        await add(client, owner, f"open {i}")
        task = await add(client, owner, f"done {i}")
        await patch(client, owner, task, status="done")
    for status in ("open", "done"):
        seen, pages = await paged(client, owner, status, 200)
        assert len(seen) == 201 and len(set(seen)) == 201 and pages == 2, status
    # Asking past the clamp is the same as asking for the clamp.
    over = await client.get("/v1/tasks", headers=owner, params={"status": "open", "limit": 1000})
    assert len(over.json()["items"]) == 200 and over.json()["next_cursor"] is not None


async def test_move_and_scopes(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    bob = await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    as_bob = await sign_in_as(client, "bob@example.test", org_id)

    c = await add(client, owner, "c")
    await add(client, owner, "b")
    a = await add(client, owner, "a", assignee_id=str(bob.id))
    assert await titles(client, owner) == ["a", "b", "c"]

    moved = await move(client, owner, a, c["id"])
    assert moved.status_code == 200, moved.text
    assert await titles(client, owner) == ["b", "c", "a"]
    back = await move(client, owner, moved.json(), None)
    assert back.status_code == 200, back.text
    assert await titles(client, owner) == ["a", "b", "c"]

    assert await titles(client, as_bob, scope="mine") == ["a"]
    assert await titles(client, owner, scope="mine") == ["b", "c"]
    assert await titles(client, as_bob, scope="team") == ["a", "b", "c"]

    unassigned = await patch(client, owner, back.json(), assignee_id=None)
    assert unassigned["assignee_id"] is None
    assert await titles(client, owner, scope="mine") == ["a", "b", "c"]


async def test_a_move_answers_the_rank_written_out_and_moves_no_other_task(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    """Sixty moves into one gap: the rank on the wire is the decimal in full,
    never in exponent form and never a float, the order is the one asked
    for, and the task nobody moved keeps its version."""
    c = await add(client, owner, "c")
    a = await add(client, owner, "a")
    b = await add(client, owner, "b")
    assert (b["rank"], a["rank"], c["rank"]) == ("-2", "-1", "0")
    rank = ""
    for index in range(60):
        moved = a if index % 2 == 0 else c
        current = (await client.get(f"/v1/tasks/{moved['id']}", headers=owner)).json()
        answer = await move(client, owner, current, b["id"])
        assert answer.status_code == 200, answer.text
        rank = answer.json()["rank"]
        assert isinstance(rank, str) and "E" not in rank.upper()
        assert answer.json()["position"] == float(rank), "the rank's float, for the release before"
    assert len(rank.split(".")[1]) > 15, "past what a float holds"
    listed = (await client.get("/v1/tasks", headers=owner)).json()["items"]
    assert [t["title"] for t in listed] == ["b", "c", "a"], "c moved last, right after b"
    assert next(t for t in listed if t["title"] == "b")["version"] == b["version"]


def test_an_open_cursor_reads_a_finite_decimal_however_it_is_written() -> None:
    """An open cursor's mark is a rank: any finite decimal, in full or in a
    float's shortest text, reads as the same number."""
    task_id = uuid4()
    for mark in ("-3.0", "1e-05", "0.30000000000000004"):
        raw = base64.urlsafe_b64encode(f"open|{mark}|{task_id}".encode()).decode().rstrip("=")
        cursor = decode_cursor(TaskStatus.OPEN, raw)
        assert isinstance(cursor, OpenTaskCursor)
        assert cursor.rank == Decimal(mark) and cursor.id == task_id
    for mark in ("NaN", "Infinity", "one"):
        raw = base64.urlsafe_b64encode(f"open|{mark}|{task_id}".encode()).decode().rstrip("=")
        with pytest.raises(ValidationFailed):
            decode_cursor(TaskStatus.OPEN, raw)


async def test_task_errors_use_the_envelope(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    missing = await client.get(f"/v1/tasks/{uuid4()}", headers=owner)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

    blank = await client.post("/v1/tasks", headers=owner, json={"title": "   "})
    assert blank.status_code == 422 and blank.json()["error"]["code"] == "validation_failed"

    stranger = await client.post(
        "/v1/tasks", headers=owner, json={"title": "x", "assignee_id": str(uuid4())}
    )
    assert stranger.status_code == 422 and stranger.json()["error"]["code"] == "validation_failed"

    bad_cursor = await client.get(
        "/v1/tasks", headers=owner, params={"status": "done", "cursor": "nope"}
    )
    assert bad_cursor.status_code == 422

    unknown_field = await client.post("/v1/tasks", headers=owner, json={"titel": "x"})
    assert unknown_field.status_code == 422

    anonymous = await client.get("/v1/tasks")
    assert anonymous.status_code == 401


def test_the_partial_update_cannot_name_provenance() -> None:
    """The service's merge copies the request's set fields onto the stored
    task; the request type has no provenance field to set and forbids extra
    ones, so the translation cannot rewrite who made a row or its deletion."""
    reserved = PROVENANCE_FIELDS | {"id", "updated_at", "updated_by", "rank"}
    assert set(UpdateTaskRequest.model_fields).isdisjoint(reserved)
    assert UpdateTaskRequest.model_config.get("extra") == "forbid"


async def test_a_patch_naming_provenance_is_refused(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    created = await client.post("/v1/tasks", headers=owner, json={"title": "keep me"})
    assert created.status_code == 201
    task_id = created.json()["id"]
    for field in ("created_by", "created_at", "deleted_at", "deleted_by"):
        patched = await client.patch(
            f"/v1/tasks/{task_id}",
            headers={**owner, "If-Match": '"1"'},
            json={"title": "x", field: None},
        )
        assert patched.status_code == 422, (field, patched.text)
    unchanged = await client.get(f"/v1/tasks/{task_id}", headers=owner)
    assert unchanged.json() == created.json()


async def test_every_write_names_the_version_it_read(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    # The version is not optional: a write that does not say which task it
    # saw is a validation failure, not a write that wins by default.
    task = await add(client, owner, "unversioned")
    no_version = await client.patch(f"/v1/tasks/{task['id']}", headers=owner, json={"title": "x"})
    assert no_version.status_code == 422, no_version.text
    no_move = await client.post(
        f"/v1/tasks/{task['id']}/move", headers=owner, json={"after_id": None}
    )
    assert no_move.status_code == 422, no_move.text
    no_delete = await client.delete(f"/v1/tasks/{task['id']}", headers=owner)
    assert no_delete.status_code == 422, no_delete.text
    for tag in ('"x"', '"0"', 'W/"1"', ""):
        bad = await client.patch(
            f"/v1/tasks/{task['id']}", headers={**owner, "If-Match": tag}, json={"title": "x"}
        )
        assert bad.status_code == 422, (tag, bad.text)
    assert (await client.get(f"/v1/tasks/{task['id']}", headers=owner)).json() == task


async def test_the_body_and_the_query_carry_no_version(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    # The version travels in If-Match and in expected_version and nowhere
    # else. A body that names one is an unknown field, and a query that names
    # one names no version at all; each is refused and nothing changes.
    task = await add(client, owner, "older client")
    edited = await client.patch(
        f"/v1/tasks/{task['id']}", headers={**owner, **if_match(task)}, json={"version": 1}
    )
    assert edited.status_code == 422, edited.text
    moved = await client.post(
        f"/v1/tasks/{task['id']}/move", headers=owner, json={"after_id": None, "version": 1}
    )
    assert moved.status_code == 422, moved.text
    deleted = await client.delete(f"/v1/tasks/{task['id']}", headers=owner, params={"version": 1})
    assert deleted.status_code == 422, deleted.text
    assert (await client.get(f"/v1/tasks/{task['id']}", headers=owner)).json() == task


async def test_two_updates_from_one_snapshot_one_wins_and_the_other_is_412(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    as_bob = await sign_in_as(client, "bob@example.test", org_id)
    # Both read the task at version 1; Ann's edit lands, Bob's names a version
    # the task is no longer at and is refused with a stable code and nothing
    # changed. Bob reads again and his edit lands over Ann's.
    task = await add(client, owner, "as read")
    anns = await patch(client, owner, task, title="ann's")
    bobs = await client.patch(
        f"/v1/tasks/{task['id']}", headers={**as_bob, **if_match(task)}, json={"title": "bob's"}
    )
    assert bobs.status_code == 412, bobs.text
    assert bobs.json()["error"]["code"] == "precondition_failed"
    assert (await client.get(f"/v1/tasks/{task['id']}", headers=as_bob)).json() == anns
    again = await patch(client, as_bob, anns, title="bob's")
    assert again["title"] == "bob's" and again["version"] == 3

    # The same for a move and a delete from the stale snapshot.
    stale_move = await move(client, as_bob, task, None)
    assert stale_move.status_code == 412, stale_move.text
    assert stale_move.json()["error"]["code"] == "precondition_failed"
    stale_delete = await client.delete(
        f"/v1/tasks/{task['id']}", headers={**as_bob, **if_match(task)}
    )
    assert stale_delete.status_code == 412
    assert stale_delete.json()["error"]["code"] == "precondition_failed"
    assert await titles(client, owner) == ["bob's"]


async def test_a_delete_racing_an_edit_cannot_be_undone_by_the_edit(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    task = await add(client, owner, "going")
    deleted = await client.delete(f"/v1/tasks/{task['id']}", headers={**owner, **if_match(task)})
    assert deleted.status_code == 200, deleted.text
    # The edit holds the snapshot from before the delete: the task is gone
    # for it, and its "not deleted" never lands.
    edit = await client.patch(
        f"/v1/tasks/{task['id']}", headers={**owner, **if_match(task)}, json={"title": "back?"}
    )
    assert edit.status_code == 404, edit.text
    assert await titles(client, owner) == [] and await titles(client, owner, status="done") == []
