"""Tenant B against tenant A, over the live app. Two tenants are seeded
whole, and every shape a signed-in principal of A can use to name something
of B is swept: the by-id routes, the lists, the paging cursors, the writes
that carry a foreign id, and the sign-in that asks for the other tenant. A
cross-tenant id is answered the way an id that never existed is, so the
boundary tells nobody what stands on the other side, and no id of B appears
anywhere in a body A is given."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import add_member, build_container, on_plan, run, seed_request, sign_in_as
from starlette.testclient import TestClient

from acme.om.billing.types.plan import Plan
from acme.om.opcontext import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer


def ids_in(payload: object) -> set[str]:
    """Every id anywhere in a response body. A leak is a leak wherever in the
    JSON it sits, so the sweep reads the whole body and not the field a route
    happens to put its rows under."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for value in payload.values():
            found |= ids_in(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= ids_in(value)
    elif isinstance(payload, str):
        try:
            found.add(str(UUID(payload)))
        except ValueError:
            return found
    return found


@dataclass(frozen=True)
class Tenant:
    """One seeded tenant: the headers of its signed-in owner and one id of
    every entity a route names."""

    org_id: UUID
    headers: dict[str, str]
    owner_id: str
    member_id: str
    task_ids: list[str]
    done_task_id: str
    api_key_ids: list[str]
    session_id: str

    @property
    def ids(self) -> set[str]:
        return {
            str(self.org_id),
            self.owner_id,
            self.member_id,
            self.session_id,
            self.done_task_id,
            *self.task_ids,
            *self.api_key_ids,
        }


async def seed_tenant(
    client: httpx.AsyncClient, container: AppContainer, name: str, slug: str
) -> Tenant:
    """A tenant with two of everything a list pages over, so a cursor of its
    own exists to hand to the other tenant, and one task carried to done. The
    task list has an open half and a done half behind two queries, so a tenant
    with nothing done leaves the sweep over the done half passing on an empty
    page."""
    email = f"owner@{slug}.test"
    _, org = await container.managers.tenancy.bootstrap(seed_request(), name, slug, email, name)
    await on_plan(container, org.id, Plan.TEAM)
    headers = await sign_in_as(client, email, org.id)
    member = await add_member(container, org.id, f"member@{slug}.test", Role.MEMBER)
    tasks: list[str] = []
    keys: list[str] = []
    for index in range(2):
        task = await client.post("/v1/tasks", headers=headers, json={"title": f"{slug} {index}"})
        assert task.status_code == 201, task.text
        tasks.append(task.json()["id"])
        key = await client.post(
            "/v1/api-keys", headers=headers, json={"name": f"{slug}-{index}", "role": "member"}
        )
        assert key.status_code == 201, key.text
        keys.append(key.json()["api_key"]["id"])
    finished = await client.post("/v1/tasks", headers=headers, json={"title": f"{slug} done"})
    assert finished.status_code == 201, finished.text
    done_task_id = finished.json()["id"]
    carried = await client.patch(
        f"/v1/tasks/{done_task_id}",
        headers={**headers, "If-Match": '"1"'},
        json={"status": "done"},
    )
    assert carried.status_code == 200 and carried.json()["status"] == "done", carried.text
    me = await client.get("/v1/me", headers=headers)
    assert me.status_code == 200, me.text
    sessions = await client.get("/v1/sessions", headers=headers)
    assert sessions.status_code == 200, sessions.text
    return Tenant(
        org_id=org.id,
        headers=headers,
        owner_id=me.json()["user"]["id"],
        member_id=str(member.id),
        task_ids=tasks,
        done_task_id=done_task_id,
        api_key_ids=keys,
        session_id=sessions.json()[0]["id"],
    )


@pytest.fixture
async def tenants(client: httpx.AsyncClient, container: AppContainer) -> tuple[Tenant, Tenant]:
    """Two tenants on one app: A is the caller everywhere below, B is what A
    must not reach."""
    first = await seed_tenant(client, container, "Ajax", "ajax")
    second = await seed_tenant(client, container, "Other", "other")
    return first, second


LISTS: tuple[str, ...] = (
    "/v1/tasks",
    "/v1/users",
    "/v1/memberships",
    "/v1/sessions",
    "/v1/api-keys",
    "/v1/events",
)
"""Every route that answers with rows of its own choosing rather than an id
the caller named. `/v1/me`, `/v1/me/identity` and `/v1/orgs/current` answer
about the caller and name nothing to ask for."""

PAGED: tuple[str, ...] = ("/v1/tasks", "/v1/users", "/v1/api-keys")
"""The lists that hand out a cursor."""


async def test_no_by_id_route_reaches_another_tenants_row(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """Every route that takes an id in its path, called by A with an id of B
    that exists: each is a 404, the same answer an unknown id gets, and B's
    rows are untouched afterwards."""
    caller, other = tenants
    task, second_task = other.task_ids
    if_match = {"headers": {"If-Match": '"1"'}}
    swept: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", f"/v1/tasks/{task}", {}),
        ("PATCH", f"/v1/tasks/{task}", {"json": {"title": "taken"}, **if_match}),
        ("POST", f"/v1/tasks/{task}/move", {"json": {"after_id": None, "expected_version": 1}}),
        (
            "POST",
            f"/v1/tasks/{second_task}/move",
            {"json": {"after_id": task, "expected_version": 1}},
        ),
        ("DELETE", f"/v1/tasks/{task}", if_match),
        ("PATCH", f"/v1/memberships/{other.member_id}", {"json": {"role": "admin"}}),
        ("DELETE", f"/v1/memberships/{other.member_id}", {}),
        ("PATCH", f"/v1/memberships/{other.owner_id}", {"json": {"role": "member"}}),
        ("DELETE", f"/v1/memberships/{other.owner_id}", {}),
        ("DELETE", f"/v1/sessions/{other.session_id}", {}),
        ("DELETE", f"/v1/api-keys/{other.api_key_ids[0]}", {}),
    ]
    for method, path, extra in swept:
        sent = {**extra, "headers": {**caller.headers, **extra.get("headers", {})}}
        answered = await client.request(method, path, **sent)
        assert answered.status_code == 404, f"{method} {path}: {answered.status_code}"
        # The refusal names back the id the caller named and nothing else of B's.
        named = ids_in(path.split("/")) | ids_in(extra)
        assert ids_in(answered.json()) & other.ids <= named, f"{method} {path}: {answered.text}"

    # Nothing of B's moved: B still reads its own rows, unrevoked and unrenamed.
    for task_id in other.task_ids:
        theirs = await client.get(f"/v1/tasks/{task_id}", headers=other.headers)
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["title"].startswith("other") and theirs.json()["version"] == 1
    keys = await client.get("/v1/api-keys", headers=other.headers, params={"limit": 200})
    assert sorted(k["id"] for k in keys.json()["items"]) == sorted(other.api_key_ids)
    members = await client.get("/v1/memberships", headers=other.headers)
    assert sorted(m["user_id"] for m in members.json()["items"]) == sorted(
        [other.owner_id, other.member_id]
    )
    assert (await client.get("/v1/me", headers=other.headers)).status_code == 200


async def test_no_list_carries_another_tenants_rows(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """Every list a router exposes, read by A with the page as wide as the
    clamp allows: A's own rows are there and not one id of B is."""
    caller, other = tenants
    seen: set[str] = set()
    for path in LISTS:
        for params in list_params(path):
            page = await client.get(path, headers=caller.headers, params=params)
            assert page.status_code == 200, f"{path} {params}: {page.text}"
            found = ids_in(page.json())
            assert not found & other.ids, f"{path} {params} carried {found & other.ids}"
            seen |= found
    # The sweep is not passing on empty lists: A's own rows were listed, the
    # done half of the task list among them.
    assert {
        *caller.task_ids,
        caller.done_task_id,
        *caller.api_key_ids,
        caller.owner_id,
        caller.member_id,
    } <= seen


def list_params(path: str) -> Iterable[dict[str, Any]]:
    """The variants of a list: the task list has an open and a done half and
    a scope, the others one shape."""
    if path == "/v1/tasks":
        return [
            {"limit": 200, "status": status, "scope": scope}
            for status in ("open", "done")
            for scope in ("team", "mine")
        ]
    return [{"limit": 200}]


async def test_a_cursor_minted_in_another_tenant_carries_nothing_across(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """A cursor is an opaque token, and an opaque token is a thing to steal:
    B's cursor presented by A pages A's own tenant or nothing, never B's."""
    caller, other = tenants
    for path in PAGED:
        page = await client.get(path, headers=other.headers, params={"limit": 1})
        assert page.status_code == 200, page.text
        cursor = page.json()["next_cursor"]
        assert cursor is not None, f"{path} handed out no cursor to steal"
        crossed = await client.get(
            path, headers=caller.headers, params={"limit": 200, "cursor": cursor}
        )
        assert crossed.status_code == 200, crossed.text
        assert not ids_in(crossed.json()) & other.ids


async def test_a_write_that_names_another_tenants_id_is_refused(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """The ids a write carries in its body are as much a way in as the ones
    in a path: an assignee from B, an anchor from B."""
    caller, other = tenants
    assigned = await client.post(
        "/v1/tasks",
        headers=caller.headers,
        json={"title": "assigned across", "assignee_id": other.member_id},
    )
    assert assigned.status_code == 422, assigned.text

    mine = caller.task_ids[0]
    reassigned = await client.patch(
        f"/v1/tasks/{mine}",
        headers={**caller.headers, "If-Match": '"1"'},
        json={"assignee_id": other.member_id},
    )
    assert reassigned.status_code == 422, reassigned.text

    anchored = await client.post(
        f"/v1/tasks/{mine}/move",
        headers=caller.headers,
        json={"after_id": other.task_ids[0], "expected_version": 1},
    )
    assert anchored.status_code == 404, anchored.text

    unchanged = await client.get(f"/v1/tasks/{mine}", headers=caller.headers)
    assert unchanged.json()["assignee_id"] is None
    assert unchanged.json()["version"] == 1


async def test_a_bulk_change_never_reaches_another_tenants_tasks(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """A bulk change names B's ids beside one of A's, and then asks for A's
    whole lists: B's tasks are skipped as `not_found`, the way an id that
    never existed is, and B's tasks and counts are as they were."""
    caller, other = tenants
    mine = caller.task_ids[0]
    named = await client.post(
        "/v1/tasks/bulk",
        headers=caller.headers,
        json={"action": "complete", "ids": [*other.task_ids, mine]},
    )
    assert named.status_code == 200, named.text
    body = named.json()
    assert body["changed"] == [mine]
    assert {s["id"]: s["reason"] for s in body["skipped"]} == dict.fromkeys(
        other.task_ids, "not_found"
    )
    reopened = await client.post(
        "/v1/tasks/bulk",
        headers=caller.headers,
        json={"action": "reopen", "ids": [other.done_task_id]},
    )
    assert reopened.json()["skipped"] == [{"id": other.done_task_id, "reason": "not_found"}]
    for action, status in (("complete", "open"), ("reopen", "done")):
        whole = await client.post(
            "/v1/tasks/bulk",
            headers=caller.headers,
            json={"action": action, "all": {"scope": "team", "status": status}},
        )
        assert whole.status_code == 200, whole.text
        assert not ids_in(whole.json()) & other.ids
    for task_id in other.task_ids:
        theirs = await client.get(f"/v1/tasks/{task_id}", headers=other.headers)
        assert theirs.json()["status"] == "open" and theirs.json()["version"] == 1
    done = await client.get(f"/v1/tasks/{other.done_task_id}", headers=other.headers)
    assert done.json()["status"] == "done"
    counted = await client.get(
        "/v1/tasks/count", headers=other.headers, params={"status": "open", "scope": "team"}
    )
    assert counted.json()["count"] == len(other.task_ids)


async def test_a_sign_in_is_not_exchangeable_for_another_tenant(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """The one place a caller names a tenant: A's person exchanges a login
    for a session in B and is refused, and the login lists only A."""
    caller, other = tenants
    login = await client.post("/v1/auth/dev-sign-in", json={"email": "owner@ajax.test"})
    assert login.status_code == 200, login.text
    places = login.json()["memberships"]
    assert [m["org"]["id"] for m in places if m["org"]["kind"] == "team"] == [str(caller.org_id)]
    assert [m["org"]["kind"] for m in places].count("personal") == 1, "and A's own place"
    bearer = {"Authorization": f"Bearer {login.json()['token']}"}
    crossed = await client.post(
        "/v1/auth/sessions", json={"org_id": str(other.org_id)}, headers=bearer
    )
    assert crossed.status_code == 403, crossed.text


async def test_the_event_stream_stops_at_the_tenant_boundary(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """Each tenant counts its own stream, so the sequence numbers of the two
    are the same numbers over different rows: A asks from the beginning and
    gets its own."""
    caller, other = tenants
    mine = await client.get("/v1/events", headers=caller.headers, params={"after_seq": 0})
    theirs = await client.get("/v1/events", headers=other.headers, params={"after_seq": 0})
    assert mine.status_code == 200 and theirs.status_code == 200, mine.text
    assert [e["seq"] for e in mine.json()] == [e["seq"] for e in theirs.json()] != []
    assert not ids_in(mine.json()) & other.ids
    assert {*caller.task_ids, *caller.api_key_ids} <= ids_in(mine.json())


def sign_in_over(tc: TestClient, slug: str, org_id: UUID) -> dict[str, str]:
    """The sign-in of a seeded tenant's owner, driven by the test client: a
    socket is opened in process and an async client cannot open one."""
    login = tc.post("/v1/auth/dev-sign-in", json={"email": f"owner@{slug}.test"})
    assert login.status_code == 200, login.text
    session = tc.post(
        "/v1/auth/sessions",
        json={"org_id": str(org_id)},
        headers={"Authorization": f"Bearer {login.json()['token']}"},
    )
    assert session.status_code == 200, session.text
    return {"Authorization": f"Bearer {session.json()['token']}", "X-App": "portal"}


def test_a_socket_of_one_tenant_never_hears_a_change_in_another(tmp_path: Path) -> None:
    """The channel carries one tenant. A socket of A subscribed to the changes
    of its org hears nothing of B's writes, and the frame it does receive is
    its own: the ping is the barrier, since frames leave in the order they
    were offered and a frame of B's would stand before the pong."""
    container = build_container(tmp_path)
    orgs: dict[str, UUID] = {}
    for name, slug in (("Ajax", "ajax"), ("Other", "other")):
        _, org = run(
            container.managers.tenancy.bootstrap(
                seed_request(), name, slug, f"owner@{slug}.test", name
            )
        )
        run(on_plan(container, org.id, Plan.TEAM))
        orgs[slug] = org.id
    with TestClient(create_app(container)) as tc:
        mine = sign_in_over(tc, "ajax", orgs["ajax"])
        theirs = sign_in_over(tc, "other", orgs["other"])
        ticket = tc.post("/v1/realtime/tickets", headers=mine).json()["ticket"]
        with tc.websocket_connect(f"/v1/realtime?ticket={ticket}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["org_id"] == str(orgs["ajax"])
            ws.send_json({"op": "subscribe", "topic": "entity_changed"})
            assert ws.receive_json()["type"] == "subscribed"

            crossed = tc.post("/v1/tasks", headers=theirs, json={"title": "theirs"})
            assert crossed.status_code == 201, crossed.text
            key = tc.post("/v1/api-keys", headers=theirs, json={"name": "theirs", "role": "member"})
            assert key.status_code == 201, key.text
            member = tc.post("/v1/tasks", headers=theirs, json={"title": "theirs again"})
            assert member.status_code == 201, member.text

            ws.send_json({"op": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong", pong
            assert pong["seq"] == hello["seq"]  # B's writes moved no stream of A's

            # The channel is open, so the silence above is the tenant fence
            # and not a socket that hears nothing at all.
            own = tc.post("/v1/tasks", headers=mine, json={"title": "mine"})
            assert own.status_code == 201, own.text
            event = ws.receive_json()
            assert event["type"] == "event" and event["topic"] == "entity_changed"
            assert event["payload"]["target_id"] == own.json()["id"]
