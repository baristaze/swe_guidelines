"""Tenant B against tenant A, over the live app. Two tenants are seeded
whole, and every shape a signed-in principal of A can use to name something
of B is swept: the by-id routes, the lists, the paging cursors, and the
sign-in that asks for the other tenant. A cross-tenant id is answered the
way an id that never existed is, so the boundary tells nobody what stands on
the other side, and no id of B appears anywhere in a body A is given."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import add_member, build_container, run, seed_request, sign_in_as
from starlette.testclient import TestClient

from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.om.opcontext import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer

PDF = b"%PDF-1.7"
UPLOAD = {"name": "report.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}


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
    file_ids: list[str]
    pending_file_id: str
    api_key_ids: list[str]
    invitation_ids: list[str]
    session_id: str

    @property
    def ids(self) -> set[str]:
        return {
            str(self.org_id),
            self.owner_id,
            self.member_id,
            self.session_id,
            self.pending_file_id,
            *self.file_ids,
            *self.api_key_ids,
            *self.invitation_ids,
        }


async def stored_file(client: httpx.AsyncClient, headers: dict[str, str], name: str) -> str:
    """An upload started, its bytes moved, and confirmed: a file the list shows."""
    started = await client.post("/v1/media/files", headers=headers, json={**UPLOAD, "name": name})
    assert started.status_code == 201, started.text
    file_id = started.json()["id"]
    put = await client.put(f"/v1/media/files/{file_id}/content", headers=headers, content=PDF)
    assert put.status_code == 200, put.text
    confirmed = await client.post(f"/v1/media/files/{file_id}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    return file_id


async def seed_tenant(
    client: httpx.AsyncClient, container: AppContainer, name: str, slug: str
) -> Tenant:
    """A tenant with two of everything a list pages over, so a cursor of its
    own exists to hand to the other tenant, and one upload left pending: the
    file list shows stored files only, and the routes that move an upload's
    bytes take a pending one."""
    email = f"owner@{slug}.test"
    _, org = await container.managers.tenancy.bootstrap(seed_request(), name, slug, email, name)
    headers = await sign_in_as(client, email, org.id)
    member = await add_member(container, org.id, f"member@{slug}.test", Role.MEMBER)
    files: list[str] = []
    keys: list[str] = []
    invitations: list[str] = []
    for index in range(2):
        files.append(await stored_file(client, headers, f"{slug}-{index}.pdf"))
        key = await client.post(
            "/v1/api-keys", headers=headers, json={"name": f"{slug}-{index}", "role": "member"}
        )
        assert key.status_code == 201, key.text
        keys.append(key.json()["api_key"]["id"])
        invited = await client.post(
            "/v1/invitations",
            headers={**headers, "Idempotency-Key": f"{slug}-invite-{index}"},
            json={"email": f"guest-{index}@{slug}.test", "role": "member"},
        )
        assert invited.status_code == 201, invited.text
        invitations.append(invited.json()["id"])
    pending = await client.post("/v1/media/files", headers=headers, json=UPLOAD)
    assert pending.status_code == 201, pending.text
    me = await client.get("/v1/me", headers=headers)
    assert me.status_code == 200, me.text
    sessions = await client.get("/v1/sessions", headers=headers)
    assert sessions.status_code == 200, sessions.text
    return Tenant(
        org_id=org.id,
        headers=headers,
        owner_id=me.json()["user"]["id"],
        member_id=str(member.id),
        file_ids=files,
        pending_file_id=pending.json()["id"],
        api_key_ids=keys,
        invitation_ids=invitations,
        session_id=sessions.json()[0]["id"],
    )


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    """The test container over the identity provider's twin, which the
    invitation routes send through."""
    return build_container(tmp_path, integrations=IntegrationsOverImpl(IdentityProviderTwinImpl()))


@pytest.fixture
async def tenants(client: httpx.AsyncClient, container: AppContainer) -> tuple[Tenant, Tenant]:
    """Two tenants on one app: A is the caller everywhere below, B is what A
    must not reach."""
    first = await seed_tenant(client, container, "Ajax", "ajax")
    second = await seed_tenant(client, container, "Other", "other")
    return first, second


LISTS: tuple[str, ...] = (
    "/v1/users",
    "/v1/memberships",
    "/v1/sessions",
    "/v1/api-keys",
    "/v1/invitations",
    "/v1/media/files",
    "/v1/events",
)
"""Every route that answers with rows of its own choosing rather than an id
the caller named. `/v1/me`, `/v1/me/identity`, `/v1/orgs/current`, and
`/v1/media/usage` answer about the caller and name nothing to ask for."""

PAGED: tuple[str, ...] = (
    "/v1/users",
    "/v1/memberships",
    "/v1/api-keys",
    "/v1/invitations",
    "/v1/media/files",
)
"""The lists that hand out a cursor."""


def by_id_routes(other: Tenant) -> list[tuple[str, str, dict[str, Any]]]:
    """Every tenant route that takes an id in its path, named with an id of
    B's that exists, and a request that passes its validation."""
    stored, pending = other.file_ids[0], other.pending_file_id
    return [
        ("PATCH", f"/v1/memberships/{other.member_id}", {"json": {"role": "admin"}}),
        ("DELETE", f"/v1/memberships/{other.member_id}", {}),
        ("PATCH", f"/v1/memberships/{other.owner_id}", {"json": {"role": "member"}}),
        ("DELETE", f"/v1/memberships/{other.owner_id}", {}),
        ("DELETE", f"/v1/sessions/{other.session_id}", {}),
        ("DELETE", f"/v1/api-keys/{other.api_key_ids[0]}", {}),
        ("POST", f"/v1/invitations/{other.invitation_ids[0]}/resend", {}),
        ("DELETE", f"/v1/invitations/{other.invitation_ids[0]}", {}),
        ("GET", f"/v1/media/files/{stored}", {}),
        ("GET", f"/v1/media/files/{stored}/download", {}),
        ("GET", f"/v1/media/files/{stored}/content", {}),
        ("DELETE", f"/v1/media/files/{stored}", {}),
        ("GET", f"/v1/media/files/{pending}", {}),
        ("POST", f"/v1/media/files/{pending}/upload", {}),
        ("PUT", f"/v1/media/files/{pending}/content", {"content": PDF}),
        ("POST", f"/v1/media/files/{pending}/confirm", {}),
    ]


async def test_no_by_id_route_reaches_another_tenants_row(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """Every route that takes an id in its path, called by A with an id of B
    that exists: each is a 404, the same answer an unknown id gets, and B's
    rows are untouched afterwards."""
    caller, other = tenants
    for method, path, extra in by_id_routes(other):
        sent = {**extra, "headers": {**caller.headers, **extra.get("headers", {})}}
        answered = await client.request(method, path, **sent)
        assert answered.status_code == 404, f"{method} {path}: {answered.status_code}"
        # The refusal names back the id the caller named and nothing else of B's.
        named = ids_in(path.split("/"))
        assert ids_in(answered.json()) & other.ids <= named, f"{method} {path}: {answered.text}"

    # Nothing of B's moved: B still reads its own rows, unrevoked and undeleted.
    for file_id in other.file_ids:
        theirs = await client.get(f"/v1/media/files/{file_id}", headers=other.headers)
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["status"] == "stored" and theirs.json()["deleted_at"] is None
    pending = await client.get(f"/v1/media/files/{other.pending_file_id}", headers=other.headers)
    assert pending.json()["status"] == "pending"
    keys = await client.get("/v1/api-keys", headers=other.headers, params={"limit": 200})
    assert sorted(k["id"] for k in keys.json()["items"]) == sorted(other.api_key_ids)
    invitations = await client.get("/v1/invitations", headers=other.headers, params={"limit": 200})
    assert sorted(i["id"] for i in invitations.json()["items"]) == sorted(other.invitation_ids)
    assert {i["state"] for i in invitations.json()["items"]} == {"pending"}
    members = await client.get("/v1/memberships", headers=other.headers)
    assert sorted((m["user_id"], m["role"]) for m in members.json()["items"]) == sorted(
        [(other.owner_id, "owner"), (other.member_id, "member")]
    )
    assert (await client.get("/v1/me", headers=other.headers)).status_code == 200


async def test_an_unknown_id_is_answered_as_another_tenants_is(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """The control of the sweep above: the same routes with ids nobody holds
    answer the same status and code, so a 404 there says nothing about what
    stands in B."""
    caller, other = tenants
    unknown = str(UUID(int=7))
    for method, path, extra in by_id_routes(other):
        crossed = await client.request(method, path, headers=caller.headers, **extra)
        for known in ids_in(path.split("/")):
            path = path.replace(known, unknown)
        missing = await client.request(method, path, headers=caller.headers, **extra)
        assert (missing.status_code, missing.json()["error"]["code"]) == (
            crossed.status_code,
            crossed.json()["error"]["code"],
        ), f"{method} {path}"


async def test_no_list_carries_another_tenants_rows(
    client: httpx.AsyncClient, tenants: tuple[Tenant, Tenant]
) -> None:
    """Every list a router exposes, read by A with the page as wide as the
    clamp allows: A's own rows are there and not one id of B is."""
    caller, other = tenants
    seen: set[str] = set()
    for path in LISTS:
        page = await client.get(path, headers=caller.headers, params={"limit": 200})
        assert page.status_code == 200, f"{path}: {page.text}"
        found = ids_in(page.json())
        assert not found & other.ids, f"{path} carried {found & other.ids}"
        seen |= found
    usage = await client.get("/v1/media/usage", headers=caller.headers)
    assert usage.json()["total_count"] == len(caller.file_ids), "B's files count nowhere in A"
    # The sweep is not passing on empty lists: A's own rows were listed.
    assert {
        *caller.file_ids,
        *caller.api_key_ids,
        *caller.invitation_ids,
        caller.owner_id,
        caller.member_id,
        caller.session_id,
    } <= seen


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
        assert not ids_in(crossed.json()) & other.ids, f"{path} carried B's rows"


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
    assert {*caller.file_ids, *caller.api_key_ids} <= ids_in(mine.json())


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

            crossed = tc.post("/v1/media/files", headers=theirs, json=UPLOAD)
            assert crossed.status_code == 201, crossed.text
            key = tc.post("/v1/api-keys", headers=theirs, json={"name": "theirs", "role": "member"})
            assert key.status_code == 201, key.text
            renamed = tc.patch("/v1/me", headers=theirs, json={"display_name": "Theirs"})
            assert renamed.status_code == 200, renamed.text

            ws.send_json({"op": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong", pong
            assert pong["seq"] == hello["seq"]  # B's writes moved no stream of A's

            # The channel is open, so the silence above is the tenant fence
            # and not a socket that hears nothing at all.
            own = tc.post("/v1/media/files", headers=mine, json=UPLOAD)
            assert own.status_code == 201, own.text
            event = ws.receive_json()
            assert event["type"] == "event" and event["topic"] == "entity_changed"
            assert event["payload"]["target_id"] == own.json()["id"]
