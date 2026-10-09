"""The leases swimlane: a scarce resource, leased to one holder at a time
under a fencing token, with a line in front of it.

A resource stands for a row of another namespace by its kind and id. A
request names one resource, or a selector, and waits in line; a grant is a
side effect of a resource freeing (a release, an expiry, a revocation, or
its availability back) and goes to the head of its line, decided under the
anchor's lock. The holder renews its lease and releases it; the sweep ends
one past its expiry and the skew margin. A grant that starts a job lets the
worker that claims the job start the lease, renew it, and end it for the
holder."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import RequestContext, TenantContext
from acme.om.leases.types.lease import JobClaim, Lease
from acme.om.leases.types.request import LeaseEntry, LeaseRequest, Line, Standing, WaiterKind
from acme.om.leases.types.resource import Resource, ResourceUpdate
from acme.om.orchestrations.types.orchestration import Step


class LeasesManagerInterface(ABC):
    # Resources: the kind's owner registers one, updates it, sets its
    # availability, and retires it.

    @abstractmethod
    async def register(self, ctx: TenantContext, resource: Resource) -> Resource:
        """The create: a resource of a registered kind for the owner's row,
        offered to its line at once. A kind and row registered already answer
        the stored resource. An owner that writes its row in the same commit
        uses the storage's companion statement instead, and calls `offer`
        after it."""
        ...

    @abstractmethod
    async def update(
        self, ctx: TenantContext, resource_id: UUID, change: ResourceUpdate
    ) -> Resource:
        """The owner's update: the labels and the bound, and the availability
        when it names one, under the anchor's lock; then the resource is
        offered to its line, so a waiting request its labels now match is
        granted and one they no longer match is not. A lease it holds keeps
        its term, and the new bound holds from its next renewal. An owner that
        writes its row in the same commit uses the storage's companion
        statement instead, and calls `offer` after it."""
        ...

    @abstractmethod
    async def offer(self, ctx: TenantContext, resource_id: UUID) -> Lease | None:
        """Offers a resource to its line, and answers the lease it granted, if
        any: what its owner calls after the commit that registered it,
        updated it, or put it back in service. The sweep offers each free
        resource too, so a crash before this call only delays a grant."""
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
        leave their line as `retired`, each waking its waiter, and its lease
        is never renewed."""
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
        must be the shape its kind fixes (`ASK_PAYLOADS`), and the kind may
        refuse the ask before anything lands (`check_ask`). An orchestration
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
        """A resource and the requests in its line, first first, each with its
        place and estimate, replayed from the one read the line makes."""
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
    async def list_leases(
        self, ctx: TenantContext, resource_id: UUID | None = None, limit: int = 50
    ) -> tuple[LeaseEntry, ...]:
        """The history: the org's leases, or one resource's, newest first,
        ended ones included, each with the request it answered, in one read;
        at most `limit`, which the manager clamps. A resource of another
        tenant has none here."""
        ...

    @abstractmethod
    async def renew(
        self,
        ctx: TenantContext,
        lease_id: UUID,
        seconds: int | None = None,
        job: JobClaim | None = None,
    ) -> Lease:
        """Its holder's, or with `job` its job's worker's: the lease runs
        `seconds` from now, or its term again when it names none, within the
        resource's bound. A lease past its expiry, ended, or on a retired
        resource is refused (`LeaseEnded`); anyone else is refused
        (`NotAuthorized`), and so is a claim that no longer holds the job."""
        ...

    @abstractmethod
    async def start(self, ctx: TenantContext, lease_id: UUID, job: JobClaim) -> Lease:
        """Its job's worker's: the job starts, and the lease runs its term
        from now. It lands while the lease still holds its resource: until
        the window the grant gave the job and the skew margin have passed, so
        a job that waited in its lane to the end of its window still gets its
        whole term. Past that, or once the lease ended, it is refused
        (`LeaseEnded`); a lease already started is answered as it is."""
        ...

    @abstractmethod
    async def release(
        self, ctx: TenantContext, lease_id: UUID, job: JobClaim | None = None
    ) -> Lease:
        """Its holder's, or with `job` its job's worker's: the lease ends and
        the resource goes to its line. A released lease is answered as it
        is; one ended otherwise is refused (`LeaseEnded`)."""
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
