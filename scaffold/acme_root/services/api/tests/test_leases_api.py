"""Leases over the live app: an ask granted at once and one that waits, the
line, a renewal for the term or a named length and a release by the holder
alone, the grant that follows, a manager's reorder and revocation, the
refusals of a malformed ask, a replayed ask that joins no line twice, and
the tenant boundary."""

from typing import Any
from uuid import UUID, uuid4

import httpx
from api_support import add_member, seed_request, sign_in_as

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import Role
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.services.api.container import AppContainer


async def org_of(client: httpx.AsyncClient, headers: dict[str, str]) -> UUID:
    return UUID((await client.get("/v1/me", headers=headers)).json()["org"]["id"])


async def a_dock(container: AppContainer, org_id: UUID, *labels: str) -> Resource:
    """A resource its owner namespace registers, as no route does."""
    ctx = await container.managers.tenancy.service_context(seed_request(), org_id, EMPTY_UUID)
    now = utcnow()
    return await container.managers.leases.register(
        ctx,
        Resource(
            id=new_id(), created_at=now, updated_at=now, created_by=EMPTY_UUID,
            updated_by=EMPTY_UUID, kind=ResourceKind.NOOP, ref_id=new_id(), labels=labels,
        ),
    )  # fmt: skip


async def ask(
    client: httpx.AsyncClient, headers: dict[str, str], key: str | None = None, **body: Any
) -> httpx.Response:
    return await client.post(
        "/v1/leases/requests",
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"kind": "noop", **body},
    )


async def a_member(
    client: httpx.AsyncClient, container: AppContainer, org_id: UUID, name: str, role: Role
) -> dict[str, str]:
    await add_member(container, org_id, f"{name}@example.test", role)
    return await sign_in_as(client, f"{name}@example.test", org_id)


async def test_a_lease_is_granted_renewed_released_and_the_line_moves_on(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = await org_of(client, owner)
    dock = await a_dock(container, org_id)
    ann = await a_member(client, container, org_id, "kai", Role.MEMBER)
    mia = await a_member(client, container, org_id, "mia", Role.MEMBER)
    first = await ask(client, ann, resource_id=str(dock.id))
    assert first.status_code == 201, first.text
    lease = first.json()["lease"]
    assert lease["fencing_token"] == 1 and 0 < lease["expires_in_seconds"] <= 60
    second = await ask(client, mia, resource_id=str(dock.id))
    waits = second.json()
    assert waits["lease"] is None and waits["place"] == 1 and waits["estimate_seconds"] > 0
    line = await client.get(f"/v1/leases/resources/{dock.id}/line", headers=ann)
    assert [r["id"] for r in line.json()["requests"]] == [waits["request"]["id"]]
    assert line.json()["resource"]["lease_id"] == lease["id"]
    lease_id = lease["id"]
    assert (await client.post(f"/v1/leases/{lease_id}/renew", headers=mia)).status_code == 403
    assert (await client.post(f"/v1/leases/{lease_id}/release", headers=mia)).status_code == 403
    renewed = await client.post(f"/v1/leases/{lease_id}/renew", headers=ann)
    assert renewed.status_code == 200 and renewed.json()["status"] == "active"
    renew = f"/v1/leases/{lease_id}/renew"
    named = await client.post(renew, headers=ann, json={"seconds": 200})
    assert named.status_code == 200 and 190 < named.json()["expires_in_seconds"] <= 200
    bounded = await client.post(renew, headers=ann, json={"seconds": 5000})
    assert 290 < bounded.json()["expires_in_seconds"] <= 300, "the resource's bound"
    assert (await client.post(renew, headers=ann, json={"seconds": 0})).status_code == 422
    released = await client.post(f"/v1/leases/{lease_id}/release", headers=ann)
    assert released.status_code == 200 and released.json()["status"] == "released"
    ended = await client.post(f"/v1/leases/{lease_id}/renew", headers=ann)
    assert ended.status_code == 409 and ended.json()["error"]["code"] == "lease_ended"
    now = (await client.get(f"/v1/leases/requests/{waits['request']['id']}", headers=mia)).json()
    assert now["lease"]["fencing_token"] == 2 and now["request"]["status"] == "granted"


async def test_a_manager_reorders_and_revokes_and_a_member_does_neither(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = await org_of(client, owner)
    dock = await a_dock(container, org_id)
    ann = await a_member(client, container, org_id, "kai", Role.MEMBER)
    held = (await ask(client, ann, resource_id=str(dock.id))).json()["lease"]
    a = (await ask(client, ann, resource_id=str(dock.id))).json()["request"]
    b = (await ask(client, owner, resource_id=str(dock.id))).json()["request"]
    reorder = f"/v1/leases/requests/{b['id']}/reorder"
    refused = await client.post(reorder, headers=ann, json={"before_id": a["id"]})
    assert refused.status_code == 403
    moved = await client.post(reorder, headers=owner, json={"before_id": a["id"]})
    assert moved.status_code == 200 and moved.json()["rank"] < a["rank"]
    assert (await client.post(f"/v1/leases/{held['id']}/revoke", headers=ann)).status_code == 403
    revoked = await client.post(f"/v1/leases/{held['id']}/revoke", headers=owner)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    granted = (await client.get(f"/v1/leases/requests/{b['id']}", headers=owner)).json()
    assert granted["lease"]["fencing_token"] == 2
    cancelled = await client.post(f"/v1/leases/requests/{a['id']}/cancel", headers=ann)
    assert cancelled.status_code == 200 and cancelled.json()["end_reason"] == "asked"


async def test_a_malformed_ask_is_refused_and_a_replay_joins_no_line_twice(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = await org_of(client, owner)
    dock = await a_dock(container, org_id, "cold")
    refusals: list[dict[str, Any]] = [
        {"labels": ["x" * 201]},
        {"labels": ["a line\nbreak"]},
        {"labels": [str(n) for n in range(161)]},
        {"resource_id": str(dock.id), "labels": ["cold"]},
        {},
        {"resource_id": str(dock.id), "payload": {"run": "anything"}},
        {"resource_id": str(dock.id), "term_seconds": 0},
        {"resource_id": str(dock.id), "start_seconds": 0},
        {"resource_id": str(dock.id), "term_seconds": 604_801},
        {"kind": "nothing", "labels": []},
        {"resource_id": "not-an-id"},
    ]
    for body in refusals:
        answer = await ask(client, owner, **body)
        assert answer.status_code == 422, f"{body}: {answer.status_code} {answer.text}"
    assert (await ask(client, owner, resource_id=str(uuid4()))).status_code == 404
    held = await ask(client, owner, labels=["cold"])
    assert held.json()["lease"] is not None
    key = str(uuid4())
    first = await ask(client, owner, key=key, resource_id=str(dock.id))
    again = await ask(client, owner, key=key, resource_id=str(dock.id))
    assert again.json()["request"]["id"] == first.json()["request"]["id"]
    line = await client.get(f"/v1/leases/resources/{dock.id}/line", headers=owner)
    assert len(line.json()["requests"]) == 1


async def test_another_tenants_leases_answer_as_missing_ones(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    """Tenant B names A's request, lease, and resource on every route that
    takes one; each answer is the 404 an unknown id gets, and A's lease is as
    it was."""
    org_id = await org_of(client, owner)
    dock = await a_dock(container, org_id)
    granted = (await ask(client, owner, resource_id=str(dock.id))).json()
    _, other = await container.managers.tenancy.bootstrap(
        seed_request(), "Other", "other", "eve@other.test", "Eve"
    )
    eve = await sign_in_as(client, "eve@other.test", other.id)
    request_id, lease_id = granted["request"]["id"], granted["lease"]["id"]
    swept: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", f"/v1/leases/requests/{request_id}", {}),
        ("POST", f"/v1/leases/requests/{request_id}/cancel", {}),
        ("POST", f"/v1/leases/requests/{request_id}/reorder", {"json": {}}),
        ("GET", f"/v1/leases/resources/{dock.id}/line", {}),
        ("GET", f"/v1/leases/{lease_id}", {}),
        ("POST", f"/v1/leases/{lease_id}/renew", {}),
        ("POST", f"/v1/leases/{lease_id}/release", {}),
        ("POST", f"/v1/leases/{lease_id}/revoke", {}),
    ]
    for method, path, extra in swept:
        answered = await client.request(method, path, headers=eve, **extra)
        assert answered.status_code == 404, f"{method} {path}: {answered.status_code}"
    assert (await ask(client, eve, resource_id=str(dock.id))).status_code == 404
    kept = await client.get(f"/v1/leases/{lease_id}", headers=owner)
    assert kept.json()["status"] == "active"
