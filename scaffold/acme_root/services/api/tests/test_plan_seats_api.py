"""Every door into an org takes a seat of its plan: an invitation is refused
before anything reaches the identity provider, its acceptance asks again,
since a seat can be taken between the two, and a join through the org's
single sign-on asks the same. Over the whole API in-process, with the
identity provider's twin."""

from pathlib import Path
from uuid import UUID

import httpx
import pytest
from api_support import account_written, add_member, build_container, sign_in

from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import Role
from acme.services.api.container import AppContainer

VERIFIER = "v" * 43


@pytest.fixture
def twin() -> IdentityProviderTwinImpl:
    return IdentityProviderTwinImpl()


@pytest.fixture
def container(tmp_path: Path, twin: IdentityProviderTwinImpl) -> AppContainer:
    return build_container(tmp_path, integrations=IntegrationsOverImpl(twin))


async def invite(
    client: httpx.AsyncClient, headers: dict[str, str], email: str, key: str
) -> httpx.Response:
    return await client.post(
        "/v1/invitations",
        headers={**headers, "Idempotency-Key": key},
        json={"email": email, "role": "member"},
    )


async def regrant(container: AppContainer, org_id: UUID, plan: Plan) -> None:
    """The org's grant changed, as an operator would change it."""
    storage = container.storage.get_billing_storage()
    account = await storage.read_account(org_id)
    assert account is not None
    await storage.write_account(org_id, account.model_copy(update={"comped_plan": plan}), ())
    await account_written(container, org_id)


async def test_an_invitation_past_the_seats_is_refused_before_anything_is_sent(
    client: httpx.AsyncClient, container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    free = await sign_in(client, container, plan=None)
    refused = await invite(client, free, "bob@example.test", "inv-1")
    assert refused.status_code == 402, refused.text
    assert refused.json()["error"]["plan_limit"] == {
        "lever": "members",
        "plan": "free",
        "limit": 1,
        "suggested_plan": "team",
    }
    assert twin.sent == []
    assert (await client.get("/v1/invitations", headers=free)).json()["items"] == []


async def test_a_seat_taken_between_the_invitation_and_its_acceptance_keeps_it_pending(
    client: httpx.AsyncClient, container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    owner = await sign_in(client, container, plan=Plan.TEAM)
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    for n in range(3):
        await add_member(container, org_id, f"m{n}@example.test", Role.MEMBER)
    sent = await invite(client, owner, "bob@example.test", "inv-1")
    assert sent.status_code == 201, sent.text  # four of five seats taken
    await add_member(container, org_id, "late@example.test", Role.MEMBER)  # the fifth
    full = await invite(client, owner, "cat@example.test", "inv-2")
    assert full.status_code == 402 and full.json()["error"]["plan_limit"]["suggested_plan"] == "max"
    code = twin.accept_invitation(twin.sent[0].id)
    signed_in = await client.post(
        "/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER}
    )
    # The sign-in goes on into the places the person has; the org is not one.
    assert signed_in.status_code == 200, signed_in.text
    assert org_id not in {UUID(m["org"]["id"]) for m in signed_in.json()["memberships"]}
    pending = (await client.get("/v1/invitations", headers=owner)).json()["items"]
    assert [i["state"] for i in pending] == ["pending"]
    # With a seat again, the same acceptance lands.
    await regrant(container, org_id, Plan.MAX)
    again = await client.post(
        "/v1/auth/callback",
        json={"code": twin.accept_invitation(twin.sent[0].id), "code_verifier": VERIFIER},
    )
    assert org_id in {UUID(m["org"]["id"]) for m in again.json()["memberships"]}


async def test_a_join_through_single_sign_on_takes_a_seat(
    client: httpx.AsyncClient, container: AppContainer, twin: IdentityProviderTwinImpl
) -> None:
    owner = await sign_in(client, container, plan=Plan.TEAM)
    org_id = UUID((await client.get("/v1/orgs/current", headers=owner)).json()["id"])
    for n in range(3):
        await add_member(container, org_id, f"m{n}@ajax.example", Role.MEMBER)
    # The org's organization at the provider, made by the first invitation.
    assert (await invite(client, owner, "bob@example.test", "inv-1")).status_code == 201
    org = await container.storage.get_tenancy_storage().read_org(org_id)
    assert org is not None and org.provider_org_id is not None
    twin.verify_domain(org.provider_org_id, "ajax.example")
    await add_member(container, org_id, "late@ajax.example", Role.MEMBER)  # five of five

    async def through_sso(email: str) -> set[UUID]:
        code = twin.issue_code(email, organization_id=org.provider_org_id, via_sso=True)
        answer = await client.post(
            "/v1/auth/callback", json={"code": code, "code_verifier": VERIFIER}
        )
        assert answer.status_code == 200, answer.text
        return {UUID(m["org"]["id"]) for m in answer.json()["memberships"]}

    assert org_id not in await through_sso("eve@ajax.example")
    await regrant(container, org_id, Plan.MAX)
    assert org_id in await through_sso("eve@ajax.example")
