"""Deleting a team org over the wire: the owner's typed name, the refusals in
the error envelope, everyone in it signed out of it the moment the answer
comes, and the owner landing in their personal org."""

from uuid import UUID

import httpx
from api_support import OWNER, add_member, dev_login, sign_in_as

from acme.om.context import Role
from acme.services.api.container import AppContainer


async def org_of(client: httpx.AsyncClient, headers: dict[str, str]) -> UUID:
    return UUID((await client.get("/v1/orgs/current", headers=headers)).json()["id"])


async def test_only_the_owner_deletes_and_only_by_the_orgs_name(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    ajax = await org_of(client, owner)
    await add_member(container, ajax, "bob@example.test", Role.MEMBER)
    await add_member(container, ajax, "cid@example.test", Role.ADMIN)
    for email in ("bob@example.test", "cid@example.test"):
        other = await sign_in_as(client, email, ajax)
        refused = await client.post(
            "/v1/orgs/current/deletion", headers=other, json={"name": "Ajax"}
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "not_authorized"
    wrong = await client.post("/v1/orgs/current/deletion", headers=owner, json={"name": "ajax"})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "validation_failed"
    assert (await client.get("/v1/orgs/current", headers=owner)).status_code == 200


async def test_a_personal_org_is_refused_on_this_route(client: httpx.AsyncClient) -> None:
    login = await dev_login(client, OWNER["email"])
    places = await client.get("/v1/auth/memberships", headers={"Authorization": f"Bearer {login}"})
    home = next(p["org"] for p in places.json()["items"] if p["org"]["kind"] == "personal")
    at_home = await sign_in_as(client, OWNER["email"], UUID(home["id"]))
    refused = await client.post(
        "/v1/orgs/current/deletion", headers=at_home, json={"name": home["name"]}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "personal_org_fixed"


async def test_everyone_is_out_at_once_and_the_owner_lands_at_home(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    ajax = await org_of(client, owner)
    await add_member(container, ajax, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", ajax)
    key = await client.post("/v1/api-keys", headers=bob, json={"name": "ci", "role": "member"})
    assert key.status_code == 201, key.text

    deleted = await client.post("/v1/orgs/current/deletion", headers=owner, json={"name": "Ajax"})
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted_at"]
    landing = body["session"]
    assert landing["org"]["kind"] == "personal" and landing["role"] == "owner"

    for headers in (owner, bob, {"Authorization": f"Bearer {key.json()['key']}"}):
        assert (await client.get("/v1/me", headers=headers)).status_code == 401
    home = {"Authorization": f"Bearer {landing['token']}"}
    me = await client.get("/v1/me", headers=home)
    assert me.status_code == 200 and me.json()["org"]["id"] == landing["org"]["id"]
    # Nobody's picker offers Ajax any more.
    for email in (OWNER["email"], "bob@example.test"):
        login = await dev_login(client, email)
        places = await client.get(
            "/v1/auth/memberships", headers={"Authorization": f"Bearer {login}"}
        )
        assert str(ajax) not in [p["org"]["id"] for p in places.json()["items"]]
    # The owner, last owner of Ajax a moment ago, deletes their account now.
    gone = await client.post("/v1/me/deletion", headers=home, json={"email": OWNER["email"]})
    assert gone.status_code == 200, gone.text
