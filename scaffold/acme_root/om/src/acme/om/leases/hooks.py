"""The hooks a kind registers, as a work kind registers its handler. A
resource kind says what a grant of it starts and whether a request may still
be granted; a waiter kind says whether a waiter still waits and what wakes
it. The root hands the manager one impl per kind, and a kind with none is
refused at the ask."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.request import LeaseRequest
from acme.om.leases.types.resource import Resource
from acme.om.outbox.types.row import OutboxRow


class ResourceKindInterface(ABC):
    @abstractmethod
    async def may_grant(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest
    ) -> bool:
        """Asked just before a grant, under the context of whatever freed the
        resource: whether the request may still be granted, read live (the
        asker's role, say). False cancels the request as `refused`."""
        ...

    @abstractmethod
    def grant_rows(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        """What a grant starts: the rows it lands in the grant's own commit,
        a `work.<kind>` row among them for a job to enqueue. A kind keeps
        per-lease facts of its own in its own table, keyed by the lease's id."""
        ...


class WaiterInterface(ABC):
    @abstractmethod
    async def still_waits(self, ctx: TenantContext, waiter_id: UUID) -> bool:
        """Asked before a grant: whether the waiter still waits. False
        cancels the request as `waiter_gone`, so no one is granted a lease
        nothing will use."""
        ...

    @abstractmethod
    def wake_rows(self, ctx: TenantContext, waiter_id: UUID, lease: Lease) -> tuple[OutboxRow, ...]:
        """The rows the grant lands in its own commit to wake the waiter."""
        ...

    @abstractmethod
    def revoke_rows(
        self, ctx: TenantContext, waiter_id: UUID, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        """The rows a revocation lands in its own commit to tell the waiter
        its lease is gone."""
        ...
