import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import pytest
from contracts.factories import make_membership, make_user
from contracts.outbox_storage import claim_all
from contracts.second_factor import (
    TOTP_KEY,
    SteppingClock,
    enrolled_operator,
    minting_operator,
    second_factor,
    secret_of,
)

from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import EMPTY_UUID, new_id, utcnow
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
    TenantContext,
)
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
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
    SignInDelayed,
    Unavailable,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.idempotency.types.attempt import Attempt, lease_bound
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.relay import OutboxRelayInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.operator import (
    TOTP_ISSUER,
    TenancyOperatorManagerImpl,
    TenancyOperatorOptions,
)
from acme.om.tenancy.rules import (
    email_digest,
    hash_token,
)
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.issued import IssuedSession, OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.om.tenancy.types.user import PERSONAL_FIELDS, User

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def team(memberships: tuple[OrgMembership, ...]) -> list[OrgMembership]:
    """The team orgs among a person's places; the personal one is always there."""
    return [m for m in memberships if not m.org.personal]


def request(app: AppContext = APP) -> RequestContext:
    """The request stage a test mints at its edge, one per call."""
    return RequestContext(request_id=new_id(), app=app)


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


async def begin_attempt(
    markers: IdempotencyStorageMemoryImpl, ctx: TenantContext, key: str, target_id: UUID
) -> Attempt:
    """What the gateway does before a creating request: a pending marker on the
    id the create uses, under a token of its own, and the attempt that carries
    both down to the write."""
    attempt_id = new_id()
    await markers.write_record(
        ctx.org_id,
        IdempotencyRecord(
            id=new_id(),
            created_at=utcnow(),
            user_id=ctx.user_id,
            key=key,
            request_digest="digest",
            target_id=target_id,
            attempt_id=attempt_id,
        ),
    )
    return Attempt(target_id=target_id, attempt_id=attempt_id)


def make_manager(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    options: TenancyOptions | None = None,
    cache: CacheInterface | None = None,
    outbox: OutboxStorageMemoryImpl | None = None,
    clock: SteppingClock | None = None,
) -> TenancyManagerInterface:
    # A relay over its own outbox is enough where the sweep never runs; the
    # fixture below shares the one the storage lands rows in.
    relay = OutboxRelayImpl(
        outbox or OutboxStorageMemoryImpl(), EventStorageMemoryImpl(), infra.get_topics()
    )
    return build_tenancy(
        storage,
        relay,
        cache or infra.get_cache(CacheScope.REALTIME_TICKET),
        options or TenancyOptions(dev_sign_in=True, totp_encryption_key=TOTP_KEY),
        clock or SteppingClock(),
        identity_provider=IdentityProviderAbsentImpl(),
    )


@pytest.fixture
def clock() -> SteppingClock:
    return SteppingClock()


@pytest.fixture
def manager(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    outbox: OutboxStorageMemoryImpl,
    clock: SteppingClock,
) -> TenancyManagerInterface:
    return make_manager(storage, infra, outbox=outbox, clock=clock)


@pytest.fixture
def operator(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    outbox: OutboxStorageMemoryImpl,
    clock: SteppingClock,
) -> TenancyOperatorManagerImpl:
    events = EventStorageMemoryImpl()
    relay = OutboxRelayImpl(outbox, events, infra.get_topics())
    return TenancyOperatorManagerImpl(
        storage, events, relay, TenancyOperatorOptions(totp_encryption_key=TOTP_KEY), clock
    )


async def token_operator(manager: TenancyManagerInterface, email: str) -> OperatorContext:
    """An operator admitted on the token the grant job mints, for a test about
    what an operator does; the tests of the gate sign in with a code."""
    issued = await manager.grant_operator_token(request(), email)
    return await manager.admit_operator(await manager.authenticate_login(request(), issued.token))


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


async def sign_in(manager: TenancyManagerInterface, email: str, org_id: UUID) -> TenantContext:
    login = await manager.sign_in.dev_sign_in(request(), email)
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org_id
    )
    return await manager.authenticate(request(), issued.token)


async def add_member(
    storage: TenancyStorageMemoryImpl, org_id: UUID, email: str, role: Role
) -> User:
    """Seeds a second member straight into storage."""
    now = utcnow()
    identity_id, user_id = new_id(), new_id()
    await storage.write_identity(
        Identity(
            id=identity_id,
            created_at=now,
            updated_at=now,
            created_by=identity_id,
            updated_by=identity_id,
            email=email,
        )
    )
    user = User(
        id=user_id,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
        identity_id=identity_id,
        email=email,
        display_name=email.split("@")[0].title(),
    )
    await storage.write_user(org_id, user)
    await storage.write_membership(
        org_id,
        Membership(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=user_id,
            updated_by=user_id,
            user_id=user_id,
            role=role,
        ),
    )
    return user


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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
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
    assert ctx.security.credential_kind is CredentialKind.SESSION_TOKEN
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


async def test_a_login_credential_cannot_call_tenant_routes(
    manager: TenancyManagerInterface,
) -> None:
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), login.token)
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), "garbage")


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


async def test_expired_sessions_are_refused(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, session_ttl=timedelta(seconds=-1))
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    ctx = await manager.authenticate(request(), issued.token)
    seen = await storage.read_session(org.id, ctx.credential_id)
    assert seen is not None and seen.last_seen_at is not None
    await manager.authenticate(request(), issued.token)
    again = await storage.read_session(org.id, ctx.credential_id)
    assert again is not None and again.last_seen_at == seen.last_seen_at, "once a minute"
    # Just past the idle lifetime without a request, well inside the absolute one.
    options = TenancyOptions()
    assert options.session_idle_ttl < options.session_ttl
    since = utcnow() - options.session_idle_ttl - timedelta(seconds=1)
    idle = seen.model_copy(update={"last_seen_at": since})
    await storage.write_session(org.id, idle)
    with pytest.raises(CredentialExpired, match="idle"):
        await manager.authenticate(request(), issued.token)
    with pytest.raises(CredentialExpired, match="idle"):
        await manager.authenticate_login(request(), issued.token)


async def test_api_keys_are_role_capped_and_revocable(
    manager: TenancyManagerInterface, infra: InfraLocalImpl
) -> None:
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)

    issued = await manager.credentials.create_api_key(owner, "ci", Role.MEMBER)
    key_ctx = await manager.authenticate(
        request(AppContext(type=AppType.CLI, version="cli@0")), issued.key
    )
    assert key_ctx.security.role is Role.MEMBER
    assert key_ctx.security.credential_kind is CredentialKind.API_KEY
    assert not key_ctx.has(Permission.MANAGE_MEMBERS)

    # An api key mints nothing, at its own role or above: revoking a leaked key
    # has to end the access it gave, and a successor would outlive it.
    for role in (Role.MEMBER, Role.OWNER):
        with pytest.raises(NotAuthorized):
            await manager.credentials.create_api_key(key_ctx, "successor", role)
    assert [
        k.id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ] == [issued.api_key.id]
    revoked = await manager.credentials.revoke_api_key(owner, issued.api_key.id)
    assert revoked.deleted_at is not None
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), issued.key)
    assert [type(p) for p in seen] == [EntityChangedPayload, EntityChangedPayload]
    assert all(p.org_id == org.id for p in seen)


async def test_no_one_mints_a_service_key(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    # The service role holds MANAGE_MEMBERS and a member does not; it is refused
    # by name before the ladder is asked, for the owner as for the member.
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    owner = await sign_in(manager, "ann@example.test", org.id)
    member = await sign_in(manager, "bob@example.test", org.id)
    assert not member.has(Permission.MANAGE_MEMBERS)
    for ctx in (member, owner):
        with pytest.raises(ValidationFailed):
            await manager.credentials.create_api_key(ctx, "svc", Role.SERVICE)
    assert (await manager.credentials.get_api_keys(owner, None, limit=10)).items == ()


async def test_a_rerun_of_the_create_reissues_the_secret_on_the_same_key(
    manager: TenancyManagerInterface, infra: InfraLocalImpl, markers: IdempotencyStorageMemoryImpl
) -> None:
    seen: list[TopicPayload] = []

    async def record(payload: TopicPayload) -> None:
        seen.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    api_key_id = new_id()
    attempt = await begin_attempt(markers, owner, "retried", api_key_id)
    first = await manager.credentials.create_api_key(owner, "ci", Role.MEMBER, attempt=attempt)
    assert (await manager.authenticate(request(), first.key)).credential_id == api_key_id

    # The retry that took over an abandoned marker runs the create again on
    # the id the marker carries: same row, fresh secret, and the first secret,
    # which reached no one, stops authenticating.
    taken = await markers.take_over_pending(
        owner.org_id, owner.user_id, "retried", lease_bound(utcnow()), new_id()
    )
    assert taken is not None and taken.attempt_id is not None
    retry = Attempt(target_id=taken.target_id, attempt_id=taken.attempt_id)
    again = await manager.credentials.create_api_key(owner, "ci", Role.MEMBER, attempt=retry)
    assert again.key != first.key
    assert again.api_key.id == api_key_id
    assert again.api_key.created_at == first.api_key.created_at
    assert (await manager.authenticate(request(), again.key)).credential_id == api_key_id
    with pytest.raises(InvalidCredential):
        await manager.authenticate(request(), first.key)
    assert [
        k.id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ] == [api_key_id]
    assert len(seen) == 1, "the key was announced once"

    # The attempt the retry took the marker from is a zombie: its re-mint is
    # refused, and the secret the caller is holding goes on authenticating.
    with pytest.raises(Conflict):
        await manager.credentials.create_api_key(owner, "ci", Role.MEMBER, attempt=attempt)
    assert (await manager.authenticate(request(), again.key)).credential_id == api_key_id


async def test_api_key_ttl_is_bounded_by_the_option(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, api_key_ttl=timedelta(days=30))
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)

    with pytest.raises(ValidationFailed):
        await manager.credentials.create_api_key(owner, "too-long", Role.MEMBER, timedelta(days=31))
    with pytest.raises(ValidationFailed):
        await manager.credentials.create_api_key(owner, "instant", Role.MEMBER, timedelta(0))
    issued = await manager.credentials.create_api_key(
        owner, "short", Role.MEMBER, timedelta(days=7)
    )
    assert issued.api_key.expires_at - issued.api_key.created_at == timedelta(days=7)
    default = await manager.credentials.create_api_key(owner, "default", Role.MEMBER)
    assert default.api_key.expires_at - default.api_key.created_at == timedelta(days=30)


async def test_member_roles_are_capped_at_the_callers_role(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    bob = await add_member(storage, org.id, "bob@example.test", Role.ADMIN)
    cid = await add_member(storage, org.id, "cid@example.test", Role.VIEWER)
    admin = await sign_in(manager, "bob@example.test", org.id)

    promoted = await manager.members.update_membership_role(admin, cid.id, Role.MEMBER)
    assert promoted.role is Role.MEMBER
    assert promoted.updated_at > promoted.created_at
    assert (await sign_in(manager, "cid@example.test", org.id)).security.role is Role.MEMBER

    with pytest.raises(NotAuthorized):
        await manager.members.update_membership_role(admin, cid.id, Role.OWNER)
    with pytest.raises(NotAuthorized):
        await manager.members.update_membership_role(admin, owner.user_id, Role.VIEWER)
    with pytest.raises(ValidationFailed):
        await manager.members.update_membership_role(admin, bob.id, Role.MEMBER)
    with pytest.raises(ValidationFailed):
        await manager.members.update_membership_role(owner, cid.id, Role.SERVICE)
    with pytest.raises(NotFound):
        await manager.members.update_membership_role(owner, new_id(), Role.MEMBER)
    member = await sign_in(manager, "cid@example.test", org.id)
    with pytest.raises(NotAuthorized):
        await manager.members.update_membership_role(member, bob.id, Role.VIEWER)


async def test_removing_a_member_soft_deletes_the_user_and_ends_access(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    bob = await add_member(storage, org.id, "bob@example.test", Role.ADMIN)
    cid = await add_member(storage, org.id, "cid@example.test", Role.MEMBER)
    admin = await sign_in(manager, "bob@example.test", org.id)
    cid_login = await manager.sign_in.dev_sign_in(request(), "cid@example.test")
    cid_session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), cid_login.token), org.id
    )
    member = await manager.authenticate(request(), cid_session.token)

    with pytest.raises(NotAuthorized):
        await manager.members.remove_member(admin, owner.user_id)
    with pytest.raises(ValidationFailed):
        await manager.members.remove_member(admin, bob.id)
    with pytest.raises(NotAuthorized):
        await manager.members.remove_member(member, bob.id)

    removed = await manager.members.remove_member(admin, cid.id)
    assert removed.deleted_at is not None and removed.deleted_by == admin.user_id
    assert removed.updated_at == removed.deleted_at
    assert [u.id for u in (await manager.members.get_users(owner, None, limit=10)).items] == sorted(
        [owner.user_id, bob.id]
    )
    with pytest.raises(CredentialExpired):  # revoked with the member
        await manager.authenticate(request(), cid_session.token)
    # Removed from the team, Cid keeps the one place nobody removes him from.
    left = (await manager.sign_in.dev_sign_in(request(), "cid@example.test")).memberships
    assert [m.org.kind for m in left] == [OrgKind.PERSONAL]
    with pytest.raises(NotFound):
        await manager.members.remove_member(admin, cid.id)
    # The membership ended with the member: no list shows it, no role change reaches it.
    assert [
        m.user_id for m in (await manager.members.get_memberships(owner, None, limit=10)).items
    ] == sorted([owner.user_id, bob.id])
    with pytest.raises(NotFound):
        await manager.members.update_membership_role(owner, cid.id, Role.VIEWER)
    ended = await storage.read_membership_for_user(org.id, cid.id)
    assert ended is None


async def test_removing_a_member_revokes_their_credentials_and_announces_each(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    relay = SpyRelay(OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    bob = await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    bobs = await sign_in(manager, "bob@example.test", org.id)
    other = await sign_in(manager, "bob@example.test", org.id)
    key = await manager.credentials.create_api_key(bobs, "ci", Role.MEMBER)
    assert key.api_key.id in [
        k.id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ]

    await manager.members.remove_member(owner, bob.id)
    # No key of theirs stays listed for a manager, and no session of theirs is live.
    assert [
        k.user_id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ] == []
    assert await storage.read_sessions(org.id, bob.id, utcnow(), 10) == []
    stored = await storage.read_api_key(org.id, key.api_key.id)
    assert stored is not None and stored.deleted_at is not None
    assert stored.deleted_by == owner.user_id
    # The removal is announced first, so their sockets close as a membership
    # that ended; then each revocation, the way a revocation is.
    kinds = [(r.kind, r.target_id) for _, r in relay.rows if r.actor_id == owner.user_id]
    assert kinds[0] == ("tenancy.user.deleted", bob.id)
    assert sorted(kinds[1:]) == sorted(
        [
            ("tenancy.session.revoked", bobs.security.credential_id),
            ("tenancy.session.revoked", other.security.credential_id),
            ("tenancy.api_key.deleted", key.api_key.id),
        ]
    )
    assert await claim_all(outbox) == []


async def test_no_event_about_a_user_carries_who_they_are(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The stream outlives a removed member; the purge that erases a person
    reaches the user row and not the stream, so the stream never holds the
    email or the name."""
    relay = SpyRelay(OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    _, bob, _ = await manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    await manager.members.remove_member(owner, bob.id)
    about_users = [r for _, r in relay.rows if r.kind.startswith("tenancy.user.")]
    assert [r.kind for r in about_users] == ["tenancy.user.created", "tenancy.user.deleted"]
    for row in about_users:
        assert not PERSONAL_FIELDS & set(row.payload), row.kind
        assert row.payload == {"identity_id": str(bob.identity_id)}, "ids only"


class DownOnRemoveStorage(TenancyStorageMemoryImpl):
    """The writes that end a member fail while `down`: the twin of a database
    that went away between two statements."""

    down = True

    async def write_user(
        self, org_id: UUID, user: User, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        if self.down and user.deleted_at is not None:
            raise RuntimeError("storage is down")
        await super().write_user(org_id, user, outbox_rows)

    async def remove_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        revocation_row: Callable[[str, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        if self.down:
            raise RuntimeError("storage is down")
        return await super().remove_member(org_id, user, membership, outbox_rows, revocation_row)


async def test_a_removal_that_fails_leaves_the_member_whole(
    infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    # The user, the membership, and their credentials go in one write: a
    # failure leaves all of them, so the member is still listed, still a
    # member, still signed in, and the next remove finishes the job.
    storage = DownOnRemoveStorage(outbox)
    manager = make_manager(storage, infra, outbox=outbox)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    cid = await add_member(storage, org.id, "cid@example.test", Role.MEMBER)
    cids = await sign_in(manager, "cid@example.test", org.id)
    with pytest.raises(RuntimeError):
        await manager.members.remove_member(owner, cid.id)
    session = await storage.read_session(org.id, cids.credential_id)
    assert session is not None and session.revoked_at is None, "still signed in"
    assert cid.id in [u.id for u in (await manager.members.get_users(owner, None, limit=10)).items]
    assert cid.id in [
        m.user_id for m in (await manager.members.get_memberships(owner, None, limit=10)).items
    ]
    _, _, created = await manager.add_member(
        request(), "ajax", "cid@example.test", "Cid", Role.MEMBER
    )
    assert not created, "still a member"
    storage.down = False
    removed = await manager.members.remove_member(owner, cid.id)
    assert removed.deleted_at is not None
    assert cid.id not in [
        u.id for u in (await manager.members.get_users(owner, None, limit=10)).items
    ]
    assert await storage.read_membership_for_user(org.id, cid.id) is None


async def test_a_membership_needs_a_live_user_to_be_read_or_changed(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    cid = await add_member(storage, org.id, "cid@example.test", Role.MEMBER)
    # The user row goes while the membership row stays live: the membership is
    # still not one to list or change.
    now = utcnow()
    await storage.write_user(
        org.id, cid.model_copy(update={"deleted_at": now, "deleted_by": owner.user_id})
    )
    with pytest.raises(NotFound):
        await manager.members.update_membership_role(owner, cid.id, Role.VIEWER)
    with pytest.raises(NotFound):
        await manager.members.remove_member(owner, cid.id)


async def test_users_update_their_own_display_name(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    await add_member(storage, org.id, "cid@example.test", Role.VIEWER)
    viewer = await sign_in(manager, "cid@example.test", org.id)

    # The context carries the user's id; the entity is loaded by the manager.
    renamed = await manager.members.rename_user(viewer, viewer.user_id, "Cid R.")
    assert renamed.display_name == "Cid R." and renamed.updated_at > renamed.created_at
    assert renamed.updated_by == viewer.user_id
    assert await manager.members.get_user(viewer, viewer.user_id) == renamed

    with pytest.raises(NotAuthorized):
        await manager.members.rename_user(viewer, owner.user_id, "Nope")
    with pytest.raises(ValidationFailed):
        await manager.members.rename_user(viewer, viewer.user_id, "  ")
    by_owner = await manager.members.rename_user(owner, viewer.user_id, "Cid")
    assert by_owner.display_name == "Cid" and by_owner.email == "cid@example.test"


async def test_sessions_are_listed_revoked_and_logged_out(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    # Two sign-ins, since each makes one session: two tabs of one person.
    first, second = [
        await manager.sign_in.exchange_login(
            await manager.authenticate_login(
                request(), (await manager.sign_in.dev_sign_in(request(), "ann@example.test")).token
            ),
            org.id,
        )
        for _ in range(2)
    ]
    ctx = await manager.authenticate(request(), first.token)
    other = await manager.authenticate(request(), second.token)

    sessions = await manager.credentials.get_sessions(ctx, limit=10)
    assert {s.id for s in sessions} == {ctx.security.credential_id, other.security.credential_id}
    assert all(s.credential_kind is CredentialKind.SESSION_TOKEN for s in sessions)

    revoked = await manager.credentials.revoke_session(ctx, other.security.credential_id)
    assert revoked.revoked_at is not None and revoked.updated_at == revoked.revoked_at
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), second.token)
    assert [s.id for s in await manager.credentials.get_sessions(ctx, limit=10)] == [
        ctx.security.credential_id
    ]
    with pytest.raises(NotFound):
        await manager.credentials.revoke_session(ctx, other.security.credential_id)

    await add_member(storage, org.id, "cid@example.test", Role.VIEWER)
    viewer = await sign_in(manager, "cid@example.test", org.id)
    with pytest.raises(NotAuthorized):
        await manager.credentials.revoke_session(viewer, ctx.security.credential_id)
    assert await manager.credentials.get_sessions(viewer, limit=10) != sessions

    issued = await manager.credentials.create_api_key(ctx, "ci", Role.MEMBER)
    # An api key has no sign-out: it never proves an identity, and its holder
    # revokes it.
    with pytest.raises(InvalidCredential):
        await manager.authenticate_login(request(), issued.key)

    signed_out = await manager.sign_in.logout(
        await manager.authenticate_login(request(), first.token)
    )
    out = signed_out.session
    assert out.id == ctx.security.credential_id and out.revoked_at is not None
    # The local sign-in leaves no session at the provider to end.
    assert signed_out.provider_logout_url is None
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), first.token)


class SpyRelay(OutboxRelayInterface):
    """The real relay, with every row it was handed kept for the assertions."""

    def __init__(self, relay: OutboxRelayImpl) -> None:
        self._relay = relay
        self.rows: list[tuple[UUID, OutboxRow]] = []

    async def relay(self, org_id: UUID, row: OutboxRow) -> bool:
        self.rows.append((org_id, row))
        return await self._relay.relay(org_id, row)

    async def relay_all(self, org_id: UUID, rows: Sequence[OutboxRow]) -> bool:
        self.rows.extend((org_id, row) for row in rows)
        return await self._relay.relay_all(org_id, rows)

    async def relay_pending(self, limit: int) -> int:
        return await self._relay.relay_pending(limit)

    async def purge_done(self, retention: timedelta, limit: int) -> int:
        return await self._relay.purge_done(retention, limit)

    async def oldest_pending_age(self) -> timedelta:
        return await self._relay.oldest_pending_age()

    async def failed_within(self, window: timedelta) -> int:
        return await self._relay.failed_within(window)

    def hold(self, request_id: UUID) -> None:
        self._relay.hold(request_id)

    def held(self, request_id: UUID) -> int:
        return self._relay.held(request_id)

    async def release(self, request_id: UUID) -> int:
        return await self._relay.release(request_id)

    def abandon(self, request_id: UUID) -> int:
        return self._relay.abandon(request_id)


async def test_revoking_a_session_announces_it_on_the_bus_without_its_token(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The socket that session opened lives in some process; the revocation
    reaches it as any change does, an outbox row the relay publishes. The
    event is a record, not a credential: its snapshot has no token hash."""
    relay = SpyRelay(OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
    )
    published: list[TopicPayload] = []

    async def hear(payload: TopicPayload) -> None:
        published.append(payload)

    infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", hear)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    other = await sign_in(manager, "ann@example.test", org.id)

    revoked = await manager.credentials.revoke_session(ctx, other.security.credential_id)
    row = next(r for _, r in relay.rows if r.kind == "tenancy.session.revoked")
    assert row.target_id == revoked.id and row.actor_id == ctx.user_id
    assert "token_hash" not in row.payload
    assert row.payload == {"user_id": str(ctx.user_id)}, "ids only"
    frames = [p for p in published if isinstance(p, EntityChangedPayload)]
    assert [(f.kind, f.target_id, f.org_id) for f in frames if f.kind.startswith("tenancy.se")] == [
        ("tenancy.session.revoked", revoked.id, org.id)
    ]

    # Logging out is the same revocation, announced the same way.
    third = await manager.sign_in.exchange_login(
        await manager.authenticate_login(
            request(), (await manager.sign_in.dev_sign_in(request(), "ann@example.test")).token
        ),
        org.id,
    )
    out = (
        await manager.sign_in.logout(await manager.authenticate_login(request(), third.token))
    ).session
    logged_out = next(r for _, r in relay.rows if r.target_id == out.id)
    assert logged_out.actor_id == ctx.user_id and logged_out.org_id == org.id
    assert [r.target_id for _, r in relay.rows if r.kind == "tenancy.session.revoked"] == [
        revoked.id,
        out.id,
    ]


async def test_a_page_of_dead_sessions_never_hides_a_live_one(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, max_limit=1), outbox=outbox
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    older = await sign_in(manager, "ann@example.test", org.id)
    newer = await sign_in(manager, "ann@example.test", org.id)
    stale = await storage.read_session(org.id, older.security.credential_id)
    assert stale is not None
    await storage.write_session(
        org.id, stale.model_copy(update={"expires_at": utcnow() - timedelta(seconds=1)})
    )
    # The page holds one session; the expired one must not be it.
    assert [s.id for s in await manager.credentials.get_sessions(newer, limit=10)] == [
        newer.security.credential_id
    ]


async def test_a_members_own_keys_are_found_behind_a_page_of_others(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, max_limit=2), outbox=outbox
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    owner = await sign_in(manager, "ann@example.test", org.id)
    await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    bob = await sign_in(manager, "bob@example.test", org.id)
    anns = [await manager.credentials.create_api_key(owner, "ci", Role.MEMBER) for _ in range(2)]
    own = await manager.credentials.create_api_key(bob, "mine", Role.MEMBER)
    # Bob sees his own key though the page is full of Ann's older ones.
    assert [k.id for k in (await manager.credentials.get_api_keys(bob, None, limit=10)).items] == [
        own.api_key.id
    ]
    # The member manager sees the tenant's newest page.
    assert [
        k.id for k in (await manager.credentials.get_api_keys(owner, None, limit=10)).items
    ] == [
        own.api_key.id,
        anns[1].api_key.id,
    ]


async def test_the_identity_behind_the_caller(manager: TenancyManagerInterface) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    identity = await manager.org.get_identity(ctx)
    assert identity.id == (await manager.members.get_user(ctx, ctx.user_id)).identity_id
    assert identity.email == "ann@example.test" and identity.operator_role is None


async def seed_operator(
    manager: TenancyManagerInterface, email: str, role: OperatorRole = OperatorRole.WRITE
) -> None:
    await manager.bootstrap(request(), email, email.split("@")[0], email, "Op", operator_role=role)


async def admitted_on_login(
    manager: TenancyManagerInterface, email: str, code: str | None = None
) -> OperatorContext:
    if code is None:
        login = await manager.sign_in.dev_sign_in(request(), email)
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
    await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    with pytest.raises(ValidationFailed):
        await second_factor(manager, "ann@example.test", "123456")


async def test_a_process_without_the_totp_key_refuses_to_enrol(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    manager = make_manager(storage, infra, TenancyOptions(dev_sign_in=True), outbox=outbox)
    operator = TenancyOperatorManagerImpl(
        storage,
        EventStorageMemoryImpl(),
        OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics()),
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
    found = await storage.read_session_by_digest(hash_token(issued.token))
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
    row = await storage.read_session_by_digest(hash_token(short.token))
    assert row is not None
    await storage.write_session(
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
    row = await storage.read_session_by_id(issued.id)
    assert row is not None and row[1].token_hash == hash_token(issued.token)
    assert row[1].created_by == minting.identity_id
    ended = await storage.read_session_by_id(minting.credential_id)
    assert ended is not None and ended[1].revoked_at is not None
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), login.token)
    with pytest.raises(CredentialExpired):
        await operator.issue_operator_token(minting, OperatorRole.READ)
    # One sign-in, one token: the enrolment's sign-in minted the other one.
    listed = await operator.get_operator_tokens(admin, None, limit=10)
    assert [t.id for t in listed.items] == [issued.id, admin.credential_id]


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


async def test_a_sign_out_ends_the_credential_presented_whichever_it_is(
    manager: TenancyManagerInterface, operator: TenancyOperatorManagerImpl, clock: SteppingClock
) -> None:
    """A sign-in with no code, a sign-in with its code, and an operator token
    each end on their own sign-out; the next use of each is refused."""
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    picker = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    out = await manager.sign_in.logout(await manager.authenticate_login(request(), picker.token))
    assert out.session.credential_kind is CredentialKind.LOGIN and out.session.revoked_at
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
    assert signed_out.session.id == token.id and signed_out.provider_logout_url is None
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), token.token)
    assert token.id not in {
        t.id for t in (await operator.get_operator_tokens(admin, None, limit=10)).items
    }


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
            request(), (await manager.sign_in.dev_sign_in(request(), "ann@example.test")).token
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
    assert reads == ["read_session_by_digest", "read_principal"]
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
        assert reads == ["read_session_with_identity_by_digest"]


async def test_the_grant_job_puts_an_identity_on_the_allowlist_and_audits_it(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    relay = SpyRelay(OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics()))
    manager = build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True),
        identity_provider=IdentityProviderAbsentImpl(),
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
    # The platform's own identities are made by the first grant, with no org
    # and no way to sign in; nobody signs in as one.
    provisioner = await manager.grant_operator(
        request(), "provisioner@platform.acme.invalid", OperatorRole.WRITE
    )
    assert provisioner.operator_role is OperatorRole.WRITE
    with pytest.raises(ValidationFailed):
        await manager.sign_in.dev_sign_in(request(), "smoke@platform.acme.invalid")
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
    the platform's own identity is kept folded and found the same way."""
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
    signed_up = await manager.sign_in.dev_sign_in(request(), "cid@example.test")
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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
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
    left = (await manager.sign_in.dev_sign_in(request(), "ann@example.test")).memberships
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
    no_retention = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, retention=timedelta(0))
    )
    assert await no_retention.purge_tenant(sweep) == 5, "user, membership, key, session, ticket"
    assert await storage.read_users(org.id, None, limit=10) == []
    assert await storage.read_memberships(org.id, limit=10) == []
    assert await storage.read_api_keys(org.id, None, limit=10) == []
    assert await storage.read_sessions(org.id, ann.user_id, utcnow(), 10) == []
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
        await manager.resume(request(), org.id, CredentialKind.SESSION_TOKEN, new_id())


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
    no_retention = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, retention=timedelta(0))
    )
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
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, login_ttl=timedelta(seconds=-1))
    )
    await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    found = await storage.read_session_by_digest(hash_token(login.token))
    assert found is not None and found[0] == EMPTY_UUID
    system = next(c for c in await manager.service_contexts(request()) if c.org_id == EMPTY_UUID)
    assert system.user_id == EMPTY_UUID and system.role is Role.SERVICE
    assert await manager.purge_across_tenants() == 0, "retention has not passed"
    no_retention = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, retention=timedelta(0))
    )
    assert await no_retention.purge_tenant(system) == 0, "the system scope never expires"
    assert await no_retention.purge_across_tenants() == 1
    assert await storage.read_session_by_digest(hash_token(login.token)) is None


async def test_expired_api_keys_are_purged_like_revoked_ones(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, api_key_ttl=timedelta(seconds=1))
    )
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ctx = await sign_in(manager, "ann@example.test", org.id)
    expired = await manager.credentials.create_api_key(
        ctx, "short", Role.MEMBER, ttl=timedelta(seconds=1)
    )
    await asyncio.sleep(1.01)
    assert [k.id for k in (await manager.credentials.get_api_keys(ctx, None, limit=10)).items] == [
        expired.api_key.id
    ]
    no_retention = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, retention=timedelta(0))
    )
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
    session = await storage.read_session(org.id, ctx.security.credential_id)
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
    assert principal.credential_kind is CredentialKind.SESSION_TOKEN
    assert principal.membership_id == membership.id

    session = await storage.read_session(org.id, ctx.security.credential_id)
    assert session is not None
    seen = utcnow() - timedelta(hours=1)
    await storage.write_session(org.id, session.model_copy(update={"last_seen_at": seen}))
    again = await manager.resume(
        request(), org.id, principal.credential_kind, ctx.security.credential_id, record_use=False
    )
    assert again.ctx.security == principal.ctx.security
    session = await storage.read_session(org.id, ctx.security.credential_id)
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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
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
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, ticket_ttl=timedelta(seconds=-1))
    )
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

    login = await manager.sign_in.dev_sign_in(request(), "bob@example.test")
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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
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


async def test_every_api_key_is_reachable_a_page_at_a_time(
    manager: TenancyManagerInterface,
) -> None:
    """A list answers a page and says whether another follows; the page a
    caller asks for is a page size, never a ceiling past which a live key
    stops being listed and so cannot be revoked."""
    owner, _ = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    issued = [
        await manager.credentials.create_api_key(owner, f"key-{i}", Role.MEMBER) for i in range(7)
    ]
    newest_first = [k.api_key.id for k in issued][::-1]
    paged: list[UUID] = []
    after: UUID | None = None
    while True:
        page = await manager.credentials.get_api_keys(owner, after, limit=3)
        paged += [k.id for k in page.items]
        if not page.has_more:
            break
        after = page.items[-1].id
    assert paged == newest_first
    # The oldest key is on the last page, not lost behind the first.
    last = await manager.credentials.get_api_keys(owner, newest_first[-2], limit=3)
    assert [k.id for k in last.items] == [newest_first[-1]] and not last.has_more


async def test_every_member_is_reachable_a_page_at_a_time(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """The member list pages the same way, so every member is reachable,
    not only whoever the first page happened to hold."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    for index in range(4):
        await add_member(storage, org.id, f"member-{index}@example.test", Role.MEMBER)
    owner = await sign_in(manager, "ann@example.test", org.id)
    every = sorted(u.id for u in (await manager.members.get_users(owner, None, limit=50)).items)
    assert len(every) == 5
    paged: list[UUID] = []
    after: UUID | None = None
    while True:
        page = await manager.members.get_users(owner, after, limit=2)
        paged += [u.id for u in page.items]
        if not page.has_more:
            break
        after = page.items[-1].id
    assert paged == every


async def test_memberships_pair_with_members_page_for_page(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    """A page of memberships read with the cursor and limit of a page of users
    carries exactly those members' roles, so no member of a large org is
    listed without one."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    for index in range(4):
        await add_member(storage, org.id, f"member-{index}@example.test", Role.MEMBER)
    owner = await sign_in(manager, "ann@example.test", org.id)
    after: UUID | None = None
    pages = 0
    while True:
        users = await manager.members.get_users(owner, after, limit=2)
        roles = await manager.members.get_memberships(owner, after, limit=2)
        assert [m.user_id for m in roles.items] == [u.id for u in users.items]
        assert roles.has_more == users.has_more
        pages += 1
        if not users.has_more:
            break
        after = users.items[-1].id
    assert pages == 3


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
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, max_orgs_per_identity=3), outbox=outbox
    )
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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
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
        await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    with pytest.raises(MembershipLimitReached):
        await manager.sign_in.exchange_login(ictx, first.id)


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


# A team org of one's own.


async def signed_up(manager: TenancyManagerInterface, email: str, name: str) -> TenantContext:
    """A person who signed in the first time, in a session in their personal org."""
    issued = await manager.sign_in.dev_sign_in(request(), email, name)
    return await sign_in_with(manager, email, issued.memberships[0].org.id)


async def sign_in_with(manager: TenancyManagerInterface, email: str, org_id: UUID) -> TenantContext:
    login = await manager.sign_in.dev_sign_in(request(), email)
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org_id
    )
    return await manager.authenticate(request(), session.token)


async def test_a_signed_in_person_creates_a_team_org_and_switches_to_it(
    manager: TenancyManagerInterface,
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    place = await manager.org.create_org(dee, "Dee's Bakery", None)
    assert place.org.kind is OrgKind.TEAM and place.org.personal_identity_id is None
    assert place.org.name == "Dee's Bakery" and place.org.slug.startswith("dee-s-bakery-")
    assert place.role is Role.OWNER and place.user.display_name == "Dee"
    assert place.org.created_by == place.user.id
    # The session stays where it was; the exchange is the switch.
    assert dee.org_id != place.org.id
    login = await manager.sign_in.dev_sign_in(request(), "dee@example.test")
    assert {m.org.kind for m in login.memberships} == {OrgKind.PERSONAL, OrgKind.TEAM}
    there = await sign_in_with(manager, "dee@example.test", place.org.id)
    assert there.org_id == place.org.id and there.security.role is Role.OWNER
    # A slug the person typed is kept, and a taken one is refused.
    named = await manager.org.create_org(dee, "Cafe", "cafe")
    assert named.org.slug == "cafe"
    with pytest.raises(Conflict):
        await manager.org.create_org(dee, "Cafe Two", "cafe")


@pytest.mark.parametrize(("name", "slug"), [("  ", None), ("Cafe", "Cafe"), ("Cafe", "-cafe")])
async def test_a_malformed_team_org_is_refused(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl, name: str, slug: str | None
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    with pytest.raises(ValidationFailed):
        await manager.org.create_org(dee, name, slug)
    assert await storage.count_orgs() == 1


async def test_only_a_session_creates_an_org(manager: TenancyManagerInterface) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    key = await manager.credentials.create_api_key(dee, "bot", Role.OWNER)
    bot = await manager.authenticate(request(), key.key)
    with pytest.raises(NotAuthorized):
        await manager.org.create_org(bot, "Bots", None)


async def test_a_rerun_of_a_create_answers_with_the_org_it_made(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    attempt = Attempt(target_id=new_id(), attempt_id=new_id())
    first = await manager.org.create_org(dee, "Bakery", None, attempt)
    again = await manager.org.create_org(dee, "Bakery", None, attempt)
    assert again.org.id == first.org.id == attempt.target_id
    assert again.user.id == first.user.id
    assert await storage.count_orgs() == 2


async def test_a_team_org_counts_toward_the_bound(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    manager = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, max_orgs_per_identity=2)
    )
    dee = await signed_up(manager, "dee@example.test", "Dee")
    await manager.org.create_org(dee, "One", None)
    with pytest.raises(MembershipLimitReached):
        await manager.org.create_org(dee, "Two", None)


# A personal org stays its person's.


async def test_the_person_of_a_personal_org_is_never_removed_or_demoted(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    dee = await signed_up(manager, "dee@example.test", "Dee")
    # Anyone may be in a personal org; nothing refuses a second person.
    other = await add_member(storage, dee.org_id, "eve@example.test", Role.OWNER)
    eve = await sign_in(manager, "eve@example.test", dee.org_id)
    with pytest.raises(PersonalOrgFixed) as refused:
        await manager.members.remove_member(eve, dee.user_id)
    assert refused.value.http_status == 409
    with pytest.raises(PersonalOrgFixed):
        await manager.members.update_membership_role(eve, dee.user_id, Role.VIEWER)
    # Another member of it is an ordinary member.
    changed = await manager.members.update_membership_role(dee, other.id, Role.MEMBER)
    assert changed.role is Role.MEMBER
    await manager.members.remove_member(dee, other.id)
    # And the person cannot leave it either: nobody removes themselves.
    with pytest.raises(ValidationFailed):
        await manager.members.remove_member(dee, dee.user_id)


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
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org.id
    )
    ictx = await manager.authenticate_login(request(), session.token)
    assert ictx.email == "ann@example.test"
    assert ictx.credential_kind is CredentialKind.SESSION_TOKEN
    await manager.sign_in.logout(ictx)
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), session.token)
    with pytest.raises(InvalidCredential):
        await manager.authenticate_login(request(), "ses_never-issued")


async def test_an_expired_session_or_a_removed_members_proves_no_identity(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl
) -> None:
    expiring = make_manager(
        storage, infra, TenancyOptions(dev_sign_in=True, session_ttl=timedelta(seconds=-1))
    )
    manager = make_manager(storage, infra)
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    login = await manager.sign_in.dev_sign_in(request(), "ann@example.test")
    ictx = await manager.authenticate_login(request(), login.token)
    stale = await expiring.sign_in.exchange_login(ictx, org.id)
    with pytest.raises(CredentialExpired):
        await manager.authenticate_login(request(), stale.token)
    # A member removed from the org: the session they held proves nothing.
    bob = await add_member(storage, org.id, "bob@example.test", Role.MEMBER)
    bob_login = await manager.sign_in.dev_sign_in(request(), "bob@example.test")
    bob_session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), bob_login.token), org.id
    )
    owner = await sign_in(manager, "ann@example.test", org.id)
    await manager.members.remove_member(owner, bob.id)
    with pytest.raises((CredentialExpired, InvalidCredential)):
        await manager.authenticate_login(request(), bob_session.token)


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
    relay = SpyRelay(OutboxRelayImpl(outbox, EventStorageMemoryImpl(), infra.get_topics()))
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
    revoked = [(org_id, r) for org_id, r in relay.rows if r.kind == "tenancy.session.revoked"]
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

    async def exchange(token: str, org_id: UUID) -> IssuedSession:
        return await manager.sign_in.exchange_login(
            await manager.authenticate_login(request(), token), org_id
        )

    async def deadline(issued: IssuedSession) -> datetime:
        """The deadline on the row every request reads, which is the one issued."""
        ctx = await manager.authenticate(request(), issued.token)
        row = await storage.read_session(ctx.org_id, ctx.security.credential_id)
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
    home = (await manager.org.delete_org(owner, "Ajax")).session
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
        storage, infra, TenancyOptions(dev_sign_in=True, session_ttl=timedelta(days=1))
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
    ended = await storage.read_session_by_id(ictx.credential_id)
    assert ended is not None and ended[0] == EMPTY_UUID
    assert ended[1].revoked_at == ended[1].updated_at is not None
    # A second exchange, a replay or a retry after a lost answer, lands nothing.
    with pytest.raises(CredentialExpired, match="sign in again"):
        await manager.sign_in.exchange_login(ictx, ajax.id)
    # Presented again, the sign-in proves nothing any more, the list of
    # places included; the session is what the person holds.
    with pytest.raises(CredentialExpired, match="sign in again"):
        await manager.authenticate_login(request(), login.token)
    assert [s.id for s in await manager.credentials.get_sessions(ctx, limit=10)] == [
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
    assert [s.id for s in await manager.credentials.get_sessions(ctx, limit=10)] == [
        ctx.security.credential_id
    ]


async def test_a_person_records_their_time_zone_on_their_identity(
    manager: TenancyManagerInterface,
) -> None:
    """The zone is the person's, on their identity: set by them, and refused
    when it is not an IANA name, which leaves the one they had."""
    ann, _ = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    assert (await manager.org.get_identity(ann)).time_zone is None
    identity = await manager.org.set_time_zone(ann, "Asia/Tokyo")
    assert identity.time_zone == "Asia/Tokyo"
    with pytest.raises(ValidationFailed):
        await manager.org.set_time_zone(ann, "+09:00")
    assert (await manager.org.get_identity(ann)).time_zone == "Asia/Tokyo"


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
