"""Doubles and helpers the manager suites share: just enough tenancy, a
context of a given role, and the media manager over the memory storage."""

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    RequestContext,
    Role,
    TenantContext,
    build_context,
)
from acme.om.media.impl.manager import MediaManagerImpl, MediaOptions
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.rules import permissions_of
from acme.om.tenancy.types.org import Org
from contracts.factories import make_org, make_user

APP = AppContext(type=AppType.PORTAL, version="portal@test")


class Members(TenancyManagerInterface):
    """Just enough tenancy for the sweep's question: whether the tenant is
    past its retention. A partial double: only `tenant_expired` is reached,
    and any other method fails loudly as unimplemented, so the abstract set
    is cleared below."""

    def __init__(self) -> None:
        self.expired = False

    async def tenant_expired(self, ctx: TenantContext) -> bool:
        return self.expired


Members.__abstractmethods__ = frozenset()


def context(role: Role, org: Org | None = None) -> TenantContext:
    user = make_user(new_id())
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
    """The media manager over the memory storage, landing in the same outbox."""
    return MediaManagerImpl(
        MediaStorageMemoryImpl(outbox),
        infra.get_buckets(),
        members,
        relay,
        infra.get_flags(),
        MediaOptions(),
    )
