"""The leases swimlane: a scarce resource, leased to one holder at a time
under a fencing token, with a line in front of it.

A resource stands for a row of another namespace by its kind and id. A
request names one resource, or a selector, and waits in line; a grant is a
side effect of a resource freeing (a release, an expiry, a revocation, or
its availability back) and goes to the head of its line, decided under the
anchor's lock. The holder renews its lease and releases it; the sweep ends
one past its expiry and the skew margin."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import RequestContext, TenantContext
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.request import LeaseRequest, Line, Standing, WaiterKind
from acme.om.leases.types.resource import Resource
from acme.om.orchestrations.types.orchestration import Step


class LeasesManagerInterface(ABC):
    # Resources: the kind's owner registers one, sets its availability, and
    # retires it.

    @abstractmethod
    async def register(self, ctx: TenantContext, resource: Resource) -> Resource:
        """The create: a resource of a registered kind for the owner's row. A
        kind and row registered already answer the stored resource. An owner
        that writes its row in the same commit uses the storage's companion
        statement instead."""
        ...

    @abstractmethod
    async def get_resource(self, ctx: TenantContext, resource_id: UUID) -> Resource: ...

    @abstractmethod
    async def set_available(
        self, ctx: TenantContext, resource_id: UUID, available: bool
    ) -> Resource:
        """Takes a resource out of service, where held leases run on and
        nothing new is granted, or puts it back, which offers it to its line."""
        ...

    @abstractmethod
    async def retire(self, ctx: TenantContext, resource_id: UUID) -> Resource:
        """Retires a resource with its owner's row: the requests that name it
        are cancelled as `retired`, and its lease is never renewed."""
        ...

    # The line.

    @abstractmethod
    async def ask(
        self,
        ctx: TenantContext,
        request: LeaseRequest,
        park: Step | None = None,
    ) -> Standing:
        """Joins the line, last in the org's one rank order, and offers every
        free resource the request may take, so a direct ask is granted at once
        only when no one waits in front of it. An ask asked again by its key
        answers its lease or its place and joins no line twice. The payload
        must be the shape its kind fixes (`ASK_PAYLOADS`). An orchestration
        that waits on the request parks in the same call: `park` is its
        `Step`, landed only while the request still waits, under the lock the
        grant takes, so a grant either finds it parked and wakes it or comes
        first and leaves it running with its lease."""
        ...

    @abstractmethod
    async def get_request(self, ctx: TenantContext, request_id: UUID) -> Standing:
        """Where a request stands: its lease, or its place and estimate."""
        ...

    @abstractmethod
    async def line(self, ctx: TenantContext, resource_id: UUID) -> Line:
        """A resource and the requests in its line, first first."""
        ...

    @abstractmethod
    async def cancel(self, ctx: TenantContext, request_id: UUID) -> LeaseRequest:
        """Its asker's, or a manager's: a waiting request leaves every line as
        `asked`. A settled one is answered as it is."""
        ...

    @abstractmethod
    async def reorder(
        self, ctx: TenantContext, request_id: UUID, before_id: UUID | None
    ) -> LeaseRequest:
        """A manager's: the request moves in front of `before_id`, or to the
        end, between its two new neighbours; no one else moves."""
        ...

    @abstractmethod
    async def leave(self, ctx: TenantContext, waiter_kind: WaiterKind, waiter_id: UUID) -> int:
        """A waiter that ends leaves every line: its waiting requests are
        cancelled as `waiter_gone`; returns how many."""
        ...

    # Leases.

    @abstractmethod
    async def get_lease(self, ctx: TenantContext, lease_id: UUID) -> Lease: ...

    @abstractmethod
    async def renew(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        """Its holder's: the lease runs its term again from now, within the
        resource's bound. A lease past its expiry, ended, or on a retired
        resource is refused (`LeaseEnded`)."""
        ...

    @abstractmethod
    async def release(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        """Its holder's: the lease ends and the resource goes to its line. A
        released lease is answered as it is; one ended otherwise is refused
        (`LeaseEnded`)."""
        ...

    @abstractmethod
    async def revoke(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        """A manager's: the lease ends, its waiter is told, and the resource
        goes to its line."""
        ...

    # The sweep.

    @abstractmethod
    async def sweep(self, rctx: RequestContext) -> int:
        """Platform-internal, once a pass, for the orgs with something due:
        ends each lease past its expiry and the skew margin, expires each
        request past its wait, and offers each free resource to its line;
        returns how many leases and requests it ended."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the leases ended, the requests settled, and the
        retired resources past the retention, a batch at most of each,
        whatever their tenant; returns how many. It takes no context, because
        it runs for no tenant and no principal."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every row, a
        batch at most a call. Any other tenant returns 0 and reads nothing."""
        ...
