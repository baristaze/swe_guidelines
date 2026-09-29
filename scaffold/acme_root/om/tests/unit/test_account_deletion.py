"""A person deletes their account: the refusals, the one commit that erases
them in every org, the work it asks for, and the personal org's own end,
over the memory storage and the identity provider's twin."""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.plans import ON_TEAM
from contracts.second_factor import TOTP_KEY

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.twin import TWIN_LOGOUT, IdentityProviderTwinImpl
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import (
    LastOwner,
    NotAuthorized,
    NotFound,
    OperatorRoleHeld,
    PersonalOrgFixed,
    ValidationFailed,
)
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.opcontext import AppContext, AppType, OpContext, OperatorRole, RequestContext, Role
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.relay import OutboxRelayInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.impl.manager import (
    DELETED_PERSONAL_ORG_NAME,
    TenancyManagerImpl,
    TenancyOptions,
)
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.work.types.work_item import WorkKind, work_row_kind

APP = AppContext(type=AppType.PORTAL, version="portal@test")
SIGNED_OUT = "http://localhost:5173/signed-out"


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
def relay(storage: TenancyStorageMemoryImpl, tmp_path: Path) -> SpyRelay:
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
) -> TenancyManagerImpl:
    infra = InfraLocalImpl(tmp_path)
    return TenancyManagerImpl(
        storage,
        relay,
        infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(
            dev_sign_in=True, totp_encryption_key=TOTP_KEY, sign_out_return_uris=(SIGNED_OUT,)
        ),
        identity_provider=twin,
        entitlements=ON_TEAM,
    )


async def enter(manager: TenancyManagerImpl, token: str, org_id: UUID) -> OpContext:
    issued = await manager.exchange_login(
        await manager.authenticate_login(request(), token), org_id
    )
    return await manager.authenticate(request(), issued.token)


async def dev(manager: TenancyManagerImpl, email: str, org_id: UUID | None = None) -> OpContext:
    """A person signed in locally, in `org_id` or else their personal org."""
    login = await manager.dev_sign_in(request(), email)
    if org_id is None:
        org_id = next(m.org.id for m in login.memberships if m.org.personal)
    return await enter(manager, login.token, org_id)


async def test_the_typed_email_must_be_the_accounts(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl
) -> None:
    ann = await dev(manager, "ann@example.test")
    with pytest.raises(ValidationFailed):
        await manager.delete_account(ann, "someone@example.test")
    assert await storage.read_session(ann.org_id, ann.security.credential_id) is not None
    # Space and letter case are forgiven, as an address is read everywhere.
    await manager.delete_account(ann, "  Ann@Example.TEST ")
    assert await storage.read_session(ann.org_id, ann.security.credential_id) is None
    with pytest.raises(NotFound):
        await manager.get_identity(ann)


async def test_an_api_key_never_deletes_its_person(manager: TenancyManagerImpl) -> None:
    _, org = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ann = await dev(manager, "ann@example.test", org.id)
    key = await manager.create_api_key(ann, "ci", Role.MEMBER)
    program = await manager.authenticate(request(), key.key)
    with pytest.raises(NotAuthorized):
        await manager.delete_account(program, "ann@example.test")


async def test_the_last_owner_of_a_team_org_is_refused_and_told_which(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl
) -> None:
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, globex = await manager.bootstrap(request(), "Globex", "globex", "ann@example.test", "Ann")
    ann = await dev(manager, "ann@example.test", ajax.id)
    with pytest.raises(LastOwner) as refused:
        await manager.delete_account(ann, "ann@example.test")
    assert sorted(refused.value.orgs) == sorted(
        [(str(ajax.id), "Ajax", "ajax"), (str(globex.id), "Globex", "globex")]
    )
    assert "Ajax" in refused.value.message and "Globex" in refused.value.message
    assert refused.value.http_status == 409
    assert await storage.read_session(ajax.id, ann.security.credential_id) is not None

    # Another owner in each, and the refusal is gone.
    for org in (ajax, globex):
        await manager.add_member(request(), org.slug, "bob@example.test", "Bob", Role.OWNER)
    await manager.delete_account(ann, "ann@example.test")


async def test_an_operator_gives_up_the_role_first(manager: TenancyManagerImpl) -> None:
    ann = await dev(manager, "ann@example.test")
    await manager.grant_operator(request(), "ann@example.test", OperatorRole.READ)
    with pytest.raises(OperatorRoleHeld) as refused:
        await manager.delete_account(ann, "ann@example.test")
    assert refused.value.http_status == 403
    await manager.disable_operator(request(), "ann@example.test")
    await manager.delete_account(ann, "ann@example.test")


async def test_the_account_goes_in_one_commit_and_asks_for_the_rest(
    manager: TenancyManagerImpl,
    storage: TenancyStorageMemoryImpl,
    relay: SpyRelay,
    twin: IdentityProviderTwinImpl,
) -> None:
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "owner@example.test", "Owner")
    _, bob_in_ajax, _ = await manager.add_member(
        request(), "ajax", "bob@example.test", "Bob", Role.MEMBER
    )
    # Bob signs in through the provider: it knows him by a subject.
    code = twin.issue_code("bob@example.test")
    login = await manager.sign_in_with_code(request(), code)
    bob = await enter(manager, login.token, ajax.id)
    identity = await manager.get_identity(bob)
    assert identity.subject is not None
    personal = next(m.org for m in login.memberships if m.org.personal)
    at_home = await dev(manager, "bob@example.test", personal.id)
    key = await manager.create_api_key(at_home, "ci", Role.MEMBER)
    await storage.record_failed_sign_in(email_digest(identity.email), utcnow())
    relay.rows.clear()

    deleted = await manager.delete_account(bob, "bob@example.test", SIGNED_OUT)

    # The browser ends the provider's session too, and comes back here.
    assert deleted.provider_logout_url is not None
    assert deleted.provider_logout_url.startswith(TWIN_LOGOUT)
    assert "signed-out" in deleted.provider_logout_url
    # The person is gone: identity, sign-in delay, and every place and credential.
    assert await storage.read_identity(identity.id) is None
    assert await storage.read_sign_in_delay(email_digest(identity.email)) is None
    assert await storage.read_user(ajax.id, bob_in_ajax.id) is None
    assert await storage.read_membership_for_user(ajax.id, bob_in_ajax.id) is None
    for ctx in (bob, at_home):
        assert await storage.read_session(ctx.org_id, ctx.security.credential_id) is None
    assert await storage.read_api_key(personal.id, key.api_key.id) is None
    # Nothing a row says names who they were.
    for row in relay.rows:
        assert "bob" not in str(row.payload).lower()
    kinds = {(row.org_id, row.kind, row.target_id) for row in relay.rows}
    assert (ajax.id, "tenancy.user.deleted", bob_in_ajax.id) in kinds
    assert (ajax.id, work_row_kind(WorkKind.UNASSIGN_TASKS), bob_in_ajax.id) in kinds
    assert (ajax.id, "tenancy.session.revoked", bob.security.credential_id) in kinds
    assert (personal.id, "tenancy.api_key.deleted", key.api_key.id) in kinds
    # The unassignment runs as Bob in Ajax; the rest in his personal org.
    unassign = next(r for r in relay.rows if r.kind == work_row_kind(WorkKind.UNASSIGN_TASKS))
    assert unassign.actor_id == bob_in_ajax.id
    rest = next(r for r in relay.rows if r.kind == work_row_kind(WorkKind.DELETE_ACCOUNT))
    assert (rest.org_id, rest.target_id) == (personal.id, personal.id)
    assert rest.payload == {"provider_user_id": identity.subject}
    # The personal org stays until the provider's side is done.
    home = await storage.read_org(personal.id)
    assert home is not None and home.deleted_at is None


async def test_the_same_address_signs_up_again_as_a_new_person(
    manager: TenancyManagerImpl,
) -> None:
    ann = await dev(manager, "ann@example.test")
    first = await manager.get_identity(ann)
    await manager.delete_account(ann, "ann@example.test")
    again = await dev(manager, "ann@example.test")
    second = await manager.get_identity(again)
    assert second.id != first.id
    assert again.org_id != ann.org_id, "a personal org of their own, a new one"


async def test_the_personal_org_goes_last_and_keeps_no_retention(
    manager: TenancyManagerImpl, storage: TenancyStorageMemoryImpl
) -> None:
    ann = await dev(manager, "ann@example.test")
    home = ann.org_id
    await manager.delete_account(ann, "ann@example.test")
    work = await manager.service_context(request(), home, ann.user_id)
    deleted = await manager.delete_personal_org(work)
    assert deleted is not None and deleted.deleted_at is not None
    # The record stays, and says nothing of whose it was.
    assert (deleted.name, deleted.slug) == (DELETED_PERSONAL_ORG_NAME, f"deleted-{home}")
    sweep = next(c for c in await manager.service_contexts(request()) if c.org_id == home)
    assert sweep.user_id == EMPTY_UUID
    assert await manager.tenant_expired(sweep), "purged at the next sweep, not after thirty days"
    # A rerun finds it deleted and writes nothing.
    assert await manager.delete_personal_org(work) is None
    assert await storage.read_org(home) == deleted


async def test_only_a_deleted_persons_org_is_deleted_this_way(manager: TenancyManagerImpl) -> None:
    _, ajax = await manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    ann = await dev(manager, "ann@example.test")
    home = await manager.service_context(request(), ann.org_id, ann.user_id)
    team = await manager.service_context(request(), ajax.id, ann.user_id)
    for ctx in (home, team):
        with pytest.raises(PersonalOrgFixed):
            await manager.delete_personal_org(ctx)
