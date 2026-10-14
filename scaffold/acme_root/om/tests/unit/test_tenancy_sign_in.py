"""The sign-in delegate over the memory storage, with no identity
provider: the local sign-in and the personal org it makes, the second
factor, the exchange of a sign-in for a session and the switch between
orgs, the places an identity holds, and the sign-out. A sign-in through the
identity provider runs over its twin, in `test_tenancy_identity_provider.py`."""

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl
from acme.om.context import CredentialKind, OperatorRole, Role
from acme.om.exceptions import (
    CredentialExpired,
    InvalidCredential,
    NotAuthorized,
    SignInDelayed,
    UniqueKeyTaken,
)
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import IssuedAuthSession, OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.user import User

from contracts.factories import make_user
from contracts.second_factor import SteppingClock, enrolled_operator, second_factor
from contracts.tenancy import (
    SpyRelay,
    add_member,
    make_manager,
    request,
    seed_operator,
    sign_in,
    team,
    token_operator,
)


async def test_a_run_of_wrong_codes_makes_the_next_one_wait(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    clock: SteppingClock,
) -> None:
    """Counted per email, in the tenancy role's own storage: the wait holds
    whatever address the guesses come from, and even the right code is not
    checked before it has passed."""
    await seed_operator(manager, "root@example.test")
    _, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    for _ in range(3):
        with pytest.raises(InvalidCredential):
            await second_factor(manager, "root@example.test", "000000")
    with pytest.raises(SignInDelayed) as delayed:
        await second_factor(manager, "root@example.test", clock.code(secret))
    assert timedelta(0) < delayed.value.retry_after <= timedelta(seconds=1)
    digest = email_digest("root@example.test")
    run = await storage.read_sign_in_delay(digest)
    assert run is not None and run.failures == 3
    # Once the wait has passed, the right code verifies and ends the run.
    await storage.record_failed_sign_in(digest, utcnow() - timedelta(minutes=1))
    await second_factor(manager, "root@example.test", clock.code(secret))
    assert await storage.read_sign_in_delay(digest) is None


async def test_only_a_sign_in_credential_verifies_a_second_factor(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    clock: SteppingClock,
) -> None:
    await seed_operator(manager, "root@example.test")
    _, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    login = await manager.sign_in.dev_sign_in(request(), "root@example.test")
    ops = next(m.org for m in login.memberships if m.org.slug == "root")
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), ops.id
    )
    by_session = await manager.authenticate_login(request(), session.token)
    with pytest.raises(InvalidCredential):
        await manager.sign_in.verify_second_factor(by_session, clock.code(secret))
    verified = await second_factor(manager, "root@example.test", clock.code(secret))
    identity = await manager.authenticate_login(request(), verified.token)
    assert identity.second_factor and identity.credential_kind is CredentialKind.LOGIN


async def test_exchange_needs_a_membership_in_that_org(manager: TenancyManagerInterface) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, other = await manager.bootstrap(request(), "Beta", "beta", "bob@example.test", "Bob")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    with pytest.raises(NotAuthorized):
        await manager.sign_in.exchange_login(
            await manager.authenticate_login(request(), login.token), other.id
        )


async def test_exchange_refuses_a_gone_org_or_membership_as_not_authorized(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    operator: TenancyOperatorManagerImpl,
) -> None:
    # The sign-in is good; the tenant is not one the identity can enter. A 401
    # would make both clients discard a valid login, so it is a 403, as the
    # interface says.
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    bob = await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    ended = await storage.read_membership_for_user(org.id, bob.id)
    assert ended is not None
    await storage.write_membership(
        org.id, ended.model_copy(update={"deleted_at": utcnow(), "deleted_by": org.created_by})
    )
    login = await manager.sign_in.dev_sign_in(request(), "bob@example.test")
    with pytest.raises(NotAuthorized):
        await manager.sign_in.exchange_login(
            await manager.authenticate_login(request(), login.token), org.id
        )

    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    admin = await token_operator(manager, "root@example.test")
    await operator.delete_org(admin, org.id)
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    identity = await manager.authenticate_login(request(), login.token)
    with pytest.raises(NotAuthorized):
        await manager.sign_in.exchange_login(identity, org.id)
    # The login itself still stands.
    assert (
        await manager.authenticate_login(request(), login.token)
    ).identity_id == identity.identity_id


async def test_the_second_factor_ends_the_sign_in_it_verified(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl, clock: SteppingClock
) -> None:
    """A person holds one sign-in at a time: a wrong code ends nothing, and
    the right one ends the sign-in it was verified on."""
    await seed_operator(manager, "root@example.test")
    _, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    plain = await manager.sign_in.dev_sign_in(request(), "root@example.test")
    with pytest.raises(InvalidCredential):
        await manager.sign_in.verify_second_factor(
            await manager.authenticate_login(request(), plain.token), "000000"
        )
    verified = await manager.sign_in.verify_second_factor(
        await manager.authenticate_login(request(), plain.token), clock.code(secret)
    )
    assert (await manager.authenticate_login(request(), verified.token)).second_factor
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), plain.token)


async def test_a_sign_out_ends_the_credential_presented_whichever_it_is(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl, clock: SteppingClock
) -> None:
    """A sign-in with no code, a sign-in with its code, and an operator token
    each end on their own sign-out; the next use of each is refused."""
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    picker = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    out = await manager.sign_in.logout(await manager.authenticate_login(request(), picker.token))
    assert out.auth_session.credential_kind is CredentialKind.LOGIN and out.auth_session.revoked_at
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), picker.token)

    await seed_operator(manager, "root@example.test")
    admin, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    verified = await second_factor(manager, "root@example.test", clock.code(secret))
    ictx = await manager.authenticate_login(request(), verified.token)
    await manager.sign_in.logout(ictx)
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), verified.token)
    with pytest.raises(CredentialExpired):
        await operator.issue_operator_token(await manager.admit_operator(ictx), OperatorRole.READ)

    token = await manager.grant_operator_token(request(), "root@example.test")
    signed_out = await manager.sign_in.logout(
        await manager.authenticate_login(request(), token.token)
    )
    assert signed_out.auth_session.id == token.id and signed_out.provider_logout_url is None
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), token.token)
    assert token.id not in {
        t.id for t in (await operator.get_operator_tokens(admin, None, limit=10)).items
    }


# One personal org per person, whoever makes it.


class RacedPersonalStorage(TenancyStorageMemoryImpl):
    """Every read of an identity's places misses: a person the sign-in finds
    with no personal org, while another sign-in is making it."""

    async def read_users_by_identity(
        self, identity_id: UUID, limit: int
    ) -> list[tuple[UUID, User]]:
        return []


async def test_a_person_has_one_personal_org_whatever_races_to_make_it(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    storage = RacedPersonalStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    await manager.sign_in.dev_sign_in(request(), "dee@example.test", "Dee")
    [personal_org] = [o for o in await storage.read_orgs(10) if o.personal]
    # The sign-in reads no personal org and tries to make one; the unique key
    # keeps the one the first sign-in made, and the sign-in still answers.
    await manager.sign_in.dev_sign_in(request(), "dee@example.test")
    orgs = await storage.read_orgs(10)
    assert [o.id for o in orgs if o.personal] == [personal_org.id]
    identity = await storage.read_identity_by_email_digest(email_digest("dee@example.test"))
    assert identity is not None
    other = personal_org.model_copy(update={"id": new_id(), "slug": "dee-again"})
    with pytest.raises(UniqueKeyTaken):
        await storage.write_org(other.id, other)


async def test_a_person_with_no_personal_org_gets_one_at_sign_in(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    # An identity and a team org, and no personal org.
    org, user = await tenant_with_no_personal_org(storage, "gus@example.test", "Gus", "gus-co")
    login = await manager.sign_in.dev_sign_in(request(), "gus@example.test")
    kinds = sorted((m.org.kind, m.org.name) for m in login.memberships)
    assert kinds == [(OrgKind.PERSONAL, "Gus"), (OrgKind.TEAM, org.name)]
    # Once: the next sign-in finds it.
    again = await manager.sign_in.dev_sign_in(request(), "gus@example.test")
    assert {m.org.id for m in again.memberships} == {m.org.id for m in login.memberships}
    assert user.identity_id == next(
        m.org.personal_identity_id for m in again.memberships if m.org.personal
    )


async def tenant_with_no_personal_org(
    storage: TenancyStorageMemoryImpl, email: str, name: str, slug: str
) -> tuple[Org, User]:
    now = utcnow()
    identity = Identity(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=EMPTY_UUID,
        updated_by=EMPTY_UUID,
        email=email,
    )
    user = make_user(identity.id, email).model_copy(update={"display_name": name})
    org = Org(
        id=new_id(),
        name=f"{name} Co",
        slug=slug,
        created_at=now,
        updated_at=now,
        created_by=user.id,
        updated_by=user.id,
    )
    membership = Membership(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=user.id,
        updated_by=user.id,
        user_id=user.id,
        role=Role.OWNER,
    )
    await storage.create_org_with_owner(org.id, org, user, membership, identity)
    return org, user


async def test_the_memberships_of_an_identity_page_and_skip_the_gone(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
) -> None:
    orgs = []
    for name in ("Ajax", "Beta", "Gamma"):
        slug = name.lower()
        if not orgs:
            _, org = await manager.bootstrap(request(), name, slug, "ann@example.test", "Ann")
        else:
            _, org = await manager.bootstrap(request(), name, slug, f"owner@{slug}.test", "Owner")
            await manager.add_member(request(), slug, "ann@example.test", "Ann", Role.MEMBER)
        orgs.append(org)
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    every = await manager.sign_in.get_identity_memberships(ictx, None, limit=10)
    # Three team orgs, and Ann's personal org.
    personal = {m.org.id for m in every.items if m.org.personal}
    assert len(personal) == 1 and not every.has_more
    assert {m.org.id for m in every.items} - personal == {org.id for org in orgs}
    assert [m.user.id for m in every.items] == sorted(m.user.id for m in every.items)
    first = await manager.sign_in.get_identity_memberships(ictx, None, limit=3)
    assert first.items == every.items[:3] and first.has_more
    rest = await manager.sign_in.get_identity_memberships(ictx, first.items[-1].user.id, limit=3)
    assert rest.items == every.items[3:] and not rest.has_more
    # A deleted org is not a place anyone holds.
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    admin = await token_operator(manager, "root@example.test")
    await operator.delete_org(admin, orgs[1].id)
    left = await manager.sign_in.get_identity_memberships(ictx, None, limit=10)
    assert {m.org.id for m in left.items} == {orgs[0].id, orgs[2].id} | personal


async def test_a_switch_ends_the_session_it_was_presented_with_in_the_same_write(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    relay = SpyRelay(OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(request(), "Beta", "beta", "bea@example.test", "Bea")
    await manager.add_member(request(), "beta", "ann@example.test", "Ann", Role.MEMBER)
    beta = await storage.read_org_by_slug("beta")
    assert beta is not None
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    tab = await manager.sign_in.exchange_login(ictx, ajax.id)
    other_login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    other_tab = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), other_login.token), ajax.id
    )

    # The tab presents its session to the exchange with the other org's id.
    switched = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), tab.token), beta.id
    )
    assert switched.org.id == beta.id and switched.role is Role.MEMBER
    assert (await manager.authenticate(request(), switched.token)).org_id == beta.id
    # The session it was presented with is over, announced under its own tenant.
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), tab.token)
    revoked = [(org_id, r) for org_id, r in relay.rows if r.kind == "tenancy.auth_session.revoked"]
    assert len(revoked) == 1
    org_id, row = revoked[0]
    assert org_id == ajax.id and row.org_id == ajax.id
    assert row.target_id == ictx.credential_id or row.payload.keys() == {"user_id"}, "ids only"
    # Another tab's session is its own and is not touched.
    assert (await manager.authenticate(request(), other_tab.token)).org_id == ajax.id
    # A switch within the same org makes a new session too, and ends the old one.
    same = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), switched.token), beta.id
    )
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), switched.token)
    assert (await manager.authenticate(request(), same.token)).org_id == beta.id
    # A switch into an org the person does not hold is refused, and ends nothing.
    _, gamma = await manager.bootstrap(request(), "Gamma", "gamma", "gus@example.test", "Gus")
    with pytest.raises(NotAuthorized):
        await manager.sign_in.exchange_login(
            await manager.authenticate_login(request(), same.token), gamma.id
        )
    assert (await manager.authenticate(request(), same.token)).org_id == beta.id


async def test_two_switches_on_one_session_admit_one(manager: TenancyManagerInterface) -> None:
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    tab = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), ajax.id
    )
    # Both verified before either writes: the storage decides.
    ictx_a = await manager.authenticate_login(request(), tab.token)
    ictx_b = await manager.authenticate_login(request(), tab.token)
    outcomes = await asyncio.gather(
        manager.sign_in.exchange_login(ictx_a, ajax.id),
        manager.sign_in.exchange_login(ictx_b, ajax.id),
        return_exceptions=True,
    )
    won = [o for o in outcomes if not isinstance(o, BaseException)]
    lost = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(won) == 1 and len(lost) == 1
    assert isinstance(lost[0], CredentialExpired)
    assert (await manager.authenticate(request(), won[0].token)).org_id == ajax.id


async def test_a_session_made_from_a_session_keeps_its_deadline(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    """Only the exchange of a sign-in starts an absolute lifetime. A switch,
    into another org or within the same one, and the landing after an org's
    deletion each make a session from a session, and it ends when the first
    one would have: no session outlives its sign-in's lifetime."""
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(request(), "Beta", "beta", "bea@example.test", "Bea")
    await manager.add_member(request(), "beta", "ann@example.test", "Ann", Role.MEMBER)
    beta = await storage.read_org_by_slug("beta")
    assert beta is not None

    async def exchange(token: str, org_id: UUID) -> IssuedAuthSession:
        return await manager.sign_in.exchange_login(
            await manager.authenticate_login(request(), token), org_id
        )

    async def deadline(issued: IssuedAuthSession) -> datetime:
        """The deadline on the row every request reads, which is the one issued."""
        ctx = await manager.authenticate(request(), issued.token)
        row = await storage.read_auth_session(ctx.org_id, ctx.security.credential_id)
        assert row is not None and row.expires_at == issued.expires_at
        return row.expires_at

    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    first = await exchange(login.token, ajax.id)
    signed_in_until = await deadline(first)

    elsewhere = await exchange(first.token, beta.id)
    assert await deadline(elsewhere) == signed_in_until, "a switch into another org"
    same = await exchange(elsewhere.token, beta.id)
    assert await deadline(same) == signed_in_until, "a switch within the same org"
    back = await exchange(same.token, ajax.id)
    owner = await manager.authenticate(request(), back.token)
    home = (await manager.org.delete_org(owner, "Ajax")).auth_session
    assert home is not None and home.org.personal
    assert await deadline(home) == signed_in_until, "the landing after an org's deletion"

    # A new sign-in is what starts a new lifetime.
    again = await exchange(
        (await manager.sign_in.dev_sign_in(request(), "ann@example.test")).token, beta.id
    )
    assert await deadline(again) > signed_in_until
    # A lifetime set shorter since the sign-in binds the next session too: the
    # earlier of the two deadlines, never the later.
    a_day = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, auth_session_ttl=timedelta(days=1))
    )
    capped = await a_day.sign_in.exchange_login(
        await a_day.authenticate_login(request(), again.token), beta.id
    )
    assert capped.expires_at <= utcnow() + timedelta(days=1) < signed_in_until


async def test_a_sign_in_is_exchanged_once(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, gamma = await manager.bootstrap(request(), "Gamma", "gamma", "gus@example.test", "Gus")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    # A refused exchange ends nothing: the sign-in still stands.
    with pytest.raises(NotAuthorized):
        await manager.sign_in.exchange_login(ictx, gamma.id)
    issued = await manager.sign_in.exchange_login(ictx, ajax.id)
    ctx = await manager.authenticate(request(), issued.token)
    # The sign-in ended in the write that made the session.
    ended = await storage.read_auth_session_by_id(ictx.credential_id)
    assert ended is not None and ended[0] == EMPTY_UUID
    assert ended[1].revoked_at == ended[1].updated_at is not None
    # A second exchange, a replay or a retry after a lost answer, lands nothing.
    with pytest.raises(CredentialExpired, match="sign in again"):
        await manager.sign_in.exchange_login(ictx, ajax.id)
    # Presented again, the sign-in proves nothing any more, the list of
    # places included; the session is what the person holds.
    with pytest.raises(CredentialExpired, match="sign in again"):
        await manager.authenticate_login(request(), login.token)
    assert [s.id for s in await manager.credentials.get_auth_sessions(ctx, limit=10)] == [
        ctx.security.credential_id
    ]
    # A new sign-in makes a new session.
    again = await sign_in(manager, "ann@example.test", ajax.id)
    assert again.security.credential_id != ctx.security.credential_id


async def test_two_exchanges_of_one_sign_in_admit_one(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """Three clicks on the picker at once, or a replayed request: the sign-in
    ends in the write that lands the one session, so the others find it
    ended."""
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    # All verified before any writes: the storage decides.
    proofs = [await manager.authenticate_login(request(), login.token) for _ in range(3)]
    outcomes = await asyncio.gather(
        *(manager.sign_in.exchange_login(ictx, ajax.id) for ictx in proofs), return_exceptions=True
    )
    won = [o for o in outcomes if not isinstance(o, BaseException)]
    lost = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(won) == 1 and len(lost) == 2
    assert all(isinstance(error, CredentialExpired) for error in lost)
    ctx = await manager.authenticate(request(), won[0].token)
    assert [s.id for s in await manager.credentials.get_auth_sessions(ctx, limit=10)] == [
        ctx.security.credential_id
    ]


# A sign-in reads a person's places in one read, however many they hold.


class CountedReads(TenancyStorageMemoryImpl):
    """Counts the reads a sign-in makes of a person's places: the calls the
    manager makes, not the ones this memory impl makes inside its own."""

    def __init__(self, outbox: OutboxStorageMemoryImpl) -> None:
        super().__init__(outbox)
        self.reads: Counter[str] = Counter()
        self._inside = False

    async def read_org(self, org_id: UUID) -> Org | None:
        self.reads["read_org"] += 0 if self._inside else 1
        return await super().read_org(org_id)

    async def read_membership_for_user(self, org_id: UUID, user_id: UUID) -> Membership | None:
        self.reads["read_membership_for_user"] += 0 if self._inside else 1
        return await super().read_membership_for_user(org_id, user_id)

    async def read_memberships_by_identity(
        self, identity_id: UUID, limit: int, after_user_id: UUID | None = None
    ) -> list[OrgMembership]:
        self.reads["read_memberships_by_identity"] += 1
        self._inside = True
        try:
            return await super().read_memberships_by_identity(identity_id, limit, after_user_id)
        finally:
            self._inside = False


async def test_a_sign_in_reads_the_places_once_and_lists_the_living_ones(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The places a sign-in lists: every org the person is a live member of,
    by user id, and not an org deleted or a membership ended. They are read
    in one read, so a person in five orgs costs what a person in one does."""
    storage = CountedReads(outbox)
    manager = make_manager(storage, infra, TenancyOptions(dev_sign_in=True), outbox=outbox)
    orgs = [
        (await manager.bootstrap(request(), f"Org {i}", f"org-{i}", "ann@example.test", "Ann"))[1]
        for i in range(5)
    ]
    await manager.bootstrap(request(), "Solo", "solo", "bob@example.test", "Bob")

    storage.reads.clear()
    one = await manager.sign_in.dev_sign_in(request(), "bob@example.test")
    reads_for_one = storage.reads.copy()
    storage.reads.clear()
    five = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    assert storage.reads == reads_for_one, "the reads do not grow with the orgs"
    assert storage.reads["read_memberships_by_identity"] == 1
    assert len(team(one.memberships)) == 1
    assert {m.org.id for m in team(five.memberships)} == {org.id for org in orgs}
    assert [m.user.id for m in five.memberships] == sorted(m.user.id for m in five.memberships)

    # One org deleted, one membership ended: neither is a place any more.
    gone = orgs[1].model_copy(update={"deleted_at": utcnow()})
    await storage.write_org(gone.id, gone)
    left = next(m for m in five.memberships if m.org.id == orgs[3].id)
    ended = await storage.read_membership_for_user(orgs[3].id, left.user.id)
    assert ended is not None
    await storage.write_membership(orgs[3].id, ended.model_copy(update={"deleted_at": utcnow()}))
    again = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    assert {m.org.id for m in team(again.memberships)} == {orgs[0].id, orgs[2].id, orgs[4].id}
    kept = {m.org.id: m for m in five.memberships}
    for place in again.memberships:
        assert place.user == kept[place.org.id].user and place.role == kept[place.org.id].role
