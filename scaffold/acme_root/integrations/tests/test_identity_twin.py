"""The identity provider's twin speaks the interface's shapes and plays the
person's side a test drives."""

import pytest

from acme.integrations.exceptions import (
    DeviceDenied,
    DeviceExpired,
    DevicePending,
    ProviderConflict,
    ProviderRefused,
)
from acme.integrations.identity import InvitationState
from acme.integrations.identity.twin import TWIN_ISSUER, IdentityProviderTwinImpl, s256


async def test_a_code_is_exchanged_once_for_the_person_it_names() -> None:
    twin = IdentityProviderTwinImpl()
    code = twin.issue_code("dee@example.test", first_name="Dee")
    signed_in = await twin.authenticate_code(code, code_verifier=None)
    assert signed_in.user.email == "dee@example.test" and signed_in.user.id.startswith("twin_")
    assert signed_in.user.display_name == "Dee" and twin.issuer == TWIN_ISSUER
    with pytest.raises(ProviderRefused):
        await twin.authenticate_code(code, code_verifier=None)
    again = await twin.authenticate_code(twin.issue_code("dee@example.test"), code_verifier=None)
    assert again.user.id == signed_in.user.id


async def test_a_code_names_the_browser_session_and_a_device_none() -> None:
    twin = IdentityProviderTwinImpl()
    first = await twin.authenticate_code(twin.issue_code("dee@example.test"), code_verifier=None)
    second = await twin.authenticate_code(twin.issue_code("dee@example.test"), code_verifier=None)
    assert first.session_id and second.session_id and first.session_id != second.session_id
    url = twin.logout_url(session_id=first.session_id, return_to="http://localhost/signed-out")
    assert f"session_id={first.session_id}" in url and "return_to=" in url and ".invalid/" in url
    started = await twin.start_device()
    twin.confirm_device(started.user_code, "dee@example.test")
    device = await twin.authenticate_device(started.device_code)
    assert device.session_id is None


async def test_a_code_issued_with_a_challenge_needs_its_verifier() -> None:
    twin = IdentityProviderTwinImpl()
    with pytest.raises(ProviderRefused):
        await twin.authenticate_code(
            twin.issue_code("dee@example.test", code_challenge=s256("right")),
            code_verifier="wrong",
        )
    code = twin.issue_code("dee@example.test", code_challenge=s256("right"))
    signed_in = await twin.authenticate_code(code, code_verifier="right")
    assert signed_in.user.email == "dee@example.test"


def test_the_authorization_url_carries_what_it_was_handed() -> None:
    url = IdentityProviderTwinImpl().authorization_url(
        redirect_uri="http://localhost:55173/auth/callback",
        state="abc",
        code_challenge="ch",
        screen_hint="sign-up",
    )
    assert "state=abc" in url and "screen_hint=sign-up" in url and ".invalid/" in url


async def test_a_device_sign_in_is_pending_until_confirmed_then_spent() -> None:
    twin = IdentityProviderTwinImpl()
    started = await twin.start_device()
    with pytest.raises(DevicePending):
        await twin.authenticate_device(started.device_code)
    twin.confirm_device(started.user_code, "dee@example.test")
    assert (await twin.authenticate_device(started.device_code)).user.email == "dee@example.test"
    with pytest.raises(ProviderRefused):
        await twin.authenticate_device(started.device_code)
    denied = await twin.start_device()
    twin.confirm_device(denied.user_code, "x@example.test", deny=True)
    with pytest.raises(DeviceDenied):
        await twin.authenticate_device(denied.device_code)
    expired = await twin.start_device()
    twin.expire_device(expired.user_code)
    with pytest.raises(DeviceExpired):
        await twin.authenticate_device(expired.device_code)


async def test_an_organization_is_made_once_per_external_id() -> None:
    twin = IdentityProviderTwinImpl()
    first = await twin.ensure_organization(external_id="ext-1", name="Ajax")
    assert await twin.ensure_organization(external_id="ext-1", name="Other") == first
    twin.verify_domain(first.id, "Ajax.Example")
    assert (await twin.get_organization(first.id)).verified_domains == ("ajax.example",)
    with pytest.raises(ProviderRefused):
        await twin.get_organization("twin_org_missing")


async def test_an_invitation_is_sent_once_resent_revoked_and_accepted() -> None:
    twin = IdentityProviderTwinImpl()
    org = await twin.ensure_organization(external_id="ext-1", name="Ajax")
    sent = await twin.send_invitation(
        email="bob@ajax.example", organization_id=org.id, expires_in_days=7
    )
    with pytest.raises(ProviderConflict):
        await twin.send_invitation(
            email="bob@ajax.example", organization_id=org.id, expires_in_days=7
        )
    found = await twin.find_pending_invitation(email="bob@ajax.example", organization_id=org.id)
    assert found == sent
    await twin.resend_invitation(sent.id)
    assert len(twin.sent) == 2
    code = twin.accept_invitation(sent.id, "robert@ajax.example")
    signed_in = await twin.authenticate_code(code, code_verifier=None)
    assert signed_in.organization_id == org.id
    accepted = await twin.accepted_invitation(
        organization_id=org.id, user_id=signed_in.user.id, email=signed_in.user.email
    )
    assert accepted is not None and accepted.state is InvitationState.ACCEPTED
    with pytest.raises(ProviderRefused):
        await twin.revoke_invitation(sent.id)
    other = await twin.send_invitation(
        email="cat@ajax.example", organization_id=org.id, expires_in_days=7
    )
    assert (await twin.revoke_invitation(other.id)).state is InvitationState.REVOKED


async def test_a_portal_link_is_for_an_organization_it_holds() -> None:
    twin = IdentityProviderTwinImpl()
    org = await twin.ensure_organization(external_id="ext-1", name="Ajax")
    link = await twin.portal_link(organization_id=org.id, intent="sso", return_url="http://x/s")
    assert f"organization={org.id}" in link
    with pytest.raises(ProviderRefused):
        await twin.portal_link(organization_id="nope", intent="sso", return_url="http://x/s")
