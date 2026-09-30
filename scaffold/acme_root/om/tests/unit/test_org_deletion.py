"""An owner deletes their team org: the refusals, the one commit that closes
it for everyone in it, the work it asks for, the session the owner lands on,
and the org's own end, over the memory storage and the identity provider's
twin."""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.second_factor import TOTP_KEY

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.om.base import EMPTY_UUID, new_id
from acme.om.context import AppContext, AppType, RequestContext, Role, TenantContext
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import (
    NotAuthenticated,
    NotAuthorized,
    PersonalOrgFixed,
    ValidationFailed,
)
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.relay import OutboxRelayInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.org import Org
from acme.om.work.types.work_item import WorkKind, work_row_kind

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


class SpyRelay(OutboxRelayInterface):
    """The real relay, with every row it was handed kept for the assertions."""

    def __init__(self, relay: OutboxRelayImpl) -> None:
        self._relay = relay
        self.rows: list[OutboxRow] = []

    async def relay(self, org_id: UUID, row: OutboxRow) -> bool:
        self.rows.append(row)
        return await self._relay.relay(org_id, row)

    async def relay_all(self, org_id: UUID, rows: Sequence[OutboxRow]) -> bool:
        self.rows.extend(rows)
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


@pytest.fixture
def storage() -> TenancyStorageMemoryImpl:
    return TenancyStorageMemoryImpl(OutboxStorageMemoryImpl(), IdempotencyStorageMemoryImpl())


@pytest.fixture
def twin() -> IdentityProviderTwinImpl:
    return IdentityProviderTwinImpl()


@pytest.fixture
def relay(tmp_path: Path) -> SpyRelay:
    infra = InfraLocalImpl(tmp_path)
    return SpyRelay(
        OutboxRelayImpl(OutboxStorageMemoryImpl(), EventStorageMemoryImpl(), infra.get_topics())
    )


@pytest.fixture
def manager(
    storage: TenancyStorageMemoryImpl,
    relay: SpyRelay,
    twin: IdentityProviderTwinImpl,
    tmp_path: Path,
) -> TenancyManagerInterface:
    infra = InfraLocalImpl(tmp_path)
    return build_tenancy(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=True, totp_encryption_key=TOTP_KEY),
        identity_provider=twin,
    )


TOKENS: dict[UUID, str] = {}
"""The token behind each session the tests signed in with, by session id."""


async def dev(
    manager: TenancyManagerInterface, email: str, org_id: UUID | None = None
) -> TenantContext:
    """A person signed in locally, in `org_id` or else their personal org."""
    login = await manager.sign_in.dev_sign_in(request(), email)
    if org_id is None:
        org_id = next(m.org.id for m in login.memberships if m.org.personal)
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), login.token), org_id
    )
    ctx = await manager.authenticate(request(), issued.token)
    TOKENS[ctx.security.credential_id] = issued.token
    return ctx


async def ajax(manager: TenancyManagerInterface) -> Org:
    """Ajax: Ann owns it, Bob is a member, Cid an admin."""
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.MEMBER)
    await manager.add_member(request(), "ajax", "cid@example.test", "Cid", Role.ADMIN)
    return org


async def test_only_an_owners_session_deletes_a_team_org(manager: TenancyManagerInterface) -> None:
    org = await ajax(manager)
    bob = await dev(manager, "bob@example.test", org.id)
    cid = await dev(manager, "cid@example.test", org.id)
    ann = await dev(manager, "ann@example.test", org.id)
    program = await manager.authenticate(
        request(), (await manager.credentials.create_api_key(ann, "ci", Role.OWNER)).key
    )
    for refused in (bob, cid, program):
        with pytest.raises(NotAuthorized):
            await manager.org.delete_org(refused, "Ajax")
    live = await manager.org.get_org(ann)
    assert live.deleted_at is None


async def test_the_typed_name_must_be_the_orgs(
    manager: TenancyManagerInterface, storage: TenancyStorageMemoryImpl
) -> None:
    org = await ajax(manager)
    ann = await dev(manager, "ann@example.test", org.id)
    for typed in ("Ajax Inc", "ajax", ""):
        with pytest.raises(ValidationFailed):
            await manager.org.delete_org(ann, typed)
    assert await storage.count_members(org.id) == 3
    # Surrounding space is forgiven; the spelling is not.
    await manager.org.delete_org(ann, "  Ajax ")
    assert await storage.count_members(org.id) == 0


async def test_a_personal_org_goes_only_with_its_person(manager: TenancyManagerInterface) -> None:
    ann = await dev(manager, "ann@example.test")
    home = await manager.org.get_org(ann)
    with pytest.raises(PersonalOrgFixed):
        await manager.org.delete_org(ann, home.name)


async def test_everyone_loses_the_org_in_one_commit_and_the_rest_is_asked_for(
    manager: TenancyManagerInterface,
    storage: TenancyStorageMemoryImpl,
    relay: SpyRelay,
    twin: IdentityProviderTwinImpl,
) -> None:
    org = await ajax(manager)
    ann = await dev(manager, "ann@example.test", org.id)
    bob = await dev(manager, "bob@example.test", org.id)
    key = await manager.credentials.create_api_key(bob, "ci", Role.MEMBER)
    invited = await manager.members.invite_member(ann, "dee@example.test", Role.MEMBER)
    provider_org_id = (await manager.org.get_org(ann)).provider_org_id
    assert provider_org_id is not None, "the invitation made the provider's organization"
    relay.rows.clear()

    deleted = await manager.org.delete_org(ann, "Ajax")

    # Nobody is in it, and no credential reaches it.
    assert await storage.count_members(org.id) == 0
    for ctx in (ann, bob):
        with pytest.raises(NotAuthenticated):
            await manager.authenticate(request(), TOKENS[ctx.security.credential_id])
        session = await storage.read_session(org.id, ctx.security.credential_id)
        assert session is not None and session.revoked_at is not None
        user = await storage.read_user(org.id, ctx.user_id)
        assert user is not None and user.deleted_at is not None
    stored_key = await storage.read_api_key(org.id, key.api_key.id)
    assert stored_key is not None and stored_key.deleted_at is not None
    invitation = await storage.read_invitation(org.id, invited.id)
    assert invitation is not None and invitation.state is InvitationState.REVOKED
    # Bob's picker no longer offers it.
    login = await manager.sign_in.dev_sign_in(request(), "bob@example.test")
    assert org.id not in {m.org.id for m in login.memberships}
    # The org waits, live and let go of its provider organization, for the queue.
    closed = await storage.read_org(org.id)
    assert closed is not None and closed.deleted_at is None and closed.provider_org_id is None
    assert provider_org_id in twin.organizations, "the queue deletes it, not the request"
    kinds = {(row.kind, row.target_id) for row in relay.rows}
    assert ("tenancy.user.deleted", ann.user_id) in kinds
    assert ("tenancy.user.deleted", bob.user_id) in kinds
    assert ("tenancy.session.revoked", bob.security.credential_id) in kinds
    assert ("tenancy.api_key.deleted", key.api_key.id) in kinds
    work = next(r for r in relay.rows if r.kind == work_row_kind(WorkKind.DELETE_ORG))
    assert (work.org_id, work.target_id, work.actor_id) == (org.id, org.id, ann.user_id)
    assert work.payload == {"provider_org_id": provider_org_id}
    # Every row carries ids, never who.
    for row in relay.rows:
        assert "bob" not in str(row.payload).lower()
    # The owner lands in their personal org with a session of their own.
    assert deleted.session is not None
    assert deleted.session.org.personal and deleted.session.role is Role.OWNER
    home = await manager.authenticate(request(), deleted.session.token)
    assert home.org_id == deleted.session.org.id


async def test_the_last_owner_deletes_the_org_and_then_their_account(
    manager: TenancyManagerInterface,
) -> None:
    _, org = await manager.bootstrap(request(), "Solo", "solo", "ann@example.test", "Ann")
    ann = await dev(manager, "ann@example.test", org.id)
    deleted = await manager.org.delete_org(ann, "Solo")
    assert deleted.session is not None
    home = await manager.authenticate(request(), deleted.session.token)
    await manager.org.delete_account(home, "ann@example.test")


async def test_the_org_goes_last_and_keeps_the_retention(manager: TenancyManagerInterface) -> None:
    org = await ajax(manager)
    ann = await dev(manager, "ann@example.test", org.id)
    await manager.org.delete_org(ann, "Ajax")
    work = await manager.service_context(request(), org.id, ann.user_id)
    deleted = await manager.org.delete_closed_org(work)
    assert deleted is not None and deleted.deleted_at is not None
    # The record stays, under its name, as an operator's deletion leaves it.
    assert (deleted.name, deleted.slug) == ("Ajax", "ajax")
    sweep = next(c for c in await manager.service_contexts(request()) if c.org_id == org.id)
    assert sweep.user_id == EMPTY_UUID
    assert not await manager.tenant_expired(sweep), "purged after the retention, not at once"
    # A rerun finds it deleted and writes nothing.
    assert await manager.org.delete_closed_org(work) is None


async def test_only_the_platform_ends_a_closed_org_and_never_a_personal_one(
    manager: TenancyManagerInterface,
) -> None:
    org = await ajax(manager)
    ann = await dev(manager, "ann@example.test", org.id)
    with pytest.raises(NotAuthorized):
        await manager.org.delete_closed_org(ann)
    home = await dev(manager, "ann@example.test")
    work = await manager.service_context(request(), home.org_id, home.user_id)
    with pytest.raises(PersonalOrgFixed):
        await manager.org.delete_closed_org(work)
