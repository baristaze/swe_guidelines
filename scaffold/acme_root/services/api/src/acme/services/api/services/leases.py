"""The leases service: what the wire can do with a resource's line and its
leases, in views."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext
from acme.services.api.types.leases import (
    AskRequest,
    LeaseRequestView,
    LeaseView,
    LineView,
    ReorderRequest,
    StandingView,
)


class LeasesServiceInterface(ABC):
    @abstractmethod
    async def ask(self, ctx: TenantContext, body: AskRequest, request_id: UUID) -> StandingView:
        """An ask under `request_id`, the id the idempotency record minted,
        which is also its key: a retry finds the request it made."""
        ...

    @abstractmethod
    async def get_request(self, ctx: TenantContext, request_id: UUID) -> StandingView: ...

    @abstractmethod
    async def cancel(self, ctx: TenantContext, request_id: UUID) -> LeaseRequestView: ...

    @abstractmethod
    async def reorder(
        self, ctx: TenantContext, request_id: UUID, body: ReorderRequest
    ) -> LeaseRequestView: ...

    @abstractmethod
    async def line(self, ctx: TenantContext, resource_id: UUID) -> LineView: ...

    @abstractmethod
    async def get_lease(self, ctx: TenantContext, lease_id: UUID) -> LeaseView: ...

    @abstractmethod
    async def renew(self, ctx: TenantContext, lease_id: UUID) -> LeaseView: ...

    @abstractmethod
    async def release(self, ctx: TenantContext, lease_id: UUID) -> LeaseView: ...

    @abstractmethod
    async def revoke(self, ctx: TenantContext, lease_id: UUID) -> LeaseView: ...
