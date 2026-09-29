"""Invitations through the identity provider and the single sign-on link, over
the whole API in-process with the provider's twin."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import pytest
from api_support import add_member, build_container, seed_request, sign_in, sign_in_as
from httpx import ASGITransport

from acme.integrations.identity.twin import TWIN_PORTAL, IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.om.context import Role
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer

PORTAL = "http://localhost:55173/settings"

VERIFIER = "v" * 43


@pytest.fixture
def twin() -> IdentityProviderTwinImpl:
    return IdentityProviderTwinImpl()


@pytest.fixture
def container(tmp_path: Path, twin: IdentityProviderTwinImpl) -> AppContainer:
    return build_container(tmp_path, integrations=IntegrationsOverImpl(twin))


async def invite(
    client: httpx.AsyncClient, headers: dict[str, str], email: str, key: str, role: str = "member"
) -> httpx.Response:
    return await client.post(
        "/v1/invitations",
        headers={**headers, "Idempotency-Key": key},
        json={"email": email, "role": role},
    )


async def test_an_invitation_is_sent_once_listed_resent_and_revoked(
    client: httpx.AsyncClient, owner: dict[str, str], twin: IdentityProviderTwinImpl
) -> None:
    first = await invite(client, owner, "bob@example.test", "inv-1")
    assert first.status_code == 201, first.text
    invitation = first.json()
    assert invitation["state"] == "pending" and invitation["role"] == "member"
    # The provider sent the email, to the org's organization made for it.
    assert [sent.email for sent in twin.sent] == ["bob@example.test"]
    # A retry under the same key answers the first, and sends nothing more.
    replay = await invite(client, owner, "bob@example.test", "inv-1")
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == invitation and len(twin.sent) == 1
    # A second invitation for an open one is refused: send that one again.
    open_one = await invite(client, owner, "bob@example.test", "inv-2")
    assert open_one.status_code == 409, open_one.text

    listed = await client.get("/v1/invitations", headers=owner)
    assert listed.status_code == 200, listed.text
    assert [i["id"] for i in listed.json()["items"]] == [invitation["id"]]
    assert listed.json()["next_cursor"] is None

    resent = await client.post(f"/v1/invitations/{invitation['id']}/resend", headers=owner)
    assert resent.status_code == 200, resent.text
    assert len(twin.sent) == 2

    revoked = await client.delete(f"/v1/invitations/{invitation['id']}", headers=owner)
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    assert (await client.get("/v1/invitations", headers=owner)).json()["items"] == []
    closed = await client.post(f"/v1/invitations/{invitation['id']}/resend", headers=owner)
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "invitation_closed"


async def test_the_invited_person_lands_in_the_org_with_the_role(
    client: httpx.AsyncClient, owner: dict[str, str], twin: IdentityProviderTwinImpl
) -> None:
    sent = await invite(client, owner, "bob@example.test", "inv-1", role="admin")
    assert sent.status_code == 201, sent.text
    org_id = (await client.get("/v1/orgs/current", headers=owner)).json()["id"]
    code = twin.accept_invitation(twin.sent[0].id)
    signed_in = await client.post(
        "/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER}
    )
    assert signed_in.status_code == 200, signed_in.text
    places = {m["org"]["id"]: m["role"] for m in signed_in.json()["memberships"]}
    assert places[org_id] == "admin" and len(places) == 2
    assert (await client.get("/v1/invitations", headers=owner)).json()["items"] == []


async def test_only_a_member_manager_invites(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", org_id)
    refused = await invite(client, bob, "cat@example.test", "inv-1")
    assert refused.status_code == 403, refused.text
    assert (await client.get("/v1/invitations", headers=bob)).status_code == 403
    above = await invite(client, owner, "cat@example.test", "inv-2", role="service")
    assert above.status_code == 422, above.text
    member = await invite(client, owner, "bob@example.test", "inv-3")
    assert member.status_code == 409, "a member already"


async def test_another_tenants_invitation_does_not_exist(
    client: httpx.AsyncClient, container: AppContainer, owner: dict[str, str]
) -> None:
    sent = (await invite(client, owner, "bob@example.test", "inv-1")).json()
    # Another tenant, its own owner.
    _, beta = await container.managers.tenancy.bootstrap(
        seed_request(), "Beta", "beta", "bea@example.test", "Bea"
    )
    bea = await sign_in_as(client, "bea@example.test", beta.id)
    for answer in (
        await client.post(f"/v1/invitations/{sent['id']}/resend", headers=bea),
        await client.delete(f"/v1/invitations/{sent['id']}", headers=bea),
    ):
        assert answer.status_code == 404, answer.text
    assert (await client.get("/v1/invitations", headers=bea)).json()["items"] == []


async def test_a_team_org_opens_single_sign_on_and_a_personal_one_has_none(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    link = await client.post(
        "/v1/orgs/current/sso-link", headers=owner, json={"intent": "sso", "return_url": PORTAL}
    )
    assert link.status_code == 200, link.text
    url = urlparse(link.json()["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == TWIN_PORTAL
    assert parse_qs(url.query)["intent"] == ["sso"]
    elsewhere = await client.post(
        "/v1/orgs/current/sso-link",
        headers=owner,
        json={"intent": "sso", "return_url": "https://evil.example/settings"},
    )
    assert elsewhere.status_code == 422, elsewhere.text

    places = (await client.get("/v1/auth/memberships", headers=owner)).json()["items"]
    personal = next(m["org"]["id"] for m in places if m["org"]["kind"] == "personal")
    token = owner["Authorization"].removeprefix("Bearer ")
    switched = await client.post(
        "/v1/auth/sessions",
        json={"org_id": personal},
        headers={"Authorization": f"Bearer {token}"},
    )
    home = {"Authorization": f"Bearer {switched.json()['token']}"}
    refused = await client.post(
        "/v1/orgs/current/sso-link", headers=home, json={"intent": "sso", "return_url": PORTAL}
    )
    assert refused.status_code == 422, refused.text


async def test_single_sign_on_is_refused_where_no_provider_is(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    app = create_app(container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            owner = await sign_in(client, container)
            link = await client.post(
                "/v1/orgs/current/sso-link",
                headers=owner,
                json={"intent": "sso", "return_url": PORTAL},
            )
            sent = await invite(client, owner, "bob@example.test", "inv-1")
    assert link.status_code == 503, link.text
    assert sent.status_code == 503, sent.text
