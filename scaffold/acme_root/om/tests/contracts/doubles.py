"""Doubles and helpers the manager suites share: just enough tenancy, a
context of a given role, and the media manager a tasks manager composes."""

from uuid import UUID

from contracts.factories import make_org, make_user
from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import EMPTY_UUID, new_id
from acme.om.exceptions import NotFound
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.opcontext import (
    AppContext,
    AppType,
    CredentialKind,
    OpContext,
    RequestContext,
    Role,
    build_context,
)
from acme.om.orchestrations.impl.manager import OrchestrationsManagerImpl, OrchestrationsOptions
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.slack import SlackManagerInterface
from acme.om.slack.types.installation import SlackInstallation
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.rules import permissions_of
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.user import User

APP = AppContext(type=AppType.PORTAL, version="portal@test")


class Members(TenancyManagerInterface):
    """Just enough tenancy for the assignee check and for the sweep's
    questions: the users of one org, whether the tenant is past its
    retention, and the context the sweep works a tenant's rows under. A
    partial double: only `get_user`, `tenant_expired`, and `sweep_context` are
    reached, and any other method fails loudly as unimplemented, so the
    abstract set is cleared below."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.expired = False

    async def get_user(self, ctx: OpContext, user_id: UUID) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise NotFound(f"user {user_id} not found")
        return user

    async def tenant_expired(self, ctx: OpContext) -> bool:
        return self.expired

    async def sweep_context(self, rctx: RequestContext, org_id: UUID) -> OpContext | None:
        return build_context(
            rctx,
            user_id=EMPTY_UUID,
            org_id=org_id,
            role=Role.SERVICE,
            permissions=permissions_of(Role.SERVICE),
            credential_kind=CredentialKind.INTERNAL,
        )


Members.__abstractmethods__ = frozenset()


def request() -> RequestContext:
    """The request stage a case mints at its edge, as the sweep mints one a pass."""
    return RequestContext(request_id=new_id(), app=APP)


def context(role: Role, org: Org | None = None, members: Members | None = None) -> OpContext:
    user = make_user(new_id())
    if members is not None:
        members.users[user.id] = user
    return build_context(
        RequestContext(request_id=new_id(), app=APP),
        user_id=user.id,
        org_id=(org or make_org()).id,
        role=role,
        permissions=permissions_of(role),
        credential_kind=CredentialKind.SESSION_TOKEN,
    )


def media_of(
    outbox: OutboxStorageMemoryImpl,
    members: Members,
    relay: OutboxRelayImpl,
    infra: InfraLocalImpl,
) -> MediaManagerImpl:
    """The media manager a tasks manager composes, over the same outbox."""
    return MediaManagerImpl(
        MediaStorageMemoryImpl(outbox), infra.get_buckets(), members, relay, MediaOptions()
    )


def orchestrations_of(
    storage: OrchestrationsStorageMemoryImpl, members: Members, relay: OutboxRelayImpl
) -> OrchestrationsManagerImpl:
    """The orchestrations manager a tasks manager composes, over the records
    the tasks storage lands its steps in."""
    return OrchestrationsManagerImpl(storage, members, relay, OrchestrationsOptions())


class NoSlack(SlackManagerInterface):
    """A partial double: an org with no Slack installation."""

    async def get_installation(self, ctx: OpContext) -> SlackInstallation | None:
        return None


NoSlack.__abstractmethods__ = frozenset()


def no_slack() -> SlackManagerInterface:
    return NoSlack()  # pyright: ignore[reportAbstractUsage] (a partial double)
