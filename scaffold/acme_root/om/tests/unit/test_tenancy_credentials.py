"""The credentials delegate over the memory storage: the api keys a
member creates, pages, and revokes, and the sessions a person lists and
revokes, each revocation announced."""

from datetime import timedelta
from uuid import UUID

import pytest

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import new_id, utcnow
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl
from acme.om.context import AppContext, AppType, CredentialKind, Permission, Role, TenantContext
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    InvalidCredential,
    NotAuthorized,
    NotFound,
    ValidationFailed,
)
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.idempotency.types.attempt import Attempt, lease_bound
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl

from contracts.tenancy import SpyRelay, add_member, make_manager, request, sign_in


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

    sessions = await manager.credentials.get_auth_sessions(ctx, limit=10)
    assert {s.id for s in sessions} == {ctx.security.credential_id, other.security.credential_id}
    assert all(s.credential_kind is CredentialKind.AUTH_SESSION_TOKEN for s in sessions)

    revoked = await manager.credentials.revoke_auth_session(ctx, other.security.credential_id)
    assert revoked.revoked_at is not None and revoked.updated_at == revoked.revoked_at
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), second.token)
    assert [s.id for s in await manager.credentials.get_auth_sessions(ctx, limit=10)] == [
        ctx.security.credential_id
    ]
    with pytest.raises(NotFound):
        await manager.credentials.revoke_auth_session(ctx, other.security.credential_id)

    await add_member(storage, org.id, "cid@example.test", Role.VIEWER)
    viewer = await sign_in(manager, "cid@example.test", org.id)
    with pytest.raises(NotAuthorized):
        await manager.credentials.revoke_auth_session(viewer, ctx.security.credential_id)
    assert await manager.credentials.get_auth_sessions(viewer, limit=10) != sessions

    issued = await manager.credentials.create_api_key(ctx, "ci", Role.MEMBER)
    # An api key has no sign-out: it never proves an identity, and its holder
    # revokes it.
    with pytest.raises(InvalidCredential):
        await manager.authenticate_login(request(), issued.key)

    signed_out = await manager.sign_in.logout(
        await manager.authenticate_login(request(), first.token)
    )
    out = signed_out.auth_session
    assert out.id == ctx.security.credential_id and out.revoked_at is not None
    # The local sign-in leaves no session at the provider to end.
    assert signed_out.provider_logout_url is None
    with pytest.raises(CredentialExpired):
        await manager.authenticate(request(), first.token)


async def test_revoking_a_session_announces_it_on_the_bus_without_its_token(
    storage: TenancyStorageMemoryImpl, infra: InfraLocalImpl, outbox: OutboxStorageMemoryImpl
) -> None:
    """The socket that session opened lives in some process; the revocation
    reaches it as any change does, an outbox row the relay publishes. The
    event is a record, not a credential: its snapshot has no token hash."""
    relay = SpyRelay(OutboxRelayImpl(outbox, ChangeEventStorageMemoryImpl(), infra.get_topics()))
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

    revoked = await manager.credentials.revoke_auth_session(ctx, other.security.credential_id)
    row = next(r for _, r in relay.rows if r.kind == "tenancy.auth_session.revoked")
    assert row.target_id == revoked.id and row.actor_id == ctx.user_id
    assert "token_hash" not in row.payload
    assert row.payload == {"user_id": str(ctx.user_id)}, "ids only"
    frames = [p for p in published if isinstance(p, EntityChangedPayload)]
    assert [
        (f.kind, f.target_id, f.org_id)
        for f in frames
        if f.kind.startswith("tenancy.auth_session.")
    ] == [("tenancy.auth_session.revoked", revoked.id, org.id)]

    # Logging out is the same revocation, announced the same way.
    third = await manager.sign_in.exchange_login(
        await manager.authenticate_login(
            request(), (await manager.sign_in.dev_sign_in(request(), "ann@example.test")).token
        ),
        org.id,
    )
    out = (
        await manager.sign_in.logout(await manager.authenticate_login(request(), third.token))
    ).auth_session
    logged_out = next(r for _, r in relay.rows if r.target_id == out.id)
    assert logged_out.actor_id == ctx.user_id and logged_out.org_id == org.id
    assert [r.target_id for _, r in relay.rows if r.kind == "tenancy.auth_session.revoked"] == [
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
    stale = await storage.read_auth_session(org.id, older.security.credential_id)
    assert stale is not None
    await storage.write_auth_session(
        org.id, stale.model_copy(update={"expires_at": utcnow() - timedelta(seconds=1)})
    )
    # The page holds one session; the expired one must not be it.
    assert [s.id for s in await manager.credentials.get_auth_sessions(newer, limit=10)] == [
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
