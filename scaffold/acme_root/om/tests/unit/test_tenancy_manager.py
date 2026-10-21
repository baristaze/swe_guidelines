"""The tenancy manager's own operations over the memory storage: the
seeding (`bootstrap`, `add_member`, and the creates behind them), the
transitions to each stage, the operator gate and the grant job, the socket
tickets, and the sweep. Each delegate's operations are in a module of its
own, `test_tenancy_<delegate>.py`, and the fixtures they share in
`conftest.py`."""

import asyncio
from datetime import timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID

import pytest

from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    OperatorContext,
    OperatorPermission,
    OperatorRole,
    Permission,
    RequestContext,
    Role,
)
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    InvalidCredential,
    MembershipLimitReached,
    NotAnOperator,
    NotAuthorized,
    NotFound,
    PersonalOrgFixed,
    SecondFactorRequired,
    Unavailable,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.relay import OutboxRelayInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.creates import MAX_ORGS_PER_IDENTITY, Admission, add_member_to
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.operator import (
    TOTP_ISSUER,
    TenancyOperatorManagerImpl,
    TenancyOperatorOptions,
)
from acme.om.tenancy.rules import email_digest, hash_token
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.om.tenancy.types.user import User

from contracts.factories import make_membership, make_user
from contracts.outbox_storage import claim_all
from contracts.second_factor import (
    SteppingClock,
    enrolled_operator,
    minting_operator,
    second_factor,
    secret_of,
)
from contracts.signing_in import provider_login, tenancy_signing_in
from contracts.tenancy import (
    SpyRelay,
    add_member,
    make_manager,
    request,
    seed_operator,
    sign_in,
    signed_up,
    team,
    token_operator,
)


class DownCache(CacheInterface):
    """A backend that cannot be reached: every read is a miss, every write is lost."""

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        return None

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        return None

    async def invalidate(self, org_id: UUID, key: str) -> None:
        return None

    async def increment(self, org_id: UUID, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        return 0, ttl

    def describe(self) -> str:
        return "cache=down"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


async def operator_deletes(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    admin: OperatorContext,
    org_id: UUID,
) -> Org:
    """An operator's deletion carried to its end: the close the request
    writes, then the last step of the `DELETE_ORG` it asked for, which the
    worker runs under the operator's name once the providers are done."""
    await operator.delete_org(admin, org_id)
    work = await manager.service_context(request(), org_id, admin.identity_id)
    deleted = await manager.org.delete_closed_org(work)
    assert deleted is not None
    return deleted


async def test_bootstrap_produces_the_owners_context(manager: TenancyManagerInterface) -> None:
    seed = request(AppContext(type=AppType.CLI, version="cli@test"))
    ctx, org = await manager.bootstrap(seed, "Ajax", "ajax", "ann@example.test", "Ann")
    assert ctx.org_id == org.id
    assert ctx.security.role is Role.OWNER
    assert ctx.security.credential_kind is CredentialKind.INTERNAL
    # The owner's context refines the request stage the seeding minted.
    assert ctx.app.type is AppType.CLI and ctx.request_id == seed.request_id
    assert (await manager.org.get_org(ctx)) == org
    assert [u.id for u in (await manager.members.get_users(ctx, None, limit=10)).items] == [
        ctx.user_id
    ]
    issued = await manager.credentials.create_api_key(ctx, "seed", Role.MEMBER)
    assert issued.api_key.created_by == ctx.user_id


async def test_bootstrap_login_exchange_authenticate(manager: TenancyManagerInterface) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    # The owner nobody had seen before came with a personal org of her own.
    assert [m.org.id for m in team(login.memberships)] == [org.id]
    assert [m.role for m in login.memberships] == [Role.OWNER, Role.OWNER]
    assert [m.org.kind for m in login.memberships if m.org.id != org.id] == [OrgKind.PERSONAL]

    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    ctx = await manager.authenticate(request(), issued.token)
    assert ctx.org_id == org.id
    assert ctx.user_id == issued.user.id
    assert ctx.security.role is Role.OWNER
    assert ctx.security.credential_kind is CredentialKind.AUTH_SESSION_TOKEN
    assert ctx.has(Permission.MANAGE_MEMBERS)
    assert (await manager.org.get_org(ctx)) == org
    assert [u.id for u in (await manager.members.get_users(ctx, None, limit=10)).items] == [
        issued.user.id
    ]


async def test_bootstrap_refuses_a_taken_slug(manager: TenancyManagerInterface) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    with pytest.raises(Conflict):
        await manager.bootstrap(request(), "Ajax 2", "ajax", "bob@example.test", "Bob")


async def test_a_deleted_org_frees_its_slug(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    admin = await token_operator(manager, "root@example.test")
    await operator_deletes(manager, operator, admin, org.id)
    _, again = await manager.bootstrap(request(), "Ajax", "ajax", "bob@example.test", "Bob")
    assert again.id != org.id and again.slug == "ajax"
    with pytest.raises(Conflict):
        await manager.bootstrap(request(), "Ajax 3", "ajax", "cat@example.test", "Cat")


async def test_a_login_credential_cannot_call_tenant_routes(
    manager: TenancyManagerInterface,
) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), login.token)
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), "garbage")


async def test_expired_sessions_are_refused(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(storage, infra, TenancyOptions(auth_session_ttl=timedelta(seconds=-1)))
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), issued.token)


async def test_a_session_idle_past_its_idle_lifetime_has_ended(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """A session ends at whichever passes first, its absolute lifetime or its
    idle one. Each use records itself, at most once a minute, and a session
    no request has touched yet starts its idle clock at its first use."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    ctx = await manager.authenticate(request(), issued.token)
    seen = await storage.read_auth_session(org.id, ctx.credential_id)
    assert seen is not None and seen.last_seen_at is not None
    await manager.authenticate(request(), issued.token)
    again = await storage.read_auth_session(org.id, ctx.credential_id)
    assert again is not None and again.last_seen_at == seen.last_seen_at, "once a minute"
    # Just past the idle lifetime without a request, well inside the absolute one.
    options = TenancyOptions()
    assert options.auth_session_idle_ttl < options.auth_session_ttl
    since = utcnow() - options.auth_session_idle_ttl - timedelta(seconds=1)
    idle = seen.model_copy(update={"last_seen_at": since})
    await storage.write_auth_session(org.id, idle)
    with pytest.raises(CredentialExpired, match="idle"):
        await manager.authenticate(request(), issued.token)
    with pytest.raises(CredentialExpired, match="idle"):
        await manager.authenticate_login(request(), issued.token)


async def admitted_on_login(
    manager: TenancyManagerInterface, email: str, code: str | None = None
) -> OperatorContext:
    if code is None:
        login = await provider_login(manager, email)
    else:
        login = await second_factor(manager, email, code)
    return await manager.admit_operator(await manager.authenticate_login(request(), login.token))


async def test_operator_gate_admits_only_operators_signing_in(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl, clock: SteppingClock
) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await seed_operator(manager, "root@example.test")
    with pytest.raises(NotAnOperator):
        await admitted_on_login(manager, "ann@example.test")

    admin, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    assert admin.email == "root@example.test"
    assert admin.credential_kind is CredentialKind.OPERATOR_TOKEN
    # Two team orgs, and the personal org of each of their two owners.
    every = await operator.get_orgs(admin, None, limit=10)
    assert len(every.items) == 4 and not every.has_more
    first = await operator.get_orgs(admin, None, limit=3)
    assert len(first.items) == 3 and first.has_more
    rest = await operator.get_orgs(admin, first.items[-1].id, limit=3)
    assert rest.items == every.items[3:] and not rest.has_more

    # A tenant session proves the identity, but the operator plane never takes
    # a tenant's credential, even from a sign-in that verified a code.
    login = await second_factor(manager, "root@example.test", clock.code(secret))
    identity = await manager.authenticate_login(request(), login.token)
    ops_org = next(m.org for m in login.memberships if m.org.slug == "root")
    session = await manager.sign_in.exchange_login(identity, ops_org.id)
    by_session = await manager.authenticate_login(request(), session.token)
    with pytest.raises(InvalidCredential):
        await manager.admit_operator(by_session)


async def test_an_operator_enrols_a_second_factor_before_the_plane_admits_them(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    clock: SteppingClock,
) -> None:
    await seed_operator(manager, "root@example.test")
    enrolling = await admitted_on_login(manager, "root@example.test")
    # Allowlisted with no second factor: the two enrolment calls and nothing else.
    assert enrolling.permissions == {OperatorPermission.ENROL}
    with pytest.raises(NotAuthorized):
        await operator.get_orgs(enrolling, None, limit=10)
    with pytest.raises(NotAuthorized):
        await operator.issue_operator_token(enrolling, OperatorRole.READ)
    first = secret_of((await operator.enrol_totp(enrolling)).otpauth_uri)
    # Minting again replaces a secret nobody confirmed.
    issued = await operator.enrol_totp(enrolling)
    issuer = quote(TOTP_ISSUER)
    assert issued.otpauth_uri.startswith(f"otpauth://totp/{issuer}%3Aroot%40example.test?secret=")
    secret = secret_of(issued.otpauth_uri)
    assert secret != first
    stored = await storage.read_identity(enrolling.identity_id)
    assert stored is not None and stored.totp_secret is not None
    assert secret.hex() not in stored.totp_secret, "sealed, never in the clear"
    with pytest.raises(ValidationFailed):
        await operator.confirm_totp(enrolling, clock.code(first))
    await operator.confirm_totp(enrolling, clock.code(secret))
    with pytest.raises(Conflict):
        await operator.enrol_totp(enrolling)

    # Enrolled: a sign-in alone is refused, a wrong code and a reused one too.
    with pytest.raises(SecondFactorRequired):
        await admitted_on_login(manager, "root@example.test")
    with pytest.raises(InvalidCredential):
        await second_factor(manager, "root@example.test", "000000")
    code = clock.code(secret)
    admin = await admitted_on_login(manager, "root@example.test", code)
    # With its code, a sign-in mints one token and does nothing else.
    assert admin.permissions == {OperatorPermission.MINT}
    assert admin.operator_entry is OperatorRole.WRITE
    with pytest.raises(NotAuthorized):
        await operator.get_orgs(admin, None, limit=10)
    with pytest.raises(InvalidCredential):
        await second_factor(manager, "root@example.test", code)
    # A tenant's sign-in needs no code, and one who has no factor may not send one.
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await provider_login(manager, "ann@example.test")
    with pytest.raises(ValidationFailed):
        await second_factor(manager, "ann@example.test", "123456")


async def test_a_process_without_the_totp_key_refuses_to_enrol(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    manager = make_manager(storage, infra, TenancyOptions(), outbox=outbox)
    operator = TenancyOperatorManagerImpl(
        storage,
        ChangeEventStorageMemoryImpl(),
        OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics()),
        TenancyOperatorOptions(),
    )
    await seed_operator(manager, "root@example.test")
    with pytest.raises(Unavailable):
        await operator.enrol_totp(await admitted_on_login(manager, "root@example.test"))


async def test_an_operator_token_carries_one_permission_and_reaches_the_plane_only(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    clock: SteppingClock,
) -> None:
    await seed_operator(manager, "root@example.test")
    _, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    admin = await minting_operator(manager, clock, "root@example.test", secret)
    issued = await operator.issue_operator_token(admin, OperatorRole.READ)
    assert issued.token.startswith("opr_")
    assert timedelta(minutes=59) < issued.expires_at - utcnow() <= timedelta(hours=1)
    found = await storage.read_auth_session_by_digest(hash_token(issued.token))
    assert found is not None and found[0] == EMPTY_UUID, "a row of the system scope"
    assert found[1].credential_kind is CredentialKind.OPERATOR_TOKEN
    assert found[1].token_hash != issued.token, "stored as its digest"
    ictx = await manager.authenticate_login(request(), issued.token)
    reader = await manager.admit_operator(ictx)
    assert reader.permissions == {OperatorPermission.READ}
    await operator.get_orgs(reader, None, limit=10)
    with pytest.raises(NotAuthorized):
        await operator.delete_org(reader, new_id())
    # A token never mints a token, and reaches no tenant.
    with pytest.raises(NotAuthorized):
        await operator.issue_operator_token(reader, OperatorRole.READ)
    with pytest.raises(InvalidCredential):
        await manager.sign_in.get_identity_memberships(ictx, None, limit=10)
    with pytest.raises(InvalidCredential):
        await manager.sign_in.exchange_login(ictx, new_id())
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), issued.token)
    # An hour at most, never wider than the entry; a refused mint ends nothing.
    admin = await minting_operator(manager, clock, "root@example.test", secret)
    with pytest.raises(ValidationFailed):
        await operator.issue_operator_token(admin, OperatorRole.READ, timedelta(seconds=3601))
    await seed_operator(manager, "sup@example.test", OperatorRole.READ)
    _, sup_secret = await enrolled_operator(manager, operator, clock, "sup@example.test")
    sup = await minting_operator(manager, clock, "sup@example.test", sup_secret)
    with pytest.raises(NotAuthorized):
        await operator.issue_operator_token(sup, OperatorRole.WRITE)
    # A token past its expiry is refused; one whose entry narrowed is narrowed.
    writer = await operator.issue_operator_token(admin, OperatorRole.WRITE)
    await manager.grant_operator(request(), "root@example.test", OperatorRole.READ)
    narrowed = await manager.admit_operator(
        await manager.authenticate_login(request(), writer.token)
    )
    assert narrowed.permissions == {OperatorPermission.READ}
    # Off the plane, the token has ended: a grant made again revives it not.
    await manager.disable_operator(request(), "root@example.test")
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), writer.token)
    await manager.grant_operator(request(), "root@example.test", OperatorRole.WRITE)
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), writer.token)
    short = await manager.grant_operator_token(request(), "sup@example.test", timedelta(seconds=1))
    row = await storage.read_auth_session_by_digest(hash_token(short.token))
    assert row is not None
    await storage.write_auth_session(
        EMPTY_UUID, row[1].model_copy(update={"expires_at": utcnow() - timedelta(seconds=1)})
    )
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), short.token)


async def test_a_sign_in_with_its_code_mints_one_token_and_ends(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    clock: SteppingClock,
) -> None:
    """The sign-in is exchanged for a token, as a tenant's is for a session:
    it ends in the write that lands the token, so a leaked one mints nothing
    more, and every read and write on the plane is a token's."""
    await seed_operator(manager, "root@example.test")
    admin, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    login = await second_factor(manager, "root@example.test", clock.code(secret))
    minting = await manager.admit_operator(await manager.authenticate_login(request(), login.token))
    with pytest.raises(NotAuthorized):
        await operator.size(minting)
    issued = await operator.issue_operator_token(minting, OperatorRole.READ)
    row = await storage.read_auth_session_by_id(issued.id)
    assert row is not None and row[1].token_hash == hash_token(issued.token)
    assert row[1].created_by == minting.identity_id
    ended = await storage.read_auth_session_by_id(minting.credential_id)
    assert ended is not None and ended[1].revoked_at is not None
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), login.token)
    with pytest.raises(CredentialExpired):
        await operator.issue_operator_token(minting, OperatorRole.READ)
    # One sign-in, one token: the enrolment's sign-in minted the other one.
    listed = await operator.get_operator_tokens(admin, None, limit=10)
    assert [t.id for t in listed.items] == [issued.id, admin.credential_id]


async def test_an_operator_lists_and_revokes_their_own_tokens_one_at_a_time(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    clock: SteppingClock,
) -> None:
    """A revoke is a `revoked_at` the operator is named on, refused on the
    token's next request, idempotent, and the operator's own: another
    operator's token is none of theirs, and the grant job's machine tokens
    are listed by no person."""
    await seed_operator(manager, "root@example.test")
    admin, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    minted = [
        await operator.issue_operator_token(
            await minting_operator(manager, clock, "root@example.test", secret), OperatorRole.READ
        )
        for _ in range(2)
    ]
    await manager.grant_operator(request(), "provisioner@platform.acme.invalid", OperatorRole.WRITE)
    machine = await manager.grant_operator_token(request(), "provisioner@platform.acme.invalid")
    every = await operator.get_operator_tokens(admin, None, limit=10)
    assert [t.id for t in every.items] == [minted[1].id, minted[0].id, admin.credential_id]
    assert not every.has_more and machine.id not in {t.id for t in every.items}
    first = await operator.get_operator_tokens(admin, None, limit=2)
    assert first.has_more
    rest = await operator.get_operator_tokens(admin, first.items[-1].id, limit=2)
    assert first.items + rest.items == every.items and not rest.has_more

    revoked = await operator.revoke_operator_token(admin, minted[0].id)
    assert revoked.revoked_at is not None and revoked.updated_by == admin.identity_id
    with pytest.raises(CredentialExpired) as refused:
        await manager.authenticate_login(request(), minted[0].token)
    assert refused.value.message == "operator token revoked"
    await manager.authenticate_login(request(), minted[1].token)  # the others stand
    again = await operator.revoke_operator_token(admin, minted[0].id)
    assert again == revoked, "a second revoke answers the first one's row"
    listed = await operator.get_operator_tokens(admin, None, limit=10)
    assert minted[0].id not in {t.id for t in listed.items}
    with pytest.raises(NotFound):
        await operator.revoke_operator_token(admin, new_id())
    with pytest.raises(NotFound):
        await operator.revoke_operator_token(admin, machine.id)
    with pytest.raises(NotFound):
        await operator.revoke_operator_token(admin, admin.identity_id)

    # Another operator reaches none of them, whatever their entry.
    await seed_operator(manager, "sup@example.test")
    sup, _ = await enrolled_operator(manager, operator, clock, "sup@example.test")
    with pytest.raises(NotFound):
        await operator.revoke_operator_token(sup, minted[1].id)
    await manager.authenticate_login(request(), minted[1].token)
    assert minted[1].id not in {
        t.id for t in (await operator.get_operator_tokens(sup, None, limit=10)).items
    }
    # A token ends itself as well as its siblings.
    ended = await operator.revoke_operator_token(admin, admin.credential_id)
    assert ended.revoked_at is not None


async def test_disabling_an_operator_ends_its_credentials_and_not_its_tenant_ones(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl, clock: SteppingClock
) -> None:
    """Off the plane at once, for good: a grant made again revives no token
    and no sign-in with a code. The person's tenant session is theirs, and
    stands."""
    _, org = await manager.bootstrap(
        request(), "Root", "root", "root@example.test", "Root", operator_role=OperatorRole.WRITE
    )
    tenant = await sign_in(manager, "root@example.test", org.id)
    admin, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    token = await manager.grant_operator_token(request(), "root@example.test")
    verified = await second_factor(manager, "root@example.test", clock.code(secret))
    await manager.disable_operator(request(), "root@example.test")
    await manager.grant_operator(request(), "root@example.test", OperatorRole.WRITE)
    for credential in (token.token, verified.token):
        with pytest.raises(CredentialExpired):
            await manager.authenticate_login(request(), credential)
    listed = await operator.get_operator_tokens(admin, None, limit=10)
    assert token.id not in {t.id for t in listed.items}
    assert (await manager.org.get_me(tenant)).user.id == tenant.user_id


def counted_reads(storage: TenancyStorageMemoryImpl, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every read the manager asks of the storage from here on, by name: each
    is one statement in one transaction on Postgres. A read the memory impl
    makes of itself inside one is not the manager's, and is not counted."""
    reads: list[str] = []
    depth = [0]
    for name in dir(storage):
        if not name.startswith("read_"):
            continue
        method = getattr(storage, name)

        async def counted(*args: object, _name: str = name, _method: Any = method) -> object:
            if depth[0] == 0:
                reads.append(_name)
            depth[0] += 1
            try:
                return await _method(*args)
            finally:
                depth[0] -= 1

        monkeypatch.setattr(storage, name, counted)
    return reads


async def test_each_credential_is_checked_in_the_fewest_reads(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    clock: SteppingClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session reads its digest and its principal. An api key reads its
    digest and its principal too, and so does the recheck of the socket it
    opened. The operator gate reads the credential with its identity, once,
    and nothing more."""
    org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    assert org is not None
    owner = await sign_in(manager, "ann@example.test", org[1].id)
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(
            request(), (await provider_login(manager, "ann@example.test")).token
        ),
        org[1].id,
    )
    key = await manager.credentials.create_api_key(owner, "ci", Role.MEMBER)
    await seed_operator(manager, "root@example.test")
    _, secret = await enrolled_operator(manager, operator, clock, "root@example.test")
    minting = await minting_operator(manager, clock, "root@example.test", secret)
    token = await operator.issue_operator_token(minting, OperatorRole.READ)
    login = await second_factor(manager, "root@example.test", clock.code(secret))
    reads = counted_reads(storage, monkeypatch)
    await manager.authenticate(request(), session.token)
    assert reads == ["read_auth_session_by_digest", "read_principal"]
    reads.clear()
    await manager.authenticate(request(), key.key)
    assert reads == ["read_api_key_by_digest", "read_principal"]
    # A key's socket asks again in as many reads.
    reads.clear()
    await manager.resume(
        request(), org[1].id, CredentialKind.API_KEY, key.api_key.id, record_use=False
    )
    assert reads == ["read_api_key", "read_principal"]
    for credential in (login.token, token.token):
        reads.clear()
        await manager.admit_operator(await manager.authenticate_login(request(), credential))
        assert reads == ["read_auth_session_with_identity_by_digest"]


async def test_the_grant_job_puts_an_identity_on_the_allowlist_and_audits_it(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    relay = SpyRelay(OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics()))
    manager = tenancy_signing_in(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(),
    )
    with pytest.raises(NotFound):
        await manager.grant_operator(request(), "ann@example.test", OperatorRole.READ)
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    granted = await manager.grant_operator(request(), "ann@example.test", OperatorRole.READ)
    assert granted.operator_role is OperatorRole.READ
    again = await manager.grant_operator(request(), "ann@example.test", OperatorRole.READ)
    assert again == granted, "a rerun changes nothing"
    disabled = await manager.disable_operator(request(), "ann@example.test")
    assert disabled.operator_role is None
    audit = [
        (org_id, r.kind, r.target_id, r.actor_id)
        for org_id, r in relay.rows
        if r.kind.startswith("tenancy.operator.")
    ]
    assert audit == [
        (EMPTY_UUID, "tenancy.operator.granted", granted.id, EMPTY_UUID),
        (EMPTY_UUID, "tenancy.operator.disabled", granted.id, EMPTY_UUID),
    ]
    # The system's own identities are made by the first grant, with no org
    # and no way to sign in; nobody signs in as one.
    provisioner = await manager.grant_operator(
        request(), "provisioner@platform.acme.invalid", OperatorRole.WRITE
    )
    assert provisioner.operator_role is OperatorRole.WRITE
    with pytest.raises(InvalidCredential):
        await provider_login(manager, "smoke@platform.acme.invalid")
    token = await manager.grant_operator_token(request(), "provisioner@platform.acme.invalid")
    assert token.operator_role is OperatorRole.WRITE
    read_only = await manager.grant_operator_token(
        request(), "provisioner@platform.acme.invalid", operator_role=OperatorRole.READ
    )
    assert read_only.operator_role is OperatorRole.READ
    with pytest.raises(NotAnOperator):
        await manager.grant_operator_token(request(), "ann@example.test")


async def test_the_operator_allowlist_takes_an_address_in_any_case(
    manager: TenancyManagerInterface,
) -> None:
    """The grant, the grant job's token, and the disable each find the
    person the seeding made as `Root@Example.test` by another spelling, and
    the system's own identity is kept folded and found the same way."""
    await manager.bootstrap(request(), "Root", "root", "Root@Example.test", "Root")
    granted = await manager.grant_operator(request(), "root@EXAMPLE.test", OperatorRole.READ)
    assert granted.email == "root@example.test" and granted.operator_role is OperatorRole.READ
    issued = await manager.grant_operator_token(request(), "ROOT@example.test")
    admin = await manager.admit_operator(await manager.authenticate_login(request(), issued.token))
    assert admin.identity_id == granted.id
    disabled = await manager.disable_operator(request(), "Root@Example.Test")
    assert disabled.id == granted.id and disabled.operator_role is None
    provisioner = await manager.grant_operator(
        request(), "Provisioner@Platform.Acme.Invalid", OperatorRole.WRITE
    )
    assert provisioner.email == "provisioner@platform.acme.invalid"
    again = await manager.grant_operator(
        request(), "provisioner@platform.acme.invalid", OperatorRole.WRITE
    )
    assert again.id == provisioner.id


async def test_a_member_added_by_another_spelling_is_the_same_person(
    manager: TenancyManagerInterface,
) -> None:
    """Adding by address finds the identity whatever the case, whether the
    seeding or the person's own sign-in made it, and the user keeps the
    identity's folded address."""
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, dee, created = await manager.add_member(
        request(), "ajax", "Dee@Example.test", "Dee", Role.MEMBER
    )
    assert created and dee.email == "dee@example.test"
    _, again, created = await manager.add_member(
        request(), "ajax", "dee@EXAMPLE.test", "Dee", Role.MEMBER
    )
    assert not created and again.id == dee.id
    signed_up = await provider_login(manager, "cid@example.test")
    identity_id = signed_up.memberships[0].user.identity_id
    _, cid, created = await manager.add_member(
        request(), "ajax", "CID@Example.test", "Cid", Role.MEMBER
    )
    assert created and cid.identity_id == identity_id and cid.email == "cid@example.test"


async def test_an_operator_closes_an_org_at_once_and_the_queue_deletes_it(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl, infra: InfraLocalImpl
) -> None:
    published: list[TopicPayload] = []

    async def hear(payload: TopicPayload) -> None:
        published.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", hear)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    login = await provider_login(manager, "ann@example.test")
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    admin = await token_operator(manager, "root@example.test")

    closed = await operator.delete_org(admin, org.id)
    # Closed, not yet deleted: the org waits, live and empty, for the queue.
    assert closed.deleted_at is None and closed.updated_by == admin.identity_id
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), issued.token)
    # Ann keeps her personal org, the one place no deletion reaches.
    left = (await provider_login(manager, "ann@example.test")).memberships
    assert [m.org.kind for m in left] == [OrgKind.PERSONAL]
    # A repeat, before the queue has run, answers the org as it stands.
    assert await operator.delete_org(admin, org.id) == closed

    deleted = await operator_deletes(manager, operator, admin, org.id)
    assert deleted.deleted_at is not None and deleted.deleted_by == admin.identity_id
    assert deleted.updated_at == deleted.deleted_at
    # Every change is announced into the tenant's stream, so every socket of
    # the tenant closes, in whichever process holds it; the operator's identity
    # is the actor, since an operator has no user in the tenant.
    frames = [p for p in published if isinstance(p, EntityChangedPayload)]
    assert [(f.kind, f.target_id, f.org_id, f.actor_id) for f in frames if "org" in f.kind] == [
        ("tenancy.org.deleted", org.id, org.id, admin.identity_id)
    ]
    assert {(f.kind, f.actor_id) for f in frames if f.kind == "tenancy.user.deleted"} == {
        ("tenancy.user.deleted", admin.identity_id)
    }
    # The sweep still visits the deleted tenant: its rows are the sweep's to purge.
    assert org.id in [c.org_id for c in await manager.service_contexts(request())]
    with pytest.raises(NotFound):
        await operator.delete_org(admin, org.id)
    with pytest.raises(NotFound):
        await operator.delete_org(admin, new_id())


async def test_a_deleted_orgs_rows_are_purged_once_the_retention_has_passed(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ann = await sign_in(manager, "ann@example.test", org.id)
    await manager.credentials.create_api_key(ann, "ci", Role.MEMBER)
    await manager.issue_ticket(ann)
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    admin = await token_operator(manager, "root@example.test")
    await operator_deletes(manager, operator, admin, org.id)
    sweep = next(c for c in await manager.service_contexts(request()) if c.org_id == org.id)
    assert sweep.role is Role.SERVICE and sweep.user_id == EMPTY_UUID
    # Within the retention nothing of the tenant is deleted in its own right, so
    # nothing goes; past it, every row of the tenant goes and the org row stays.
    assert await manager.purge_tenant(sweep) == 0
    no_retention = make_manager(storage, infra, TenancyOptions(retention=timedelta(0)))
    assert await no_retention.purge_tenant(sweep) == 5, "user, membership, key, session, ticket"
    assert await storage.read_users(org.id, None, limit=10) == []
    assert await storage.read_memberships(org.id, limit=10) == []
    assert await storage.read_api_keys(org.id, None, limit=10) == []
    assert await storage.read_auth_sessions(org.id, ann.user_id, utcnow(), 10) == []
    tombstone = await storage.read_org(org.id)
    assert tombstone is not None and tombstone.deleted_at is not None
    assert await no_retention.purge_tenant(sweep) == 0


async def test_a_deleted_tenant_past_its_retention_is_marked_purged_and_left_out(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """The sweep marks a tenant purged once a pass found nothing of it left;
    the mark takes only a tenant past its retention, and the sweep's
    contexts leave a marked one out. The expiry the pass asks is answered
    from the org rows the contexts were minted from."""
    _, live = await manager.bootstrap(request(), "Live", "live", "ann@example.test", "Ann")
    _, gone = await manager.bootstrap(request(), "Gone", "gone", "bob@example.test", "Bob")
    stored = await storage.read_org(gone.id)
    assert stored is not None
    long_ago = utcnow() - timedelta(days=40)
    await storage.write_org(gone.id, stored.model_copy(update={"deleted_at": long_ago}))
    contexts = {c.org_id: c for c in await manager.service_contexts(request())}
    assert await manager.tenant_expired(contexts[gone.id]) is True
    assert await manager.tenant_expired(contexts[live.id]) is False
    assert await manager.mark_purged(contexts[live.id]) is False, "a live tenant"
    assert await manager.mark_purged(contexts[EMPTY_UUID]) is False, "the system scope"
    assert await manager.mark_purged(contexts[gone.id]) is True
    marked = await storage.read_org(gone.id)
    assert marked is not None and marked.purged_at is not None
    assert await manager.mark_purged(contexts[gone.id]) is False, "once"
    swept = [c.org_id for c in await manager.service_contexts(request())]
    assert gone.id not in swept and live.id in swept and swept[0] == EMPTY_UUID
    assert await manager.tenant_expired(contexts[gone.id]) is True, "read again outside a pass"


async def test_the_sweep_context_is_the_passs_and_none_for_a_purged_tenant(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A purge across tenants that found a tenant's row asks for the context
    the pass minted for that tenant, deleted tenants included: under the
    pass's request stage it reads nothing. Outside a pass it reads the org
    row. A tenant marked purged, before the pass or during it, and one with
    no org row, have none."""
    _, live = await manager.bootstrap(request(), "Live", "live", "ann@example.test", "Ann")
    _, gone = await manager.bootstrap(request(), "Gone", "gone", "bob@example.test", "Bob")
    _, past = await manager.bootstrap(request(), "Past", "past", "cid@example.test", "Cid")
    for org, deleted_at in ((gone, utcnow()), (past, utcnow() - timedelta(days=40))):
        stored = await storage.read_org(org.id)
        assert stored is not None
        await storage.write_org(org.id, stored.model_copy(update={"deleted_at": deleted_at}))
    rctx = request()
    contexts = {c.org_id: c for c in await manager.service_contexts(rctx)}
    reads: list[UUID] = []
    read_org = storage.read_org

    async def counted(org_id: UUID) -> Org | None:
        reads.append(org_id)
        return await read_org(org_id)

    monkeypatch.setattr(storage, "read_org", counted)
    for org_id in (EMPTY_UUID, live.id, gone.id, past.id):
        ctx = await manager.sweep_context(rctx, org_id)
        assert ctx is not None and ctx.org_id == org_id
        assert ctx.security.role is Role.SERVICE and ctx.user_id == EMPTY_UUID
        assert ctx.security.credential_kind is CredentialKind.INTERNAL
        assert ctx.request_id == rctx.request_id
    assert reads == [], "the pass's tenants are known"
    # The pass marks a tenant past its retention purged: from then on it has
    # no context, in this pass as in the next.
    assert await manager.mark_purged(contexts[past.id]) is True
    assert await manager.sweep_context(rctx, past.id) is None, "marked purged in this pass"
    assert await manager.sweep_context(request(), past.id) is None, "marked purged before"
    assert await manager.sweep_context(request(), gone.id) is not None, "a deleted tenant"
    assert await manager.sweep_context(request(), new_id()) is None, "no org row"
    assert await manager.sweep_context(request(), EMPTY_UUID) is not None, "the system scope"


async def test_resume_and_service_contexts(manager: TenancyManagerInterface) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(request(), "Beta", "beta", "bob@example.test", "Bob")
    ctx = await sign_in(manager, "ann@example.test", org.id)

    contexts = await manager.service_contexts(request())
    # Two team orgs and the personal org of each owner.
    assert len(contexts) == 5, "the system scope, then every tenant"
    assert contexts[0].org_id == EMPTY_UUID
    assert all(c.security.role is Role.SERVICE for c in contexts)
    assert all(c.security.credential_kind is CredentialKind.INTERNAL for c in contexts)
    # Minted for the tenant: the system user is the actor, not the founder.
    assert all(c.user_id == EMPTY_UUID for c in contexts)

    rebuilt = await manager.service_context(request(), org.id, ctx.user_id)
    assert rebuilt.user_id == ctx.user_id
    with pytest.raises(InvalidCredential):
        await manager.resume(request(), org.id, CredentialKind.AUTH_SESSION_TOKEN, new_id())


async def test_a_claim_for_a_departed_members_item_still_runs_under_their_name(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    operator: TenancyOperatorManagerImpl,
) -> None:
    owner, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ann = await storage.read_user(org.id, owner.user_id)
    assert ann is not None
    now = utcnow()
    await storage.write_user(
        org.id, ann.model_copy(update={"deleted_at": now, "deleted_by": ann.id, "updated_at": now})
    )
    # The person authorized the work at enqueue; the claim keeps them as the
    # attribution and runs on the service role's authority.
    ctx = await manager.service_context(request(), org.id, ann.id)
    assert ctx.user_id == ann.id and ctx.role is Role.SERVICE
    assert ctx.credential_kind is CredentialKind.INTERNAL and ctx.has(Permission.WRITE)
    # Only the tenant must be live.
    await manager.bootstrap(
        request(),
        "Ops",
        "ops",
        "root@example.test",
        "Root",
        operator_role=OperatorRole.WRITE,
    )
    admin = await token_operator(manager, "root@example.test")
    await operator_deletes(manager, operator, admin, org.id)
    with pytest.raises(InvalidCredential):
        await manager.service_context(request(), org.id, ann.id)


async def personal_orgs(storage: TenancyStorageMemoryImpl) -> set[UUID]:
    """The personal orgs the seeding made beside the team orgs."""
    return {org.id for org in await storage.read_orgs(50) if org.personal}


async def test_a_tenant_whose_members_have_all_left_is_still_swept(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    owner, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ann = await storage.read_user(org.id, owner.user_id)
    assert ann is not None
    now = utcnow()
    await storage.write_user(
        org.id, ann.model_copy(update={"deleted_at": now, "deleted_by": ann.id, "updated_at": now})
    )
    contexts = await manager.service_contexts(request())
    personal = await personal_orgs(storage)
    assert len(personal) == 1
    assert [c.org_id for c in contexts if c.org_id not in personal] == [EMPTY_UUID, org.id]
    ctx = next(c for c in contexts if c.org_id == org.id)
    assert ctx.user_id == EMPTY_UUID and ctx.role is Role.SERVICE
    # The purge across tenants takes the one member who left, and the
    # tenant's own purge takes nothing of a tenant that lives.
    assert await manager.purge_across_tenants() == 0, "retention has not passed"
    no_retention = make_manager(storage, infra, TenancyOptions(retention=timedelta(0)))
    assert await no_retention.purge_tenant(ctx) == 0, "the tenant lives"
    assert await no_retention.purge_across_tenants() == 2, "the user and the membership"
    assert await storage.read_user(org.id, ann.id) is None


async def test_the_sweep_purges_the_expired_logins_of_the_system_scope(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    # A login credential lives under the system scope, one row per sign-in.
    # The purge runs across tenants in the system scope, so the expired ones
    # go with a tenant's dead sessions; the sweep still mints a context for
    # that scope first.
    manager = make_manager(storage, infra, TenancyOptions(login_ttl=timedelta(seconds=-1)))
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    found = await storage.read_auth_session_by_digest(hash_token(login.token))
    assert found is not None and found[0] == EMPTY_UUID
    system = next(c for c in await manager.service_contexts(request()) if c.org_id == EMPTY_UUID)
    assert system.user_id == EMPTY_UUID and system.role is Role.SERVICE
    assert await manager.purge_across_tenants() == 0, "retention has not passed"
    no_retention = make_manager(storage, infra, TenancyOptions(retention=timedelta(0)))
    assert await no_retention.purge_tenant(system) == 0, "the system scope never expires"
    assert await no_retention.purge_across_tenants() == 1
    assert await storage.read_auth_session_by_digest(hash_token(login.token)) is None


async def test_expired_api_keys_are_purged_like_revoked_ones(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(storage, infra, TenancyOptions(api_key_ttl=timedelta(seconds=1)))
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    expired = await manager.credentials.create_api_key(
        ctx, "short", Role.MEMBER, ttl=timedelta(seconds=1)
    )
    await asyncio.sleep(1.01)
    assert [k.id for k in (await manager.credentials.get_api_keys(ctx, None, limit=10)).items] == [
        expired.api_key.id
    ]
    no_retention = make_manager(storage, infra, TenancyOptions(retention=timedelta(0)))
    assert await no_retention.purge_across_tenants() == 1
    assert await storage.read_api_key(org.id, expired.api_key.id) is None


async def test_a_socket_ticket_is_redeemed_exactly_once(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)

    issued = await manager.issue_ticket(ctx)
    assert issued.ticket.startswith("tkt_")
    assert issued.expires_at > utcnow()
    principal = await manager.redeem_ticket(request(), issued.ticket)
    socket_ctx = principal.ctx
    assert socket_ctx.user_id == ctx.user_id
    assert socket_ctx.security.credential_kind is CredentialKind.SOCKET_TICKET
    assert socket_ctx.security.credential_id == ctx.security.credential_id
    # The socket's authority ends with the session behind the ticket.
    session = await storage.read_auth_session(org.id, ctx.security.credential_id)
    assert session is not None and principal.expires_at == session.expires_at
    with pytest.raises(InvalidCredential):
        await manager.redeem_ticket(request(), issued.ticket)
    with pytest.raises(NotAuthorized):
        await manager.issue_ticket(socket_ctx)
    with pytest.raises(InvalidCredential):
        await manager.redeem_ticket(request(), "tkt_never-issued")
    with pytest.raises(InvalidCredential):
        await manager.redeem_ticket(request(), "ses_not-a-ticket")


async def test_a_socket_is_resumed_without_recording_a_use(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """The redemption names what the socket needs to ask again: the kind of
    the credential behind the ticket and the membership. Asking again with
    `record_use=False` leaves the session's last use where it was."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    principal = await manager.redeem_ticket(request(), (await manager.issue_ticket(ctx)).ticket)
    membership = await storage.read_membership_for_user(org.id, ctx.user_id)
    assert membership is not None
    assert principal.credential_kind is CredentialKind.AUTH_SESSION_TOKEN
    assert principal.membership_id == membership.id

    session = await storage.read_auth_session(org.id, ctx.security.credential_id)
    assert session is not None
    seen = utcnow() - timedelta(hours=1)
    await storage.write_auth_session(org.id, session.model_copy(update={"last_seen_at": seen}))
    again = await manager.resume(
        request(), org.id, principal.credential_kind, ctx.security.credential_id, record_use=False
    )
    assert again.ctx.security == principal.ctx.security
    session = await storage.read_auth_session(org.id, ctx.security.credential_id)
    assert session is not None and session.last_seen_at == seen


async def test_concurrent_redemptions_admit_one_socket(manager: TenancyManagerInterface) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    issued = await manager.issue_ticket(ctx)

    outcomes = await asyncio.gather(
        *(manager.redeem_ticket(request(), issued.ticket) for _ in range(5)),
        return_exceptions=True,
    )
    admitted = [o for o in outcomes if isinstance(o, SocketPrincipal)]
    refused = [o for o in outcomes if isinstance(o, InvalidCredential)]
    assert len(admitted) == 1 and len(refused) == 4


async def test_the_ticket_row_decides_while_the_cache_is_down(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(storage, infra, cache=DownCache())
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    issued = await manager.issue_ticket(ctx)
    outcomes = await asyncio.gather(
        *(manager.redeem_ticket(request(), issued.ticket) for _ in range(5)),
        return_exceptions=True,
    )
    assert len([o for o in outcomes if isinstance(o, SocketPrincipal)]) == 1
    assert len([o for o in outcomes if isinstance(o, InvalidCredential)]) == 4
    with pytest.raises(InvalidCredential):
        await manager.redeem_ticket(request(), issued.ticket)
    # The row is spent: the one admitted redeemer consumed it.
    assert await storage.redeem_socket_ticket(hash_token(issued.ticket), utcnow()) is None


async def test_redeeming_a_ticket_rechecks_the_credential_behind_it(
    manager: TenancyManagerInterface,
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    ctx = await manager.authenticate(request(), session.token)
    issued = await manager.issue_ticket(ctx)
    await manager.sign_in.logout(await manager.authenticate_login(request(), session.token))
    with pytest.raises(CredentialExpired):
        await manager.redeem_ticket(request(), issued.ticket)

    key = await manager.credentials.create_api_key(
        await sign_in(manager, "ann@example.test", org.id), "ci", Role.MEMBER
    )
    key_ctx = await manager.authenticate(request(), key.key)
    from_key = await manager.redeem_ticket(request(), (await manager.issue_ticket(key_ctx)).ticket)
    assert from_key.ctx.security.role is Role.MEMBER
    assert from_key.expires_at == key.api_key.expires_at


async def test_expired_tickets_are_refused(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(storage, infra, TenancyOptions(ticket_ttl=timedelta(seconds=-1)))
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    issued = await manager.issue_ticket(ctx)
    with pytest.raises(InvalidCredential):
        await manager.redeem_ticket(request(), issued.ticket)


async def test_add_member_seeds_a_second_person_once(manager: TenancyManagerInterface) -> None:
    owner, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx, bob, created = await manager.add_member(
        request(), "ajax", "bob@example.test", "Bob", Role.MEMBER
    )
    assert created and bob.display_name == "Bob"
    # The write ran under the creator's context and is recorded as theirs.
    assert ctx.org_id == org.id and ctx.user_id == owner.user_id
    assert ctx.security.role is Role.OWNER
    assert ctx.security.credential_kind is CredentialKind.INTERNAL
    assert bob.created_by == owner.user_id
    membership = (await manager.members.get_memberships(owner, None, limit=10)).items
    assert [(m.user_id, m.created_by) for m in membership if m.user_id == bob.id] == [
        (bob.id, owner.user_id)
    ]
    _, again, created_again = await manager.add_member(
        request(), "ajax", "bob@example.test", "Robert", Role.ADMIN
    )
    assert not created_again and again.id == bob.id and again.display_name == "Bob"
    assert sorted(
        u.display_name for u in (await manager.members.get_users(owner, None, limit=10)).items
    ) == [
        "Ann",
        "Bob",
    ]

    login = await provider_login(manager, "bob@example.test")
    assert [(m.org.id, m.role) for m in team(login.memberships)] == [(org.id, Role.MEMBER)]
    # Bob was nobody before: he came with his personal org, and owns it.
    assert [(m.org.name, m.role) for m in login.memberships if m.org.personal] == [
        ("Bob", Role.OWNER)
    ]


async def test_add_member_reuses_an_identity_across_orgs(manager: TenancyManagerInterface) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.bootstrap(request(), "Globex", "globex", "gus@example.test", "Gus")
    _, _, created = await manager.add_member(
        request(), "globex", "ann@example.test", "Ann", Role.VIEWER
    )
    assert created
    login = await provider_login(manager, "ann@example.test")
    assert sorted(m.role.value for m in team(login.memberships)) == ["owner", "viewer"]


async def test_add_member_refuses_an_unknown_org(manager: TenancyManagerInterface) -> None:
    with pytest.raises(NotFound):
        await manager.add_member(request(), "nope", "bob@example.test", "Bob", Role.MEMBER)


async def test_add_member_caps_the_role_at_the_creators_and_records_the_write(
    manager: TenancyManagerInterface, infra: InfraLocalImpl
) -> None:
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    with pytest.raises(ValidationFailed):
        await manager.add_member(request(), "ajax", "svc@example.test", "Svc", Role.SERVICE)
    ctx, bob, _ = await manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.ADMIN)
    pushes = [p for p in seen if isinstance(p, EntityChangedPayload)]
    assert [(p.org_id, p.kind, p.target_id, p.seq) for p in pushes] == [
        (org.id, "tenancy.user.created", bob.id, 1)
    ]
    assert ctx.security.role is Role.OWNER  # the cap: a creator seeds at most their own rank


class RacedIdentityStorage(TenancyStorageMemoryImpl):
    """The read by email misses: another request wrote that identity between
    our read and our write, which is what the unique key is for."""

    async def read_identity_by_email_digest(self, email_digest: str) -> Identity | None:
        return None


async def test_a_duplicate_email_the_read_missed_is_a_conflict_and_leaves_nothing_behind(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    storage = RacedIdentityStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    with pytest.raises(UniqueKeyTaken) as raced:
        await manager.bootstrap(request(), "Globex", "globex", "ann@example.test", "Ann")
    assert (raced.value.http_status, raced.value.code) == (409, "unique_key_taken")
    assert await storage.read_org_by_slug("globex") is None
    with pytest.raises(UniqueKeyTaken) as raced:
        await manager.add_member(request(), "ajax", "ann@example.test", "Ann", Role.MEMBER)
    assert raced.value.http_status == 409
    assert len(await storage.read_users(org.id, None, limit=10)) == 1
    assert len(await storage.read_memberships(org.id, limit=10)) == 1


class RacedSlugStorage(TenancyStorageMemoryImpl):
    """The read by slug misses: another request took the slug between our
    read and our create, which is what the unique key is for."""

    async def read_org_by_slug(self, slug: str) -> Org | None:
        return None


async def test_a_slug_taken_meanwhile_leaves_no_identity_behind(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    storage = RacedSlugStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    # A new person: the refused tenant leaves no identity behind, so a retry
    # is not kept out.
    with pytest.raises(UniqueKeyTaken):
        await manager.bootstrap(request(), "Ajax 2", "ajax", "bob@example.test", "Bob")
    assert await storage.read_identity_by_email_digest(email_digest("bob@example.test")) is None
    _, again = await manager.bootstrap(request(), "Bobs", "bobs", "bob@example.test", "Bob")
    assert again.slug == "bobs"
    assert await sign_in(manager, "bob@example.test", again.id)
    # An existing person asked to be promoted: the refused tenant promotes nobody.
    with pytest.raises(UniqueKeyTaken):
        await manager.bootstrap(
            request(),
            "Ops",
            "ajax",
            "ann@example.test",
            "Ann",
            operator_role=OperatorRole.WRITE,
        )
    ann = await storage.read_identity_by_email_digest(email_digest("ann@example.test"))
    assert ann is not None and ann.operator_role is None


class DownOnCreateStorage(TenancyStorageMemoryImpl):
    """The create fails after every read: the twin of a database that went
    away, or a key another request took."""

    async def create_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        identity: Identity | None = None,
        personal: tuple[Org, User, Membership] | None = None,
        invitation: Invitation | None = None,
    ) -> None:
        raise UniqueKeyTaken("a key is taken")


async def test_an_add_member_that_fails_leaves_no_identity_behind(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    storage = DownOnCreateStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    with pytest.raises(UniqueKeyTaken):
        await manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    assert await storage.read_identity_by_email_digest(email_digest("bob@example.test")) is None


def held_relay(
    outbox: OutboxStorageMemoryImpl, infra: InfraLocalImpl, rctx: RequestContext
) -> OutboxRelayImpl:
    """A relay that holds the request's rows, so what an add landed stays
    pending in the outbox, where a test reads it."""
    relay = OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics())
    relay.hold(rctx.request_id)
    return relay


async def join(
    storage: TenancyStorageMemoryImpl,
    relay: OutboxRelayInterface,
    org: Org,
    rctx: RequestContext,
    email: str,
    admission: Admission,
    max_orgs: int = MAX_ORGS_PER_IDENTITY,
) -> tuple[User, bool]:
    """A person added to the org as a member, past the org's gate."""
    return await add_member_to(
        storage,
        relay,
        org_id=org.id,
        user_id=new_id(),
        email=email,
        display_name=email.partition("@")[0],
        role=Role.MEMBER,
        actor_id=org.created_by,
        request=rctx,
        max_orgs=max_orgs,
        admission=admission,
    )


def counted_row(org: Org, rctx: RequestContext) -> OutboxRow:
    """A row a gate answers: work that moves a count of the org's members."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org.id,
        kind="work.noop",
        target_id=org.id,
        payload={},
        actor_id=org.created_by,
        request_id=rctx.request_id,
        app=rctx.app.type.value,
    )


async def test_a_repeated_add_never_asks_the_gate(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    outbox: OutboxStorageMemoryImpl,
    infra: InfraLocalImpl,
) -> None:
    """A member's admission is asked once the person is known to be new to the
    org. A repeated add answers the member as stored and asks nothing, so a
    gate that counts members never counts one twice; a person past their own
    bound on orgs is refused before it."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, beta = await manager.bootstrap(request(), "Beta", "beta", "cid@example.test", "Cid")
    asked: list[UUID] = []

    async def gate() -> tuple[OutboxRow, ...]:
        asked.append(new_id())
        return ()

    rctx = request()
    relay = held_relay(outbox, infra, rctx)
    bob, created = await join(storage, relay, org, rctx, "bob@example.test", gate)
    assert created and len(asked) == 1
    again, created = await join(storage, relay, org, rctx, "bob@example.test", gate)
    assert not created and again == bob
    _, created = await join(storage, relay, org, rctx, "ann@example.test", gate)
    assert not created, "the owner is a member already"
    assert len(asked) == 1, "a member already there is never asked about again"
    # Bob holds his personal org and Ajax: a bound of two refuses a third.
    with pytest.raises(MembershipLimitReached):
        await join(storage, relay, beta, rctx, "bob@example.test", gate, max_orgs=2)
    assert len(asked) == 1, "a person refused for their own bound is never asked about"


async def test_the_gates_rows_land_in_the_adds_commit(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    outbox: OutboxStorageMemoryImpl,
    infra: InfraLocalImpl,
) -> None:
    """The rows a member's admission answers land with the user, the membership,
    and the row that announces them, in the add's commit."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    rctx = request()

    async def gate() -> tuple[OutboxRow, ...]:
        return (counted_row(org, rctx),)

    bob, created = await join(
        storage, held_relay(outbox, infra, rctx), org, rctx, "bob@example.test", gate
    )
    assert created
    landed = [
        (r.kind, r.target_id) for r in await claim_all(outbox) if r.request_id == rctx.request_id
    ]
    assert sorted(landed) == sorted([("tenancy.user.created", bob.id), ("work.noop", org.id)])


async def test_an_add_that_fails_lands_none_of_the_gates_rows(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The gate's rows ride the add's commit, or nothing does: an add whose
    commit fails leaves no row that counts a member who never landed."""
    storage = DownOnCreateStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    rctx = request()
    asked: list[UUID] = []

    async def gate() -> tuple[OutboxRow, ...]:
        asked.append(new_id())
        return (counted_row(org, rctx),)

    with pytest.raises(UniqueKeyTaken):
        await join(storage, held_relay(outbox, infra, rctx), org, rctx, "bob@example.test", gate)
    assert len(asked) == 1
    assert [r for r in await claim_all(outbox) if r.request_id == rctx.request_id] == []


async def test_a_gates_refusal_lands_before_the_add(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    outbox: OutboxStorageMemoryImpl,
    infra: InfraLocalImpl,
) -> None:
    """A member's admission refuses by raising, before anything is written: no
    identity for a person nobody has seen, no user, no membership, no row."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    rctx = request()

    async def full() -> tuple[OutboxRow, ...]:
        raise Conflict(f"org {org.id} admits no one more")

    with pytest.raises(Conflict):
        await join(storage, held_relay(outbox, infra, rctx), org, rctx, "bob@example.test", full)
    assert await storage.read_identity_by_email_digest(email_digest("bob@example.test")) is None
    assert [u.id for u in await storage.read_users(org.id, None, limit=10)] == [org.created_by]
    assert [m.user_id for m in await storage.read_memberships(org.id, limit=10)] == [org.created_by]
    assert [r for r in await claim_all(outbox) if r.request_id == rctx.request_id] == []


class RacedUserStorage(TenancyStorageMemoryImpl):
    """The read of the identity's users misses: another request added the same
    person between our read and our write."""

    async def read_users_by_identity(
        self, identity_id: UUID, limit: int
    ) -> list[tuple[UUID, User]]:
        return []


async def test_a_raced_add_member_leaves_no_membership_without_its_user(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    storage = RacedUserStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, bob, created = await manager.add_member(
        request(), "ajax", "bob@example.test", "Bob", Role.MEMBER
    )
    assert created
    with pytest.raises(UniqueKeyTaken):
        await manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.ADMIN)
    assert [u.id for u in await storage.read_users(org.id, None, limit=10)] == sorted(
        [bob.id, org.created_by]
    )
    assert sorted(m.user_id for m in await storage.read_memberships(org.id, limit=10)) == sorted(
        [bob.id, org.created_by]
    )
    pending = await outbox.claim_pending(10, utcnow(), timedelta(0), timedelta(0), timedelta(0))
    assert pending == []  # the losing add announced nothing


async def test_one_person_joins_at_most_the_bound_of_orgs(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The users one identity is are read bounded, at one past the orgs a
    person may join. A create that would add one more is refused; a member
    already in the org is still found at the bound; and a list past the bound
    (two adds that raced) is refused, never cut short, since the org a sign-in
    names could be the row cut off."""
    storage = TenancyStorageMemoryImpl(outbox)
    # Three places: the personal org every person has, and two more.
    manager = make_manager(storage, infra, TenancyOptions(max_orgs_per_identity=3), outbox=outbox)
    _, first = await manager.bootstrap(request(), "A", "a", "ann@example.test", "Ann")
    _, second = await manager.bootstrap(request(), "B", "b", "ann@example.test", "Ann")
    await manager.bootstrap(request(), "C", "c", "cid@example.test", "Cid")
    with pytest.raises(MembershipLimitReached):
        await manager.bootstrap(request(), "D", "d", "ann@example.test", "Ann")
    assert await storage.read_org_by_slug("d") is None
    with pytest.raises(MembershipLimitReached):
        await manager.add_member(request(), "c", "ann@example.test", "Ann", Role.MEMBER)
    _, again, created = await manager.add_member(
        request(), "a", "ann@example.test", "Ann", Role.MEMBER
    )
    assert not created and again.email == "ann@example.test"
    login = await provider_login(manager, "ann@example.test")
    assert {m.org.id for m in team(login.memberships)} == {first.id, second.id}

    # A third user lands past the refusal, the way two racing adds would.
    identity = await storage.read_identity_by_email_digest(email_digest("ann@example.test"))
    assert identity is not None
    third = await storage.read_org_by_slug("c")
    assert third is not None
    raced = make_user(identity.id, "ann@example.test")
    await storage.write_user(third.id, raced)
    await storage.write_membership(third.id, make_membership(raced.id))
    with pytest.raises(MembershipLimitReached):
        await provider_login(manager, "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    with pytest.raises(MembershipLimitReached):
        await manager.sign_in.exchange_login(ictx, first.id)


async def test_an_operator_never_deletes_a_personal_org(
    manager: TenancyManagerInterface,
    operator: TenancyOperatorManagerImpl,
    clock: SteppingClock,
    storage: TenancyStorageMemoryImpl,
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    await seed_operator(manager, "root@example.test")
    admin, _ = await enrolled_operator(manager, operator, clock, "root@example.test")
    with pytest.raises(PersonalOrgFixed):
        await operator.delete_org(admin, dee.org_id)
    org = await storage.read_org(dee.org_id)
    assert org is not None and org.deleted_at is None
    # A team org is deleted, through the queue as its owner's is.
    team_org = await manager.org.create_org(dee, "Bakery", None)
    deleted = await operator_deletes(manager, operator, admin, team_org.org.id)
    assert deleted.deleted_at is not None


async def test_a_live_session_proves_the_identity_and_a_dead_one_does_not(
    manager: TenancyManagerInterface,
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    ictx = await manager.authenticate_login(request(), session.token)
    assert ictx.email == "ann@example.test"
    assert ictx.credential_kind is CredentialKind.AUTH_SESSION_TOKEN
    await manager.sign_in.logout(ictx)
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), session.token)
    with pytest.raises(InvalidCredential):
        await manager.authenticate_login(request(), "ses_never-issued")


async def test_an_expired_session_or_a_removed_members_proves_no_identity(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    expiring = make_manager(storage, infra, TenancyOptions(auth_session_ttl=timedelta(seconds=-1)))
    manager = make_manager(storage, infra)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await provider_login(manager, "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    stale = await expiring.sign_in.exchange_login(ictx, org.id)
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), stale.token)
    # A member removed from the org: the session they held proves nothing.
    bob = await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    bob_login = await provider_login(manager, "bob@example.test")
    bob_session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), bob_login.token), org.id
    )
    owner = await sign_in(manager, "ann@example.test", org.id)
    await manager.members.remove_member(owner, bob.id)
    with pytest.raises((CredentialExpired, InvalidCredential)):
        await manager.authenticate_login(request(), bob_session.token)
