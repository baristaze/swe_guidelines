"""Signing in through the identity provider, the local sign-in, invitations,
and single sign-on, over the memory storage and the provider's twin."""

from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from contracts.plans import ON_TEAM
from contracts.second_factor import TOTP_KEY, SteppingClock

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity import InvitationState as ProvidedState
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.identity.twin import TWIN_ISSUER, IdentityProviderTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    EmailNotVerified,
    InvalidCredential,
    InvitationClosed,
    NotAuthorized,
    NotFound,
    SignInPending,
    SignInRefused,
    SignInSlowDown,
    Unavailable,
    ValidationFailed,
)
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.idempotency.types.attempt import Attempt
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.opcontext import AppContext, AppType, IdentityContext, OpContext, RequestContext, Role
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.tenancy.rules import email_digest, pkce_challenge
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.issued import IssuedLogin
from acme.om.tenancy.types.org import Org, OrgKind

APP = AppContext(type=AppType.PORTAL, version="portal@test")
CALLBACK = "http://localhost:55173/auth/callback"
SETTINGS = "http://localhost:55173/settings"
SIGNED_OUT = "http://localhost:55173/signed-out"


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


@pytest.fixture
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def outbox() -> OutboxStorageMemoryImpl:
    return OutboxStorageMemoryImpl()


@pytest.fixture
def markers() -> IdempotencyStorageMemoryImpl:
    return IdempotencyStorageMemoryImpl()


@pytest.fixture
def storage(
    outbox: OutboxStorageMemoryImpl, markers: IdempotencyStorageMemoryImpl
) -> TenancyStorageMemoryImpl:
    return TenancyStorageMemoryImpl(outbox, markers)


@pytest.fixture
def twin() -> IdentityProviderTwinImpl:
    return IdentityProviderTwinImpl()


def build(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    outbox: OutboxStorageMemoryImpl,
    twin: IdentityProviderTwinImpl | None,
    *,
    dev_sign_in: bool = True,
) -> TenancyManagerImpl:
    relay = OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics())
    return TenancyManagerImpl(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(
            dev_sign_in=dev_sign_in,
            totp_encryption_key=TOTP_KEY,
            sign_in_redirect_uris=(CALLBACK,),
            sign_out_return_uris=(SIGNED_OUT,),
        ),
        SteppingClock(),
        identity_provider=twin or IdentityProviderAbsentImpl(),
        entitlements=ON_TEAM,
    )


@pytest.fixture
def manager(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    outbox: OutboxStorageMemoryImpl,
    twin: IdentityProviderTwinImpl,
) -> TenancyManagerImpl:
    return build(storage, infra, outbox, twin)


async def enter(manager: TenancyManagerImpl, login: IssuedLogin, org_id: UUID) -> OpContext:
    session = await manager.exchange_login(
        await manager.authenticate_login(request(), login.token), org_id
    )
    return await manager.authenticate(request(), session.token)


async def signed_in_at(
    manager: TenancyManagerImpl, login: IssuedLogin, org_id: UUID
) -> tuple[OpContext, IdentityContext]:
    """A fresh session's tenant stage and the identity stage it proves, which
    is what a sign-out takes."""
    session = await manager.exchange_login(
        await manager.authenticate_login(request(), login.token), org_id
    )
    return (
        await manager.authenticate(request(), session.token),
        await manager.authenticate_login(request(), session.token),
    )


async def owner_of_team(manager: TenancyManagerImpl, email: str = "ann@ajax.example") -> OpContext:
    """The owner of a team org, in a session there."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", email, "Ann")
    return await enter(manager, await manager.dev_sign_in(request(), email), org.id)


# Where the browser goes.


async def test_the_sign_in_url_goes_to_the_provider_with_the_state(
    manager: TenancyManagerImpl,
) -> None:
    started = await manager.sign_in_url(request(), CALLBACK, "s" * 32, sign_up=True)
    url = started.authorization_url
    # The verifier stays with the caller; the provider sees its digest.
    assert f"code_challenge={pkce_challenge(started.code_verifier)}" in url
    assert started.code_verifier not in url
    assert "state=" + "s" * 32 in url and "screen_hint=sign-up" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A55173%2Fauth%2Fcallback" in url


async def test_a_redirect_that_is_not_this_environments_or_no_state_is_refused(
    manager: TenancyManagerImpl,
) -> None:
    with pytest.raises(ValidationFailed):
        await manager.sign_in_url(request(), "https://evil.example/auth/callback", "s" * 32)
    with pytest.raises(ValidationFailed):
        await manager.sign_in_url(request(), CALLBACK, "")


async def test_with_no_provider_every_sign_in_through_one_is_unavailable(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    manager = build(storage, infra, outbox, None)
    with pytest.raises(Unavailable):
        await manager.sign_in_url(request(), CALLBACK, "s" * 32)
    with pytest.raises(Unavailable):
        await manager.sign_in_with_code(request(), "any")
    with pytest.raises(Unavailable):
        await manager.start_device_sign_in(request())


# The code comes back.


async def test_a_first_sign_in_makes_the_person_with_their_personal_org(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    code = twin.issue_code("dee@example.test", first_name="Dee", last_name="Doe")
    login = await manager.sign_in_with_code(request(), code)
    assert login.token.startswith("lgn_")
    [place] = login.memberships
    assert place.org.kind is OrgKind.PERSONAL and place.org.name == "Dee Doe"
    assert place.role is Role.OWNER and place.user.display_name == "Dee Doe"
    identity = await storage.read_identity_by_email_digest(email_digest("dee@example.test"))
    assert identity is not None
    assert (identity.issuer, identity.subject) == (TWIN_ISSUER, twin.user("dee@example.test").id)
    # A second sign-in finds the same person and the same one place.
    again = await manager.sign_in_with_code(request(), twin.issue_code("dee@example.test"))
    assert [m.org.id for m in again.memberships] == [place.org.id]


async def test_a_person_the_seeding_made_is_linked_by_the_verified_email(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    before = await storage.read_identity_by_email_digest(email_digest("ann@example.test"))
    assert before is not None and before.subject is None
    login = await manager.sign_in_with_code(request(), twin.issue_code("ann@example.test"))
    assert org.id in {m.org.id for m in login.memberships}
    linked = await storage.read_identity(before.id)
    assert linked is not None and linked.subject == twin.user("ann@example.test").id
    assert linked.issuer == TWIN_ISSUER


async def test_a_person_the_seeding_made_is_linked_whatever_the_case_of_the_address(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    """The seeding typed `Dee@Example.test` and the provider vouches for
    `dee@EXAMPLE.test`: one address, so one person, linked on the first
    sign-in, and nobody new."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "Dee@Example.test", "Dee")
    seeded = await storage.read_identity_by_email_digest(email_digest("dee@example.test"))
    assert seeded is not None and seeded.email == "dee@example.test"
    login = await manager.sign_in_with_code(request(), twin.issue_code("dee@EXAMPLE.test"))
    assert org.id in {m.org.id for m in login.memberships}
    linked = await storage.read_identity(seeded.id)
    assert linked is not None and linked.subject == twin.user("dee@EXAMPLE.test").id
    assert await storage.count_orgs() == 2, "Ajax and Dee's personal org"


async def test_a_linked_person_is_found_by_the_subject_whatever_their_address(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    first = await manager.sign_in_with_code(request(), twin.issue_code("dee@example.test"))
    person = twin.user("dee@example.test")
    twin.users[person.id] = person.model_copy(update={"email": "dee@new.example"})
    again = await manager.sign_in_with_code(request(), twin.issue_code("dee@new.example"))
    assert {m.org.id for m in again.memberships} == {m.org.id for m in first.memberships}
    assert await storage.read_identity_by_email_digest(email_digest("dee@new.example")) is None


async def test_an_unverified_address_or_the_platforms_is_refused(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    with pytest.raises(EmailNotVerified):
        code = twin.issue_code("dee@example.test", email_verified=False)
        await manager.sign_in_with_code(request(), code)
    with pytest.raises(InvalidCredential):
        code = twin.issue_code("smoke@platform.acme.invalid")
        await manager.sign_in_with_code(request(), code)
    assert await storage.count_orgs() == 0


async def test_a_spent_code_is_refused(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    code = twin.issue_code("dee@example.test")
    await manager.sign_in_with_code(request(), code)
    with pytest.raises(SignInRefused):
        await manager.sign_in_with_code(request(), code)
    with pytest.raises(SignInRefused):
        await manager.sign_in_with_code(request(), "never-issued")


# The device.


async def test_a_device_sign_in_waits_for_the_person_and_then_signs_them_in(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    started = await manager.start_device_sign_in(request())
    assert started.user_code in started.verification_uri_complete
    with pytest.raises(SignInPending):
        await manager.finish_device_sign_in(request(), started.device_code)
    twin.confirm_device(started.user_code, "dee@example.test")
    login = await manager.finish_device_sign_in(request(), started.device_code)
    assert [m.org.kind for m in login.memberships] == [OrgKind.PERSONAL]
    with pytest.raises(SignInRefused):
        await manager.finish_device_sign_in(request(), started.device_code)


async def test_a_device_sign_in_declined_or_expired_is_refused(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    declined = await manager.start_device_sign_in(request())
    twin.confirm_device(declined.user_code, "dee@example.test", deny=True)
    with pytest.raises(SignInRefused):
        await manager.finish_device_sign_in(request(), declined.device_code)
    expired = await manager.start_device_sign_in(request())
    twin.expire_device(expired.user_code)
    with pytest.raises(SignInRefused):
        await manager.finish_device_sign_in(request(), expired.device_code)


async def test_a_device_asked_too_often_is_told_to_slow_down(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acme.integrations.exceptions import DeviceSlowDown

    async def slow(device_code: str, **_: object) -> None:
        raise DeviceSlowDown("slow down")

    monkeypatch.setattr(twin, "authenticate_device", slow)
    with pytest.raises(SignInSlowDown):
        await manager.finish_device_sign_in(request(), "any")


# The local sign-in.


async def test_the_local_sign_in_is_not_there_unless_it_is_on(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    off = build(storage, infra, outbox, None, dev_sign_in=False)
    with pytest.raises(NotFound):
        await off.dev_sign_in(request(), "dee@example.test")
    assert await storage.count_orgs() == 0


async def test_the_local_sign_in_makes_a_person_once_and_then_finds_them(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl
) -> None:
    first = await manager.dev_sign_in(request(), "dee@example.test", "Dee")
    [place] = first.memberships
    assert place.org.personal and place.org.name == "Dee"
    again = await manager.dev_sign_in(request(), "dee@example.test")
    assert [m.org.id for m in again.memberships] == [place.org.id]
    with pytest.raises(ValidationFailed):
        await manager.dev_sign_in(request(), "not-an-address")
    with pytest.raises(ValidationFailed):
        await manager.dev_sign_in(request(), "smoke@platform.acme.invalid")
    assert await storage.count_orgs() == 1


async def test_the_local_sign_in_finds_a_person_in_any_case(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl
) -> None:
    first = await manager.dev_sign_in(request(), "Dee@Example.test", "Dee")
    [place] = first.memberships
    assert place.user.email == "dee@example.test"
    again = await manager.dev_sign_in(request(), "DEE@example.TEST")
    assert [m.org.id for m in again.memberships] == [place.org.id]
    assert await storage.count_orgs() == 1


# Invitations.


async def test_an_invitation_is_sent_through_the_provider_and_listed(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.ADMIN)
    assert invitation.state is InvitationState.PENDING and invitation.role is Role.ADMIN
    assert invitation.created_by == ann.user_id
    [sent] = twin.sent
    assert sent.email == "bob@ajax.example" and sent.id == invitation.provider_invitation_id
    # The org's organization at the provider was made once and kept.
    org = await storage.read_org(ann.org_id)
    assert org is not None and org.provider_org_id is not None
    assert org.provider_org_id == sent.organization_id
    assert twin.organizations[org.provider_org_id].external_id == str(org.id)
    await manager.invite_member(ann, "cat@ajax.example", Role.MEMBER)
    assert len(twin.organizations) == 1
    page = await manager.get_invitations(ann, None, limit=1)
    assert [i.email for i in page.items] == ["cat@ajax.example"] and page.has_more
    rest = await manager.get_invitations(ann, page.items[-1].id, limit=1)
    assert [i.email for i in rest.items] == ["bob@ajax.example"] and not rest.has_more


async def test_an_invitation_needs_a_member_manager_and_a_role_at_most_theirs(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    await manager.add_member(request(), "ajax", "vic@ajax.example", "Vic", Role.ADMIN)
    vic = await enter(manager, await manager.dev_sign_in(request(), "vic@ajax.example"), ann.org_id)
    with pytest.raises(NotAuthorized):
        await manager.invite_member(vic, "own@ajax.example", Role.OWNER)
    await manager.add_member(request(), "ajax", "mem@ajax.example", "Mem", Role.MEMBER)
    mem = await enter(manager, await manager.dev_sign_in(request(), "mem@ajax.example"), ann.org_id)
    with pytest.raises(NotAuthorized):
        await manager.invite_member(mem, "x@ajax.example", Role.VIEWER)
    for email, role in (
        ("x@ajax.example", Role.SERVICE),
        ("smoke@platform.acme.invalid", Role.MEMBER),
        ("not-an-address", Role.MEMBER),
    ):
        with pytest.raises(ValidationFailed):
            await manager.invite_member(ann, email, role)
    assert twin.sent == []


async def test_a_member_or_an_open_invitation_is_not_invited_again(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    with pytest.raises(Conflict):
        await manager.invite_member(ann, "ann@ajax.example", Role.MEMBER)
    await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER)
    with pytest.raises(Conflict):
        await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER)
    assert len(twin.sent) == 1


async def test_an_address_is_invited_once_in_any_case(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    """The invitation keeps the address folded and sends it so. Another
    spelling of a pending invitation's address, or of a member's, is the
    same address."""
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "Bob@Ajax.example", Role.MEMBER)
    assert invitation.email == "bob@ajax.example"
    [sent] = twin.sent
    assert sent.email == "bob@ajax.example"
    with pytest.raises(Conflict):
        await manager.invite_member(ann, "bob@AJAX.example", Role.MEMBER)
    with pytest.raises(Conflict):
        await manager.invite_member(ann, "ANN@ajax.example", Role.MEMBER)
    assert len(twin.sent) == 1


async def test_an_expired_invitation_is_replaced(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    first = await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER)
    await storage.write_invitation(
        ann.org_id, first.model_copy(update={"expires_at": utcnow() - timedelta(days=1)})
    )
    twin.invitations[first.provider_invitation_id] = twin.invitations[
        first.provider_invitation_id
    ].model_copy(update={"state": ProvidedState.EXPIRED})
    second = await manager.invite_member(ann, "bob@ajax.example", Role.ADMIN)
    assert second.id != first.id and second.role is Role.ADMIN
    closed = await storage.read_invitation(ann.org_id, first.id)
    assert closed is not None and closed.state is InvitationState.REVOKED
    assert [i.id for i in (await manager.get_invitations(ann, None, 10)).items] == [second.id]


async def test_a_rerun_under_one_attempt_answers_with_the_invitation_it_made(
    manager: TenancyManagerImpl,
    markers: IdempotencyStorageMemoryImpl,
    twin: IdentityProviderTwinImpl,
) -> None:
    ann = await owner_of_team(manager)
    target, attempt_id = new_id(), new_id()
    await markers.write_record(
        ann.org_id,
        IdempotencyRecord(
            id=new_id(),
            created_at=utcnow(),
            user_id=ann.user_id,
            key="invite-bob",
            request_digest="digest",
            target_id=target,
            attempt_id=attempt_id,
        ),
    )
    attempt = Attempt(target_id=target, attempt_id=attempt_id)
    first = await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER, attempt)
    again = await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER, attempt)
    assert first.id == again.id == target and len(twin.sent) == 1


async def test_an_invitation_pending_at_the_provider_is_adopted(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    """An earlier attempt sent it and lost its answer: the provider refuses a
    second, and the one it holds is the one recorded."""
    ann = await owner_of_team(manager)
    await manager.invite_member(ann, "cat@ajax.example", Role.MEMBER)
    org = await storage.read_org(ann.org_id)
    assert org is not None and org.provider_org_id is not None
    lost = await twin.send_invitation(
        email="bob@ajax.example", organization_id=org.provider_org_id, expires_in_days=7
    )
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER)
    assert invitation.provider_invitation_id == lost.id


async def test_an_invitation_is_sent_again_or_revoked_while_pending(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER)
    resent = await manager.resend_invitation(ann, invitation.id)
    assert resent.id == invitation.id and len(twin.sent) == 2
    revoked = await manager.revoke_invitation(ann, invitation.id)
    assert revoked.state is InvitationState.REVOKED
    assert twin.invitations[invitation.provider_invitation_id].state is ProvidedState.REVOKED
    with pytest.raises(InvitationClosed):
        await manager.revoke_invitation(ann, invitation.id)
    with pytest.raises(InvitationClosed):
        await manager.resend_invitation(ann, invitation.id)
    with pytest.raises(NotFound):
        await manager.revoke_invitation(ann, new_id())
    assert (await manager.get_invitations(ann, None, 10)).items == ()


async def test_an_accepted_invitation_lands_the_membership_with_its_role(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.ADMIN)
    code = twin.accept_invitation(invitation.provider_invitation_id)
    login = await manager.sign_in_with_code(request(), code)
    [place] = [m for m in login.memberships if m.org.id == ann.org_id]
    assert place.role is Role.ADMIN
    assert place.user.created_by == ann.user_id, "recorded as the inviter's"
    accepted = await storage.read_invitation(ann.org_id, invitation.id)
    assert accepted is not None and accepted.state is InvitationState.ACCEPTED
    assert accepted.accepted_user_id == place.user.id
    assert any(m.org.personal for m in login.memberships)
    assert (await manager.get_invitations(ann, None, 10)).items == ()


async def test_an_invitation_accepted_with_another_address_of_the_domain_lands_it(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.MEMBER)
    code = twin.accept_invitation(invitation.provider_invitation_id, "robert@ajax.example")
    login = await manager.sign_in_with_code(request(), code)
    [place] = [m for m in login.memberships if m.org.id == ann.org_id]
    assert place.user.email == "robert@ajax.example" and place.role is Role.MEMBER


async def test_a_member_accepting_an_invitation_keeps_their_place(
    manager: TenancyManagerImpl,
    storage: TenancyStorageMemoryImpl,
    twin: IdentityProviderTwinImpl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.ADMIN)
    _, bob, _ = await manager.add_member(request(), "ajax", "bob@ajax.example", "Bob", Role.VIEWER)
    calls = counted(twin, monkeypatch)
    login = await manager.sign_in_with_code(
        request(), twin.accept_invitation(invitation.provider_invitation_id)
    )
    [place] = [m for m in login.memberships if m.org.id == ann.org_id]
    assert place.user.id == bob.id and place.role is Role.VIEWER
    closed = await storage.read_invitation(ann.org_id, invitation.id)
    assert closed is not None and closed.state is InvitationState.ACCEPTED
    assert closed.accepted_user_id == bob.id
    # A member with an invitation to their address pending: the invitations
    # are read, and the organization is not.
    assert calls == {"accepted_invitation": 1}


# Single sign-on.


async def test_the_single_sign_on_link_is_a_team_orgs_for_a_member_manager(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl
) -> None:
    ann = await owner_of_team(manager)
    link = await manager.sso_setup_link(ann, "sso", SETTINGS)
    org = await storage.read_org(ann.org_id)
    assert org is not None and org.provider_org_id is not None
    assert f"organization={org.provider_org_id}" in link and "intent=sso" in link
    with pytest.raises(ValidationFailed):
        await manager.sso_setup_link(ann, "sso", "https://evil.example/settings")
    login = await manager.dev_sign_in(request(), "ann@ajax.example")
    personal = next(m.org for m in login.memberships if m.org.personal)
    in_personal = await enter(manager, login, personal.id)
    with pytest.raises(ValidationFailed):
        await manager.sso_setup_link(in_personal, "sso", SETTINGS)
    await manager.add_member(request(), "ajax", "mem@ajax.example", "Mem", Role.MEMBER)
    mem = await enter(manager, await manager.dev_sign_in(request(), "mem@ajax.example"), ann.org_id)
    with pytest.raises(NotAuthorized):
        await manager.sso_setup_link(mem, "domain_verification", SETTINGS)


async def provider_org(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, ctx: OpContext
) -> Org:
    await manager.sso_setup_link(ctx, "sso", SETTINGS)
    org = await storage.read_org(ctx.org_id)
    assert org is not None and org.provider_org_id is not None
    return org


async def test_a_single_sign_on_in_a_verified_domain_lands_a_member(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    org = await provider_org(manager, storage, ann)
    assert org.provider_org_id is not None
    twin.verify_domain(org.provider_org_id, "Ajax.Example")
    code = twin.issue_code("eve@ajax.example", organization_id=org.provider_org_id, via_sso=True)
    login = await manager.sign_in_with_code(request(), code)
    [place] = [m for m in login.memberships if m.org.id == org.id]
    assert place.role is Role.MEMBER


async def test_a_single_sign_on_outside_a_verified_domain_lands_nothing(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    ann = await owner_of_team(manager)
    org = await provider_org(manager, storage, ann)
    assert org.provider_org_id is not None
    for email, via_sso in (("eve@ajax.example", True), ("mal@other.example", True)):
        if email.endswith("other.example"):
            twin.verify_domain(org.provider_org_id, "ajax.example")
        code = twin.issue_code(email, organization_id=org.provider_org_id, via_sso=via_sso)
        login = await manager.sign_in_with_code(request(), code)
        assert org.id not in {m.org.id for m in login.memberships}
    # Verified, but not through the org's single sign-on: nothing either.
    code = twin.issue_code("sam@ajax.example", organization_id=org.provider_org_id)
    login = await manager.sign_in_with_code(request(), code)
    assert org.id not in {m.org.id for m in login.memberships}


async def test_a_personal_org_never_gets_a_member_through_single_sign_on(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl, twin: IdentityProviderTwinImpl
) -> None:
    login = await manager.dev_sign_in(request(), "ann@ajax.example", "Ann")
    personal = login.memberships[0].org
    ann = await enter(manager, login, personal.id)
    # Invited into the personal org, its organization at the provider exists.
    await manager.invite_member(ann, "friend@ajax.example", Role.MEMBER)
    org = await storage.read_org(personal.id)
    assert org is not None and org.provider_org_id is not None
    twin.verify_domain(org.provider_org_id, "ajax.example")
    code = twin.issue_code("eve@ajax.example", organization_id=org.provider_org_id, via_sso=True)
    eve = await manager.sign_in_with_code(request(), code)
    assert personal.id not in {m.org.id for m in eve.memberships}


async def test_an_organization_that_names_no_living_org_joins_nothing(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    stray = await twin.ensure_organization(external_id="not-a-uuid", name="Stray")
    twin.verify_domain(stray.id, "ajax.example")
    code = twin.issue_code("eve@ajax.example", organization_id=stray.id, via_sso=True)
    login = await manager.sign_in_with_code(request(), code)
    assert [m.org.kind for m in login.memberships] == [OrgKind.PERSONAL]


# A sign-in through an organization asks the provider only what it must.


def counted(twin: IdentityProviderTwinImpl, monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """How many times a sign-in asked the provider for the organization and
    for the invitation the person accepted."""
    calls: Counter[str] = Counter()
    for name in ("get_organization", "accepted_invitation"):
        real = getattr(twin, name)

        async def call(*args: Any, _real: Any = real, _name: str = name, **kwargs: Any) -> Any:
            calls[_name] += 1
            return await _real(*args, **kwargs)

        monkeypatch.setattr(twin, name, call)
    return calls


async def test_a_member_signing_in_through_the_organization_asks_the_provider_nothing(
    manager: TenancyManagerImpl,
    storage: TenancyStorageMemoryImpl,
    twin: IdentityProviderTwinImpl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ann = await owner_of_team(manager)
    invitation = await manager.invite_member(ann, "bob@ajax.example", Role.ADMIN)
    await manager.invite_member(ann, "cy@ajax.example", Role.MEMBER)
    calls = counted(twin, monkeypatch)
    code = twin.accept_invitation(invitation.provider_invitation_id)
    await manager.sign_in_with_code(request(), code)
    # A new invitee: the organization once, the invitations once.
    assert calls == {"get_organization": 1, "accepted_invitation": 1}
    org = await storage.read_org(ann.org_id)
    assert org is not None and org.provider_org_id is not None
    twin.verify_domain(org.provider_org_id, "ajax.example")
    calls.clear()
    for via_sso in (False, True):
        code = twin.issue_code(
            "bob@ajax.example", organization_id=org.provider_org_id, via_sso=via_sso
        )
        login = await manager.sign_in_with_code(request(), code)
        [place] = [m for m in login.memberships if m.org.id == ann.org_id]
        assert place.role is Role.ADMIN
    assert calls == {}


async def test_a_removed_member_signing_in_through_the_organization_is_looked_up(
    manager: TenancyManagerImpl,
    storage: TenancyStorageMemoryImpl,
    twin: IdentityProviderTwinImpl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ann = await owner_of_team(manager)
    org = await provider_org(manager, storage, ann)
    assert org.provider_org_id is not None
    twin.verify_domain(org.provider_org_id, "ajax.example")
    code = twin.issue_code("eve@ajax.example", organization_id=org.provider_org_id, via_sso=True)
    login = await manager.sign_in_with_code(request(), code)
    [place] = [m for m in login.memberships if m.org.id == org.id]
    await manager.remove_member(ann, place.user.id)
    calls = counted(twin, monkeypatch)
    code = twin.issue_code("eve@ajax.example", organization_id=org.provider_org_id, via_sso=True)
    login = await manager.sign_in_with_code(request(), code)
    assert org.id in {m.org.id for m in login.memberships}
    # No invitation is pending in the org, so the provider's are not read.
    assert calls == {"get_organization": 1}


# Signing out ends the provider's session too.


async def test_a_sign_out_answers_the_providers_logout_for_the_session_the_sign_in_left(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    code = twin.issue_code("dee@example.test")
    login = await manager.sign_in_with_code(request(), code)
    org_id = login.memberships[0].org.id
    first = await manager.exchange_login(
        await manager.authenticate_login(request(), login.token), org_id
    )
    # A switch ends the session presented and carries the provider's session on.
    switched = await manager.exchange_login(
        await manager.authenticate_login(request(), first.token), org_id
    )
    ictx = await manager.authenticate_login(request(), switched.token)
    signed_out = await manager.logout(ictx, SIGNED_OUT)
    assert signed_out.session.revoked_at is not None
    url = signed_out.provider_logout_url
    assert url is not None and url.startswith("https://identity.twin.invalid/logout?")
    assert "session_id=twin_session_" in url
    assert "return_to=http%3A%2F%2Flocalhost%3A55173%2Fsigned-out" in url
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), switched.token)


async def test_a_sign_out_with_no_return_leaves_it_to_the_providers_default(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    login = await manager.sign_in_with_code(request(), twin.issue_code("dee@example.test"))
    _, ictx = await signed_in_at(manager, login, login.memberships[0].org.id)
    url = (await manager.logout(ictx)).provider_logout_url
    assert url is not None and "session_id=" in url and "return_to" not in url


async def test_a_sign_out_to_a_return_not_listed_is_refused_and_ends_nothing(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    login = await manager.sign_in_with_code(request(), twin.issue_code("dee@example.test"))
    ctx, ictx = await signed_in_at(manager, login, login.memberships[0].org.id)
    with pytest.raises(ValidationFailed):
        await manager.logout(ictx, "https://elsewhere.example/signed-out")
    assert [s.id for s in await manager.get_sessions(ctx, limit=10)] == [ctx.security.credential_id]


async def test_a_device_or_a_local_sign_in_signs_out_of_acme_alone(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl
) -> None:
    started = await manager.start_device_sign_in(request())
    twin.confirm_device(started.user_code, "dee@example.test")
    device = await manager.finish_device_sign_in(request(), started.device_code)
    _, ictx = await signed_in_at(manager, device, device.memberships[0].org.id)
    assert (await manager.logout(ictx, SIGNED_OUT)).provider_logout_url is None
    local = await manager.dev_sign_in(request(), "eve@example.test")
    _, ictx = await signed_in_at(manager, local, local.memberships[0].org.id)
    assert (await manager.logout(ictx, SIGNED_OUT)).provider_logout_url is None


async def test_a_provider_this_process_cannot_reach_leaves_acme_signed_out(
    manager: TenancyManagerImpl, twin: IdentityProviderTwinImpl, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acme.integrations.exceptions import ProviderUnavailable

    login = await manager.sign_in_with_code(request(), twin.issue_code("dee@example.test"))
    _, ictx = await signed_in_at(manager, login, login.memberships[0].org.id)

    def unreachable(*, session_id: str, return_to: str | None) -> str:
        raise ProviderUnavailable("no identity provider is configured")

    monkeypatch.setattr(twin, "logout_url", unreachable)
    signed_out = await manager.logout(ictx, SIGNED_OUT)
    assert signed_out.session.revoked_at is not None and signed_out.provider_logout_url is None
