"""The operator plane over the live app: which permission each route asks
for, the reads of one tenant and the trail they leave, the platform's size,
the two creates under the operator's idempotency record, and the requeue
of a failed work item."""

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import OWNER, enrol_operator, sign_in_as

from acme.om.base import new_id, utcnow
from acme.om.opcontext import AppContext, AppType, OperatorRole, RequestContext
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.services.api.container import AppContainer

OPERATOR_LOG = "acme.om.tenancy.impl.operator"


async def admit(
    client: httpx.AsyncClient, container: AppContainer, role: OperatorRole, email: str
) -> dict[str, str]:
    """An operator's headers: the token the person's own sign-in, with a
    second factor, minted with the whole of an entry of `role`."""
    headers, _ = await enrol_operator(client, container, email, role)
    return headers


@pytest.fixture
async def writer(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    return await admit(client, container, OperatorRole.WRITE, "root@example.test")


@pytest.fixture
async def reader(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    return await admit(client, container, OperatorRole.READ, "sup@example.test")


@pytest.fixture
async def org_id(client: httpx.AsyncClient, owner: dict[str, str]) -> str:
    """The seeded tenant, with two open tasks and one done, over its owner."""
    current = await client.get("/v1/orgs/current", headers=owner)
    for title in ("first", "second", "third"):
        created = await client.post("/v1/tasks", headers=owner, json={"title": title})
        assert created.status_code == 201, created.text
        if title == "third":
            done = await client.patch(
                f"/v1/tasks/{created.json()['id']}",
                headers={**owner, "If-Match": '"1"'},
                json={"status": "done"},
            )
            assert done.status_code == 200, done.text
    return current.json()["id"]


def routes(org_id: str) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Every operator route, the permission it asks for, and a request that
    passes its validation. The write ones name a fresh slug and email, so the
    write operator's call lands."""
    return [
        ("read", "GET", "/v1/admin/me", {}),
        ("read", "GET", "/v1/admin/me/tokens", {}),
        ("read", "GET", "/v1/admin/size", {}),
        ("read", "GET", "/v1/admin/orgs", {}),
        ("read", "GET", f"/v1/admin/orgs/{org_id}", {}),
        ("read", "GET", f"/v1/admin/orgs/{org_id}/members", {}),
        ("read", "GET", f"/v1/admin/orgs/{org_id}/tasks", {}),
        ("read", "GET", f"/v1/admin/orgs/{org_id}/events", {}),
        (
            "write",
            "POST",
            "/v1/admin/orgs",
            {
                "json": {
                    "name": "Other",
                    "slug": "other",
                    "owner_email": "otto@example.test",
                    "owner_name": "Otto",
                }
            },
        ),
        (
            "write",
            "POST",
            f"/v1/admin/orgs/{org_id}/members",
            {
                "json": {
                    "email": "bob@example.test",
                    "display_name": "Bob",
                    "role": "member",
                }
            },
        ),
        ("write", "DELETE", f"/v1/admin/orgs/{org_id}", {}),
    ]


async def test_every_route_is_held_to_its_permission(
    client: httpx.AsyncClient,
    container: AppContainer,
    owner: dict[str, str],
    reader: dict[str, str],
    writer: dict[str, str],
    org_id: str,
) -> None:
    """A read operator passes every read and is refused every write with the
    `not_authorized` shape a viewer's write gets; a write operator passes
    both; a tenant session is not an operator at all."""
    await container.managers.tenancy_operator.tally_size()
    for permission, method, path, extra in routes(org_id):
        as_tenant = await client.request(method, path, headers=owner, **extra)
        assert as_tenant.status_code == 401, f"{method} {path}: {as_tenant.text}"
        as_reader = await client.request(method, path, headers=reader, **extra)
        if permission == "read":
            assert as_reader.status_code == 200, f"{method} {path}: {as_reader.text}"
        else:
            assert as_reader.status_code == 403, f"{method} {path}: {as_reader.text}"
            assert as_reader.json()["error"]["code"] == "not_authorized"
            assert as_reader.json()["error"]["message"] == "operator lacks write"
        as_writer = await client.request(method, path, headers=writer, **extra)
        expected = 201 if method == "POST" else 200
        assert as_writer.status_code == expected, f"{method} {path}: {as_writer.text}"
    # The refused writes landed nothing: the org is only now closed, by the
    # writer, with nobody left in it; the queue deletes it.
    orgs = {
        o["slug"]: o for o in (await client.get("/v1/admin/orgs", headers=reader)).json()["items"]
    }
    assert orgs["ajax"]["deleted_at"] is None and orgs["other"]["deleted_at"] is None
    members = await client.get(f"/v1/admin/orgs/{org_id}/members", headers=reader)
    assert members.json()["items"] == []


async def test_an_operator_reads_one_tenant_and_leaves_a_trail(
    client: httpx.AsyncClient,
    container: AppContainer,
    reader: dict[str, str],
    org_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=OPERATOR_LOG):
        org = await client.get(f"/v1/admin/orgs/{org_id}", headers=reader)
        assert org.status_code == 200 and org.json()["slug"] == "ajax", org.text
        unknown = await client.get(f"/v1/admin/orgs/{UUID(int=7)}", headers=reader)
        assert unknown.status_code == 404

        members = await client.get(f"/v1/admin/orgs/{org_id}/members", headers=reader)
        assert members.status_code == 200, members.text
        assert [m["email"] for m in members.json()["items"]] == [OWNER["email"]]
        assert members.json()["next_cursor"] is None

        opened = await client.get(
            f"/v1/admin/orgs/{org_id}/tasks", headers=reader, params={"limit": 1}
        )
        assert opened.status_code == 200, opened.text
        assert [t["title"] for t in opened.json()["items"]] == ["second"]  # the top of the list
        cursor = opened.json()["next_cursor"]
        assert cursor is not None
        rest = await client.get(
            f"/v1/admin/orgs/{org_id}/tasks", headers=reader, params={"cursor": cursor}
        )
        assert [t["title"] for t in rest.json()["items"]] == ["first"]
        assert rest.json()["next_cursor"] is None
        done = await client.get(
            f"/v1/admin/orgs/{org_id}/tasks", headers=reader, params={"status": "done"}
        )
        assert [t["title"] for t in done.json()["items"]] == ["third"]
        crossed = await client.get(
            f"/v1/admin/orgs/{org_id}/tasks",
            headers=reader,
            params={"status": "done", "cursor": cursor},
        )
        assert crossed.status_code == 422, crossed.text

        events = await client.get(f"/v1/admin/orgs/{org_id}/events", headers=reader)
        assert events.status_code == 200, events.text
        assert [e["seq"] for e in events.json()] == [1, 2, 3, 4]
        # The operator's feed carries what the tenant's leaves out: the request
        # that produced each record and the app it came from.
        assert all(UUID(e["request_id"]) and e["app"] == "portal" for e in events.json())
        assert {e["kind"] for e in events.json()} == {
            "tasks.task.created",
            "tasks.task.updated",
        }
        later = await client.get(
            f"/v1/admin/orgs/{org_id}/events", headers=reader, params={"after_seq": 3}
        )
        assert [e["seq"] for e in later.json()] == [4]

    trail = [r.getMessage() for r in caplog.records if r.name == OPERATOR_LOG]
    # Seven reads landed; the cursor of the other list was refused before any.
    assert len(trail) == 7 and all(org_id in line for line in trail)
    assert not any("example.test" in line or "first" in line for line in trail)


async def test_the_org_list_pages_past_one_page(
    client: httpx.AsyncClient, reader: dict[str, str], writer: dict[str, str]
) -> None:
    """Every tenant is reached by following `next_cursor`, whatever the page
    size, so the plane sees past the clamp; a tenant's cursor is refused."""
    for index in range(3):
        created = await client.post(
            "/v1/admin/orgs",
            headers={**writer, "Idempotency-Key": f"org-page-{index}"},
            json={
                "name": f"Org {index}",
                "slug": f"org-{index}",
                "owner_email": f"owner-{index}@example.test",
                "owner_name": "Owner",
            },
        )
        assert created.status_code == 201, created.text
    every = (await client.get("/v1/admin/orgs", headers=reader, params={"limit": 200})).json()
    assert every["next_cursor"] is None

    listed: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = await client.get("/v1/admin/orgs", headers=reader, params=params)
        assert page.status_code == 200, page.text
        listed += [o["id"] for o in page.json()["items"]]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert listed == [o["id"] for o in every["items"]]
    assert len(listed) >= 5

    made_up = await client.get("/v1/admin/orgs", headers=reader, params={"cursor": "not-a-cursor"})
    assert made_up.status_code == 422, made_up.text


async def test_the_size_is_what_the_first_responder_reads(
    client: httpx.AsyncClient, container: AppContainer, reader: dict[str, str], org_id: str
) -> None:
    """The route reads the tally the worker's sweep keeps, and says when it
    was counted; before the first count it is `404`, not a size of zero."""
    early = await client.get("/v1/admin/size", headers=reader)
    assert early.status_code == 404, early.text
    assert "not counted yet" in early.json()["error"]["message"]
    await container.managers.tenancy_operator.tally_size()
    size = await client.get("/v1/admin/size", headers=reader)
    assert size.status_code == 200, size.text
    body = size.json()
    # The seeded tenant and the operator's own, and each owner's personal
    # org; the two owners, once in each.
    assert (body["tenants"], body["users"]) == (4, 4)
    assert (body["tasks_last_24h"], body["events_last_24h"]) == (3, 4)
    for moment in (body["since"], body["counted_at"]):
        assert moment.endswith("Z") or "+" in moment


async def test_the_creates_run_under_the_operators_idempotency_record(
    client: httpx.AsyncClient, writer: dict[str, str]
) -> None:
    """A creating route of the operator plane carries a key like every other:
    the retry replays the org the first call created, a reused key with
    another body is refused, and the org's owner signs in like any owner."""
    body = {
        "name": "Other",
        "slug": "other",
        "owner_email": "otto@example.test",
        "owner_name": "Otto",
    }
    headers = {**writer, "Idempotency-Key": "org-1"}
    first = await client.post("/v1/admin/orgs", headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["slug"] == "other" and first.json()["deleted_at"] is None
    second = await client.post("/v1/admin/orgs", headers=headers, json=body)
    assert second.status_code == 201 and second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()
    reused = await client.post("/v1/admin/orgs", headers=headers, json={**body, "slug": "another"})
    assert reused.status_code == 422, reused.text
    taken = await client.post(
        "/v1/admin/orgs", headers={**writer, "Idempotency-Key": "org-2"}, json=body
    )
    assert taken.status_code == 409, taken.text
    assert taken.json()["error"]["code"] == "conflict"
    org_id = UUID(first.json()["id"])
    # A new org is on Free, one seat; the operator grants it Team first.
    granted = await client.put(
        f"/v1/admin/orgs/{org_id}/plan", headers=writer, json={"plan": "team"}
    )
    assert granted.status_code == 200 and granted.json()["plan"] == "team", granted.text

    member = {
        "email": "bob@example.test",
        "display_name": "Bob",
        "role": "admin",
    }
    headers = {**writer, "Idempotency-Key": "member-1"}
    added = await client.post(f"/v1/admin/orgs/{org_id}/members", headers=headers, json=member)
    assert added.status_code == 201, added.text
    assert added.json()["email"] == "bob@example.test"
    replayed = await client.post(f"/v1/admin/orgs/{org_id}/members", headers=headers, json=member)
    assert replayed.status_code == 201 and replayed.headers["Idempotent-Replayed"] == "true"
    assert replayed.json() == added.json()
    as_owner = await client.post(
        f"/v1/admin/orgs/{org_id}/members",
        headers={**writer, "Idempotency-Key": "member-2"},
        json={**member, "email": "cat@example.test", "role": "owner"},
    )
    assert as_owner.status_code == 422, as_owner.text
    blank = await client.post(
        f"/v1/admin/orgs/{org_id}/members", headers=writer, json={**member, "email": ""}
    )
    assert blank.status_code == 422, blank.text

    # Both people sign into the org the operator made, with the roles given.
    otto = await sign_in_as(client, "otto@example.test", org_id)
    assert (await client.get("/v1/me", headers=otto)).json()["role"] == "owner"
    bob = await sign_in_as(client, "bob@example.test", org_id)
    assert (await client.get("/v1/me", headers=bob)).json()["role"] == "admin"
    listed = await client.get(f"/v1/admin/orgs/{org_id}/members", headers=writer)
    assert sorted(m["email"] for m in listed.json()["items"]) == [
        "bob@example.test",
        "otto@example.test",
    ]


async def test_an_operator_reads_its_own_entry(
    client: httpx.AsyncClient, reader: dict[str, str], writer: dict[str, str]
) -> None:
    """`/v1/admin/me` says who was admitted and what the entry grants, which is
    the check a skill makes before its first read."""
    mine = await client.get("/v1/admin/me", headers=reader)
    assert mine.status_code == 200, mine.text
    assert mine.json()["email"] == "sup@example.test"
    assert mine.json()["operator_role"] == "read"
    assert UUID(mine.json()["identity_id"])
    assert (await client.get("/v1/admin/me", headers=writer)).json()["operator_role"] == "write"


async def failed_item(container: AppContainer, org_id: str) -> UUID:
    """One item of the org, failed for good by a worker's refusal."""
    rctx = RequestContext(request_id=new_id(), app=AppContext(type=AppType.WORKER, version="t"))
    [ctx] = [
        c
        for c in await container.managers.tenancy.service_contexts(rctx)
        if str(c.org_id) == org_id
    ]
    now = utcnow()
    item = WorkItem(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        kind=WorkKind.NOOP,
        target_id=new_id(),
        idempotency_key=new_id(),
        request_id=ctx.request_id,
        available_at=now,
    )
    await container.managers.work.enqueue(ctx, item)
    claimed = await container.managers.work.claim(
        rctx, "default", [WorkKind.NOOP], "test", timedelta(seconds=30)
    )
    assert claimed is not None
    await container.managers.work.fail_for_good(claimed[0], claimed[1], "refused: no")
    return item.id


async def test_a_write_operator_requeues_a_failed_item_once(
    client: httpx.AsyncClient,
    container: AppContainer,
    reader: dict[str, str],
    writer: dict[str, str],
    org_id: str,
) -> None:
    item_id = await failed_item(container, org_id)
    path = f"/v1/admin/orgs/{org_id}/work/{item_id}/requeue"
    refused = await client.post(path, headers=reader)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_authorized"

    requeued = await client.post(path, headers=writer)
    assert requeued.status_code == 200, requeued.text
    body = requeued.json()
    assert (body["id"], body["kind"], body["status"]) == (str(item_id), "NOOP", "queued")
    assert (body["attempts"], body["max_attempts"], body["last_error"]) == (0, 3, None)

    again = await client.post(path, headers=writer)
    assert again.status_code == 409 and again.json()["error"]["code"] == "work_not_failed"
    unknown = await client.post(f"/v1/admin/orgs/{org_id}/work/{new_id()}/requeue", headers=writer)
    assert unknown.status_code == 404

    # The diary names the dead letter, then the requeue and the operator who made it.
    me = (await client.get("/v1/admin/me", headers=writer)).json()
    events = (await client.get(f"/v1/admin/orgs/{org_id}/events", headers=reader)).json()
    trail = [(e["kind"], e["target_id"]) for e in events if e["kind"].startswith("work.")]
    assert trail == [("work.item.failed", str(item_id)), ("work.item.requeued", str(item_id))]
    requeue = next(e for e in events if e["kind"] == "work.item.requeued")
    assert requeue["actor_id"] == me["identity_id"]
