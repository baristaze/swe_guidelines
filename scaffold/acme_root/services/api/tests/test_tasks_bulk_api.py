"""The change of many tasks in one call, over the live app: by ids and by a
whole list, the count a "Mark all" asks about, the plan's bound on a reopen
answered beside what changed, the replay of a retried call, the Undo as the
other action over what changed, and the body that names both or neither."""

from uuid import UUID, uuid4

import httpx
import pytest
from api_support import add_member, sign_in, sign_in_as

from acme.om.opcontext import Role
from acme.services.api.container import AppContainer


async def add(client: httpx.AsyncClient, headers: dict[str, str], title: str) -> dict:
    created = await client.post("/v1/tasks", headers=headers, json={"title": title})
    assert created.status_code == 201, created.text
    return created.json()


async def bulk(
    client: httpx.AsyncClient, headers: dict[str, str], body: dict, key: str | None = None
) -> httpx.Response:
    return await client.post(
        "/v1/tasks/bulk",
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json=body,
    )


async def count(client: httpx.AsyncClient, headers: dict[str, str], status: str) -> int:
    counted = await client.get(
        "/v1/tasks/count", headers=headers, params={"status": status, "scope": "team"}
    )
    assert counted.status_code == 200, counted.text
    assert (counted.json()["status"], counted.json()["scope"]) == (status, "team")
    return counted.json()["count"]


async def titles(client: httpx.AsyncClient, headers: dict[str, str], status: str) -> list[str]:
    listed = await client.get(
        "/v1/tasks", headers=headers, params={"status": status, "scope": "team", "limit": 200}
    )
    return [t["title"] for t in listed.json()["items"]]


async def test_complete_by_ids_and_undo_by_the_ids_it_answered(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    kept = await add(client, owner, "kept")
    tasks = [await add(client, owner, t) for t in ("c", "b", "a")]
    assert await titles(client, owner, "open") == ["a", "b", "c", "kept"]
    ids = [t["id"] for t in reversed(tasks)]  # as the list reads, top first

    marked = await bulk(client, owner, {"action": "complete", "ids": [*ids, str(uuid4())]})

    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["action"] == "complete"
    assert body["changed"] == ids and body["changed_count"] == 3
    assert body["skipped_count"] == 1 and body["skipped"][0]["reason"] == "not_found"
    assert body["plan_limit"] is None
    assert await titles(client, owner, "open") == ["kept"]
    assert await count(client, owner, "done") == 3

    # The Undo: the other action over exactly what changed, bottom first, so
    # the open list reads as it did.
    undone = await bulk(client, owner, {"action": "reopen", "ids": body["changed"][::-1]})
    assert undone.json()["changed_count"] == 3
    assert await titles(client, owner, "open") == ["a", "b", "c", "kept"]
    assert kept["status"] == "open"


async def test_mark_all_reads_the_whole_list_not_a_page(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    for index in range(40):
        await add(client, owner, f"task {index}")
    first_page = await client.get("/v1/tasks", headers=owner, params={"limit": 10})
    assert len(first_page.json()["items"]) == 10 and first_page.json()["next_cursor"]
    assert await count(client, owner, "open") == 40

    marked = await bulk(
        client, owner, {"action": "complete", "all": {"scope": "team", "status": "open"}}
    )

    assert marked.status_code == 200, marked.text
    assert marked.json()["changed_count"] == 40 and len(marked.json()["changed"]) == 40
    assert await count(client, owner, "open") == 0
    assert await count(client, owner, "done") == 40
    reopened = await bulk(
        client, owner, {"action": "reopen", "all": {"scope": "team", "status": "done"}}
    )
    assert reopened.json()["changed_count"] == 40
    assert await count(client, owner, "open") == 40


async def test_a_retry_answers_what_the_first_call_did(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    """A retried "Mark all" is a replay: it never reaches a task that joined
    the list after the first call ran."""
    await add(client, owner, "first")
    body = {"action": "complete", "all": {"scope": "team", "status": "open"}}
    first = await bulk(client, owner, body, key="mark-all-1")
    await add(client, owner, "joined later")
    again = await bulk(client, owner, body, key="mark-all-1")
    assert again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert await titles(client, owner, "open") == ["joined later"]


async def test_reopening_past_free_opens_up_to_the_bound_and_names_it(
    client: httpx.AsyncClient, container: AppContainer
) -> None:
    free = await sign_in(client, container, plan=None)
    finished = [await add(client, free, f"done {i}") for i in range(4)]
    marked = await bulk(client, free, {"action": "complete", "ids": [t["id"] for t in finished]})
    assert marked.json()["changed_count"] == 4
    for index in range(8):
        await add(client, free, f"open {index}")

    reopened = await bulk(
        client, free, {"action": "reopen", "all": {"scope": "team", "status": "done"}}
    )

    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["changed_count"] == 2 and body["skipped_count"] == 2
    assert {s["reason"] for s in body["skipped"]} == {"plan_limit"}
    assert body["plan_limit"] == {
        "lever": "active_tasks",
        "plan": "free",
        "limit": 10,
        "suggested_plan": "pro",
    }
    assert await count(client, free, "open") == 10


async def test_a_body_names_its_tasks_one_way(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    task = await add(client, owner, "one")
    everything = {"scope": "team", "status": "open"}
    for body in (
        {"action": "complete"},
        {"action": "complete", "ids": [task["id"]], "all": everything},
        {"action": "reopen", "all": everything},
        {"action": "complete", "all": {"scope": "team", "status": "done"}},
    ):
        refused = await bulk(client, owner, body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["error"]["code"] == "validation_failed"
    too_many = await bulk(
        client, owner, {"action": "complete", "ids": [str(uuid4()) for _ in range(1001)]}
    )
    assert too_many.status_code == 422
    unknown = await bulk(client, owner, {"action": "archive", "ids": [task["id"]]})
    assert unknown.status_code == 422
    assert await titles(client, owner, "open") == ["one"]


async def test_mine_is_the_callers_tasks_and_a_viewer_changes_nothing(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    bob = await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    bobs = await sign_in_as(client, bob.email, org_id)
    await add(client, owner, "the owner's")
    await add(client, bobs, "bob's")

    marked = await bulk(
        client, bobs, {"action": "complete", "all": {"scope": "mine", "status": "open"}}
    )
    assert marked.json()["changed_count"] == 1
    assert await titles(client, owner, "open") == ["the owner's"]

    viewer = await add_member(container, org_id, "viewer@example.test", Role.VIEWER)
    viewers = await sign_in_as(client, viewer.email, org_id)
    refused = await bulk(
        client, viewers, {"action": "complete", "all": {"scope": "team", "status": "open"}}
    )
    assert refused.status_code == 403, refused.text
    assert await titles(client, owner, "open") == ["the owner's"]


@pytest.mark.parametrize("status", ["open", "done"])
async def test_the_count_is_the_list_in_its_scope(
    client: httpx.AsyncClient, owner: dict[str, str], status: str
) -> None:
    assert await count(client, owner, status) == 0
    tasks = [await add(client, owner, f"task {i}") for i in range(3)]
    if status == "done":
        await bulk(client, owner, {"action": "complete", "ids": [t["id"] for t in tasks]})
    assert await count(client, owner, status) == 3
