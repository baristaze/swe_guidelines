"""Deleting my account over the wire: the confirmation, the two refusals in
the error envelope, and a person who is gone the moment the answer comes."""

from uuid import UUID

import httpx
from api_support import OWNER, add_member, dev_login, seed_request, sign_in_as

from acme.om.context import OperatorRole, Role
from acme.services.api.container import AppContainer


async def org_of(client: httpx.AsyncClient, headers: dict[str, str]) -> UUID:
    return UUID((await client.get("/v1/orgs/current", headers=headers)).json()["id"])


async def test_the_confirmation_must_be_the_accounts_email(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    ajax = await org_of(client, owner)
    await add_member(container, ajax, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", ajax)
    wrong = await client.post("/v1/me/deletion", headers=bob, json={"email": OWNER["email"]})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "validation_failed"
    missing = await client.post("/v1/me/deletion", headers=bob, json={})
    assert missing.status_code == 422
    assert (await client.get("/v1/me", headers=bob)).status_code == 200


async def test_the_last_owner_is_refused_with_the_orgs_named(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    ajax = await org_of(client, owner)
    refused = await client.post("/v1/me/deletion", headers=owner, json={"email": OWNER["email"]})
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "last_owner"
    assert error["last_owner"] == {"orgs": [{"id": str(ajax), "name": "Ajax", "slug": "ajax"}]}
    assert "Ajax" in error["message"]
    UUID(error["request_id"])
    assert (await client.get("/v1/me", headers=owner)).status_code == 200


async def test_an_operator_is_refused_until_the_role_is_off(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    ajax = await org_of(client, owner)
    await add_member(container, ajax, "ops@example.test", Role.MEMBER)
    ops = await sign_in_as(client, "ops@example.test", ajax)
    tenancy = container.managers.tenancy
    await tenancy.grant_operator(seed_request(), "ops@example.test", OperatorRole.READ)
    refused = await client.post("/v1/me/deletion", headers=ops, json={"email": "ops@example.test"})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "operator_role_held"
    assert "last_owner" not in refused.json()["error"]


async def test_a_deleted_account_is_signed_out_everywhere_and_can_start_again(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    ajax = await org_of(client, owner)
    bob_user = await add_member(container, ajax, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", ajax)
    places = await client.get(
        "/v1/auth/memberships",
        headers={"Authorization": f"Bearer {await dev_login(client, 'bob@example.test')}"},
    )
    home = next(p["org"]["id"] for p in places.json()["items"] if p["org"]["kind"] == "personal")
    at_home = await sign_in_as(client, "bob@example.test", UUID(home))
    key = await client.post("/v1/api-keys", headers=at_home, json={"name": "ci", "role": "member"})
    assert key.status_code == 201, key.text

    deleted = await client.post(
        "/v1/me/deletion", headers=bob, json={"email": " Bob@Example.test "}
    )
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted_at"] and body["provider_logout_url"] is None, "a local sign-in"

    for headers in (bob, at_home, {"Authorization": f"Bearer {key.json()['key']}"}):
        assert (await client.get("/v1/me", headers=headers)).status_code == 401
    # In Ajax the person no longer resolves: no user by that id, no member.
    users = await client.get("/v1/users", headers=owner)
    assert str(bob_user.id) not in [u["id"] for u in users.json()["items"]]
    members = await client.get("/v1/memberships", headers=owner)
    assert str(bob_user.id) not in [m["user_id"] for m in members.json()["items"]]
    # The same address signs up again as somebody new.
    again = await client.get(
        "/v1/auth/memberships",
        headers={"Authorization": f"Bearer {await dev_login(client, 'bob@example.test')}"},
    )
    fresh = again.json()["items"]
    assert [p["org"]["kind"] for p in fresh] == ["personal"]
    assert fresh[0]["org"]["id"] != home
