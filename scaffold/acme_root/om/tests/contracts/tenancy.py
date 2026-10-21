"""The tenancy manager in the unit suites: the manager over the memory
storage, the request stage a test mints at its edge, and the people, the
operators, and the relay the suites share. The fixtures that build them
for each test are in `om/tests/unit/conftest.py`."""

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.change_events.storage.impl.memory import ChangeEventStorageMemoryImpl
from acme.om.context import (
    AppContext,
    AppType,
    OperatorContext,
    OperatorRole,
    RequestContext,
    Role,
    TenantContext,
)
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.relay import OutboxRelayInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import build_tenancy
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.user import User

from contracts.second_factor import TOTP_KEY, SteppingClock
from contracts.signing_in import provider_login, signing_in

APP = AppContext(type=AppType.PORTAL, version="portal@test")


def team(memberships: tuple[OrgMembership, ...]) -> list[OrgMembership]:
    """The team orgs among a person's places; the personal one is always there."""
    return [m for m in memberships if not m.org.personal]


def request(app: AppContext = APP) -> RequestContext:
    """The request stage a test mints at its edge, one per call."""
    return RequestContext(request_id=new_id(), app=app)


def make_manager(
    storage: TenancyStorageMemoryImpl,
    infra: InfraLocalImpl,
    options: TenancyOptions | None = None,
    cache: CacheInterface | None = None,
    outbox: OutboxStorageMemoryImpl | None = None,
    clock: SteppingClock | None = None,
) -> TenancyManagerInterface:
    # A relay over its own outbox is enough where the sweep never runs; the
    # `manager` fixture shares the one the storage lands rows in.
    relay = OutboxRelayImpl(
        outbox or OutboxStorageMemoryImpl(), ChangeEventStorageMemoryImpl(), infra.get_topics()
    )
    twin = IdentityProviderTwinImpl()
    manager = build_tenancy(
        storage,
        relay,
        cache or infra.get_cache(CacheScope.REALTIME_TICKET),
        options or TenancyOptions(totp_encryption_key=TOTP_KEY),
        clock or SteppingClock(),
        identity_provider=twin,
    )
    return signing_in(manager, twin)


async def token_operator(manager: TenancyManagerInterface, email: str) -> OperatorContext:
    """An operator admitted on the token the grant job mints, for a test about
    what an operator does; the tests of the gate sign in with a code."""
    issued = await manager.grant_operator_token(request(), email)
    return await manager.admit_operator(await manager.authenticate_login(request(), issued.token))


async def sign_in(manager: TenancyManagerInterface, email: str, org_id: UUID) -> TenantContext:
    issued = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), (await provider_login(manager, email)).token),
        org_id,
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


async def seed_operator(
    manager: TenancyManagerInterface, email: str, role: OperatorRole = OperatorRole.WRITE
) -> None:
    await manager.bootstrap(request(), email, email.split("@")[0], email, "Op", operator_role=role)


async def signed_up(manager: TenancyManagerInterface, email: str, name: str) -> TenantContext:
    """A person who signed in the first time, in a session in their personal org."""
    issued = await provider_login(manager, email, name)
    return await sign_in_with(manager, email, issued.memberships[0].org.id)


async def sign_in_with(manager: TenancyManagerInterface, email: str, org_id: UUID) -> TenantContext:
    session = await manager.sign_in.exchange_login(
        await manager.authenticate_login(request(), (await provider_login(manager, email)).token),
        org_id,
    )
    return await manager.authenticate(request(), session.token)
