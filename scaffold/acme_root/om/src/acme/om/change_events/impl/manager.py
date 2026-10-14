from datetime import timedelta

from pydantic import Field

from acme.om.base import OmBase, utcnow
from acme.om.change_events.manager import ChangeEventsManagerInterface
from acme.om.change_events.storage import ChangeEventStorageInterface
from acme.om.change_events.types.change_event import ChangeEvent
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import StreamTruncated
from acme.om.tenancy import TenancyManagerInterface


class ChangeEventsOptions(OmBase):
    max_limit: int = 500
    retention: timedelta | None = None
    """How long a living tenant's events are kept. None keeps every one, and
    the sweep never moves the floor (ADR 0040)."""
    purge_batch: int = Field(default=1000, gt=0)
    """The most events one purge call deletes: by the trim across tenants, or
    from the stream of a tenant past its retention; the sweep calls again
    while a batch comes back full."""


class ChangeEventsManagerImpl(ChangeEventsManagerInterface):
    def __init__(
        self,
        storage: ChangeEventStorageInterface,
        tenancy: TenancyManagerInterface,
        options: ChangeEventsOptions,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._options = options

    async def append_change_event(self, ctx: TenantContext, event: ChangeEvent) -> ChangeEvent:
        ctx.require(Permission.WRITE)
        # The tenant and the provenance are the context's, whatever the caller built.
        stamped = event.model_copy(
            update={
                "org_id": ctx.org_id,
                "actor_id": ctx.user_id,
                "request_id": ctx.request_id,
                "app": ctx.app.type.value,
            }
        )
        return (await self._storage.append_change_events(ctx.org_id, [stamped]))[0]

    async def get_change_events(
        self, ctx: TenantContext, after_seq: int, limit: int
    ) -> list[ChangeEvent]:
        ctx.require(Permission.READ)
        after = max(0, after_seq)
        # The floor is read after the page, in its transaction. A trim that
        # committed before the page was read is seen there too, so a page with
        # a hole never leaves.
        page = await self._storage.read_page(ctx.org_id, after, self._clamp(limit))
        if after < page.floor:
            raise StreamTruncated(floor=page.floor, head=page.head)
        return list(page.change_events)

    async def purge_across_tenants(self) -> int:
        if self._options.retention is None:
            return 0
        before = utcnow() - self._options.retention
        return await self._storage.trim(before, self._options.purge_batch)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def get_head(self, ctx: TenantContext) -> int:
        ctx.require(Permission.READ)
        return await self._storage.read_head(ctx.org_id)

    def _clamp(self, limit: int) -> int:
        return max(1, min(limit, self._options.max_limit))
