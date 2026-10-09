import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError

from acme.infra.observability import OUTCOMES
from acme.om.base import EMPTY_UUID, Platform, new_id, utcnow
from acme.om.context import Permission, RequestContext, TenantContext
from acme.om.exceptions import (
    InvalidCredential,
    LeaseEnded,
    NotAuthorized,
    NotFound,
    TenantMismatch,
    ValidationFailed,
)
from acme.om.leases.hooks import ResourceKindInterface, WaiterInterface
from acme.om.leases.manager import LeasesManagerInterface
from acme.om.leases.rules import (
    holds_lapsed,
    is_grantable,
    line_of,
    measured_hold,
    place_of,
    rank_at,
    replay,
    stands_in,
    term_of,
)
from acme.om.leases.storage import LeasesStorageInterface
from acme.om.leases.types.lease import Grant, Lease, LeaseStatus
from acme.om.leases.types.request import (
    ASK_PAYLOADS,
    EndReason,
    LeaseRequest,
    Line,
    RequestStatus,
    Standing,
    WaiterKind,
)
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.orchestrations.steps import step_rows
from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.tenancy import TenancyManagerInterface

log = logging.getLogger(__name__)

RESOURCE_CREATED = "leases.resource.created"
RESOURCE_UPDATED = "leases.resource.updated"
REQUEST_CREATED = "leases.request.created"
REQUEST_UPDATED = "leases.request.updated"
LEASE_CREATED = "leases.lease.created"
LEASE_UPDATED = "leases.lease.updated"


class LeasesOptions(Platform):
    # The skew margin: a lease ends this long after its expiry, so a holder
    # whose clock runs slow has stopped before the resource is granted again.
    margin: timedelta = timedelta(seconds=30)
    line_limit: int = 1000  # waiting requests of a kind one offer or estimate reads
    resource_limit: int = 200  # resources of a kind one ask or estimate reads
    # Heads one offer looks at, cancelling those that may no longer be
    # granted, before it leaves the rest to the next freeing or the sweep.
    offer_tries: int = 20
    sweep_orgs: int = 100  # orgs one sweep call visits
    sweep_batch: int = 100  # lapsed leases, and overdue requests, one org's sweep ends
    retention: timedelta = timedelta(days=30)  # an ended lease or a settled request goes after this
    purge_batch: int = 1000  # rows of each table one purge statement deletes at most


class LeasesManagerImpl(LeasesManagerInterface):
    def __init__(
        self,
        storage: LeasesStorageInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: LeasesOptions,
        *,
        kinds: Mapping[ResourceKind, ResourceKindInterface],
        waiters: Mapping[WaiterKind, WaiterInterface],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._kinds = kinds
        self._waiters = waiters
        self._clock = clock

    # Resources.

    async def register(self, ctx: TenantContext, resource: Resource) -> Resource:
        ctx.require(Permission.WRITE)
        self._kind(resource.kind)
        now = self._clock()
        created = resource.model_copy(
            update={
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "available": True,
                "retired_at": None,
                "token": 0,
                "lease_id": None,
                "held_until": None,
                "mean_hold_seconds": None,
            }
        )
        rows = (outbox_row(ctx, RESOURCE_CREATED, created.id, {}),)
        if not await self._storage.create_resource(ctx.org_id, created, rows):
            stored = await self._storage.read_resource_by_ref(
                ctx.org_id, created.kind, created.ref_id
            ) or await self._storage.read_resource(ctx.org_id, created.id)
            if stored is None:
                raise TenantMismatch(f"resource {created.id} is not in {ctx.org_id}")
            return stored
        await self._relay.relay_all(ctx.org_id, rows)
        return created

    async def get_resource(self, ctx: TenantContext, resource_id: UUID) -> Resource:
        ctx.require(Permission.READ)
        return await self._resource(ctx, resource_id)

    async def set_available(
        self, ctx: TenantContext, resource_id: UUID, available: bool
    ) -> Resource:
        ctx.require(Permission.WRITE)
        rows = (outbox_row(ctx, RESOURCE_UPDATED, resource_id, {}),)
        written = await self._storage.write_availability(
            ctx.org_id, resource_id, available, self._clock(), ctx.user_id, rows
        )
        if written is None:
            raise NotFound(f"resource {resource_id} not found")
        await self._relay.relay_all(ctx.org_id, rows)
        if available:
            await self._offer(ctx, resource_id)
        return written

    async def retire(self, ctx: TenantContext, resource_id: UUID) -> Resource:
        ctx.require(Permission.WRITE)
        rows = (outbox_row(ctx, RESOURCE_UPDATED, resource_id, {}),)
        retired = await self._storage.retire_resource(
            ctx.org_id, resource_id, self._clock(), ctx.user_id, rows
        )
        if retired is None:
            stored = await self._resource(ctx, resource_id)
            return stored  # retired already
        await self._relay.relay_all(ctx.org_id, rows)
        await self._cancel_stranded(ctx)
        return retired

    # The line.

    async def ask(
        self, ctx: TenantContext, request: LeaseRequest, park: Step | None = None
    ) -> Standing:
        ctx.require(Permission.WRITE)
        self._kind(request.kind)
        try:
            ASK_PAYLOADS[request.kind].model_validate(dict(request.payload))
        except ValidationError as error:
            raise ValidationFailed(f"a {request.kind.value} ask is not {error}") from None
        if request.waiter_kind is not None and request.waiter_kind not in self._waiters:
            raise ValidationFailed(f"no waiter of kind {request.waiter_kind.value} is registered")
        if request.resource_id is not None:
            named = await self._resource(ctx, request.resource_id)
            if named.retired_at is not None or named.kind is not request.kind:
                raise NotFound(f"no live {request.kind.value} resource {request.resource_id}")
        now = self._clock()
        asked = request.model_copy(
            update={
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "wait_until": now + timedelta(seconds=request.wait_seconds),
                "rank": 0.0,
                "status": RequestStatus.WAITING,
                "end_reason": None,
                "lease_id": None,
            }
        )
        rows = (outbox_row(ctx, REQUEST_CREATED, asked.id, {}),)
        stored, created = await self._storage.create_request(ctx.org_id, asked, rows)
        if created:
            await self._relay.relay_all(ctx.org_id, rows)
        if stored.status is RequestStatus.WAITING:
            # Last in line, so an offer grants it only when no one waits in
            # front of it.
            for resource in await self._candidates(ctx, stored):
                await self._offer(ctx, resource.id)
            stored = await self._storage.read_request(ctx.org_id, stored.id) or stored
        if stored.status is RequestStatus.WAITING and park is not None:
            parked_rows = step_rows(ctx, park.record)
            stood = await self._storage.park_waiting(ctx.org_id, stored.id, park, parked_rows)
            if stood is not None and stood.status is RequestStatus.WAITING:
                await self._relay.relay_all(ctx.org_id, parked_rows)
            else:
                stored = stood or stored
        return await self._standing(ctx, stored)

    async def get_request(self, ctx: TenantContext, request_id: UUID) -> Standing:
        ctx.require(Permission.READ)
        return await self._standing(ctx, await self._request(ctx, request_id))

    async def line(self, ctx: TenantContext, resource_id: UUID) -> Line:
        ctx.require(Permission.READ)
        resource = await self._resource(ctx, resource_id)
        waiting = await self._storage.read_waiting(
            ctx.org_id, resource.kind, self._options.line_limit
        )
        return Line(resource=resource, requests=tuple(line_of(resource, waiting)))

    async def cancel(self, ctx: TenantContext, request_id: UUID) -> LeaseRequest:
        ctx.require(Permission.WRITE)
        request = await self._request(ctx, request_id)
        if request.created_by != ctx.user_id:
            ctx.require(Permission.MANAGE_MEMBERS)
        if request.status is not RequestStatus.WAITING:
            return request
        settled = await self._settle(ctx, request, RequestStatus.CANCELLED, EndReason.ASKED)
        return settled or await self._request(ctx, request_id)

    async def reorder(
        self, ctx: TenantContext, request_id: UUID, before_id: UUID | None
    ) -> LeaseRequest:
        ctx.require(Permission.MANAGE_MEMBERS)
        request = await self._request(ctx, request_id)
        if request.status is not RequestStatus.WAITING:
            raise ValidationFailed(
                f"request {request_id} is {request.status.value}; only one waiting moves"
            )
        waiting = await self._storage.read_waiting(
            ctx.org_id, request.kind, self._options.line_limit
        )
        others = [r for r in waiting if r.id != request.id]
        index = len(others)
        if before_id is not None:
            found = next((i for i, r in enumerate(others) if r.id == before_id), None)
            if found is None:
                raise ValidationFailed(f"request {before_id} does not wait beside {request_id}")
            index = found
        rows = (outbox_row(ctx, REQUEST_UPDATED, request.id, {}),)
        moved = await self._storage.rank_request(
            ctx.org_id,
            request.id,
            rank_at([r.rank for r in others], index),
            self._clock(),
            ctx.user_id,
            rows,
        )
        if moved is None:
            raise ValidationFailed(f"request {request_id} no longer waits")
        await self._relay.relay_all(ctx.org_id, rows)
        # At the front, it may now head the line of a free resource.
        for resource in await self._candidates(ctx, moved):
            await self._offer(ctx, resource.id)
        return await self._storage.read_request(ctx.org_id, moved.id) or moved

    async def leave(self, ctx: TenantContext, waiter_kind: WaiterKind, waiter_id: UUID) -> int:
        ctx.require(Permission.WRITE)
        left = await self._storage.cancel_waiting_of(
            ctx.org_id, waiter_kind, waiter_id, self._clock(), ctx.user_id
        )
        if left:
            log.info(
                "%s %s left %d lines in org %s", waiter_kind.value, waiter_id, left, ctx.org_id
            )
        return left

    # Leases.

    async def get_lease(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        ctx.require(Permission.READ)
        return await self._lease(ctx, lease_id)

    async def renew(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        ctx.require(Permission.WRITE)
        lease = await self._held(ctx, lease_id)
        resource = await self._storage.read_resource(ctx.org_id, lease.resource_id)
        bound = lease.term_seconds if resource is None else resource.max_term_seconds
        now = self._clock()
        expires_at = now + timedelta(seconds=min(lease.term_seconds, bound))
        renewed = await self._storage.renew_lease(
            ctx.org_id, lease_id, now, expires_at, ctx.user_id
        )
        if renewed is None:
            raise LeaseEnded(f"lease {lease_id} has ended")
        return renewed

    async def release(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        ctx.require(Permission.WRITE)
        lease = await self._held(ctx, lease_id)
        return await self._end_or_refuse(ctx, lease, LeaseStatus.RELEASED)

    async def revoke(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        ctx.require(Permission.MANAGE_MEMBERS)
        lease = await self._lease(ctx, lease_id)
        return await self._end_or_refuse(ctx, lease, LeaseStatus.REVOKED)

    # The sweep.

    async def sweep(self, rctx: RequestContext) -> int:
        """Visits up to `sweep_orgs` live orgs with something due. A deleted
        org stays due until its purge takes its rows, so the sweep reads on
        past it, and it takes no live org's place in the batch."""
        now = self._clock()
        lapsed_before = now - self._options.margin
        ended = visited = 0
        after: UUID | None = None
        while True:
            due = await self._storage.read_due_orgs(
                now, lapsed_before, self._options.sweep_orgs, after
            )
            for org_id in due:
                try:
                    ctx = await self._tenancy.service_context(rctx, org_id, EMPTY_UUID)
                except InvalidCredential:
                    continue  # a deleted org: its rows go with its purge
                try:
                    ended += await self._sweep_org(ctx)
                except Exception:
                    log.exception("sweep: leases of org %s failed", org_id)
                visited += 1
                if visited == self._options.sweep_orgs:
                    return ended
            if len(due) < self._options.sweep_orgs:
                return ended
            after = due[-1]

    async def purge_across_tenants(self) -> int:
        return await self._storage.purge_settled(
            self._clock() - self._options.retention, self._options.purge_batch
        )

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    # The grant.

    async def _offer(self, ctx: TenantContext, resource_id: UUID) -> Lease | None:
        """Offers a resource to its line: the head is granted when it still
        waits, its waiter still waits, and its kind still allows it; a head
        that fails one of those leaves the line, and the next is asked. A
        grant that loses to another writer of the resource stops here: that
        writer offers it in turn."""
        for _ in range(self._options.offer_tries):
            resource = await self._storage.read_resource(ctx.org_id, resource_id)
            if resource is None:
                return None
            now = self._clock()
            if holds_lapsed(resource, now - self._options.margin) and resource.lease_id:
                # Its holder's clock has run out too: the lease ends first.
                lapsed = await self._storage.read_lease(ctx.org_id, resource.lease_id)
                if lapsed is not None:
                    await self._end(ctx, lapsed, LeaseStatus.EXPIRED, offer=False)
                continue
            if not is_grantable(resource):
                return None
            waiting = await self._storage.read_waiting(
                ctx.org_id, resource.kind, self._options.line_limit
            )
            line = line_of(resource, waiting)
            if not line:
                return None
            head = line[0]
            if head.wait_until is not None and head.wait_until <= now:
                await self._settle(ctx, head, RequestStatus.EXPIRED, None)
                continue
            if not await self._still_waits(ctx, head):
                await self._settle(ctx, head, RequestStatus.CANCELLED, EndReason.WAITER_GONE)
                continue
            if not await self._kind(resource.kind).may_grant(ctx, resource, head):
                await self._settle(ctx, head, RequestStatus.CANCELLED, EndReason.REFUSED)
                continue
            granted = await self._grant(ctx, resource, head, now)
            if granted is not None:
                return granted
            again = await self._storage.read_request(ctx.org_id, head.id)
            if again is not None and again.status is RequestStatus.WAITING:
                return None  # the resource moved under the grant
        return None

    async def _grant(
        self, ctx: TenantContext, resource: Resource, request: LeaseRequest, now: datetime
    ) -> Lease | None:
        term = term_of(request, resource)
        lease = Lease(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            resource_id=resource.id,
            request_id=request.id,
            holder_id=request.created_by,
            token=resource.token + 1,
            term_seconds=int(term.total_seconds()),
            expires_at=now + term,
        )
        rows: tuple[OutboxRow, ...] = (
            outbox_row(ctx, LEASE_CREATED, lease.id, {}),
            outbox_row(ctx, REQUEST_UPDATED, request.id, {}),
            outbox_row(ctx, RESOURCE_UPDATED, resource.id, {}),
            *self._kind(resource.kind).grant_rows(ctx, resource, request, lease),
        )
        if request.waiter_kind is not None and request.waiter_id is not None:
            waiter = self._waiters[request.waiter_kind]
            rows = (*rows, *waiter.wake_rows(ctx, request.waiter_id, lease))
        granted = await self._storage.grant(
            ctx.org_id, Grant(lease=lease, expected_token=resource.token), rows
        )
        if granted is None:
            return None
        await self._relay.relay_all(ctx.org_id, rows)
        log.info(
            "granted %s %s to request %s under token %d in org %s",
            resource.kind.value,
            resource.id,
            request.id,
            lease.token,
            ctx.org_id,
        )
        OUTCOMES.labels(subsystem="leases", outcome="granted").inc()
        return granted

    async def _end(
        self,
        ctx: TenantContext,
        lease: Lease,
        status: LeaseStatus,
        *,
        lapsed_before: datetime | None = None,
        offer: bool = True,
    ) -> Lease | None:
        """Ends an active lease and frees its resource, then offers it to its
        line. None when another writer ended it first."""
        now = self._clock()
        resource = await self._storage.read_resource(ctx.org_id, lease.resource_id)
        mean = measured_hold(
            None if resource is None else resource.mean_hold_seconds, now - lease.created_at
        )
        rows: tuple[OutboxRow, ...] = (
            outbox_row(ctx, LEASE_UPDATED, lease.id, {}),
            outbox_row(ctx, RESOURCE_UPDATED, lease.resource_id, {}),
        )
        if status is LeaseStatus.REVOKED:
            request = await self._storage.read_request(ctx.org_id, lease.request_id)
            if request is not None and request.waiter_kind is not None and request.waiter_id:
                waiter = self._waiters.get(request.waiter_kind)
                if waiter is not None:
                    rows = (*rows, *waiter.revoke_rows(ctx, request.waiter_id, lease))
        ended = await self._storage.end_lease(
            ctx.org_id, lease.id, status, now, ctx.user_id, mean, rows, lapsed_before
        )
        if ended is None:
            return None
        await self._relay.relay_all(ctx.org_id, rows)
        OUTCOMES.labels(subsystem="leases", outcome=status.value).inc()
        if offer:
            await self._offer(ctx, lease.resource_id)
        return ended

    async def _end_or_refuse(self, ctx: TenantContext, lease: Lease, status: LeaseStatus) -> Lease:
        """A holder's release or a manager's revocation: answered as it is when
        it already ended that way, refused when it ended otherwise."""
        if lease.status is LeaseStatus.ACTIVE:
            ended = await self._end(ctx, lease, status)
            if ended is not None:
                return ended
            lease = await self._lease(ctx, lease.id)
        if lease.status is status:
            return lease
        raise LeaseEnded(f"lease {lease.id} is {lease.status.value}")

    async def _settle(
        self,
        ctx: TenantContext,
        request: LeaseRequest,
        status: RequestStatus,
        reason: EndReason | None,
    ) -> LeaseRequest | None:
        ended = request.model_copy(update={"status": status, "end_reason": reason})
        rows = (outbox_row(ctx, REQUEST_UPDATED, request.id, {}), *self._end_rows(ctx, ended))
        settled = await self._storage.settle_request(
            ctx.org_id, request.id, status, reason, self._clock(), ctx.user_id, rows
        )
        if settled is not None:
            await self._relay.relay_all(ctx.org_id, rows)
            outcome = status.value if reason is None else reason.value
            OUTCOMES.labels(subsystem="leases", outcome=f"request_{outcome}").inc()
        return settled

    def _end_rows(self, ctx: TenantContext, request: LeaseRequest) -> tuple[OutboxRow, ...]:
        """The rows that wake a request's waiter when the request leaves its
        line without a lease; a waiter that is gone is told nothing."""
        if request.waiter_kind is None or request.waiter_id is None:
            return ()
        if request.end_reason is EndReason.WAITER_GONE:
            return ()
        waiter = self._waiters.get(request.waiter_kind)
        return () if waiter is None else waiter.end_rows(ctx, request.waiter_id, request)

    async def _cancel_stranded(self, ctx: TenantContext) -> int:
        """Takes the requests that name a retired resource out of their line
        as `retired`, each in its own commit with its waiter's wake. The
        retirement lands with its owner's row, which knows no waiter."""
        cancelled = 0
        for request in await self._storage.read_stranded(ctx.org_id, self._options.sweep_batch):
            if await self._settle(ctx, request, RequestStatus.CANCELLED, EndReason.RETIRED):
                cancelled += 1
        return cancelled

    async def _sweep_org(self, ctx: TenantContext) -> int:
        """One org's pass: the lapsed leases end, the overdue requests expire,
        the requests for a retired resource leave their line, and every free
        resource with a line is offered to it, which also mends an offer a
        crash cut short."""
        ended = 0
        lapsed_before = self._clock() - self._options.margin
        for lease in await self._storage.read_lapsed(
            ctx.org_id, lapsed_before, self._options.sweep_batch
        ):
            if await self._end(ctx, lease, LeaseStatus.EXPIRED, lapsed_before=lapsed_before):
                ended += 1
        for request in await self._storage.read_overdue(
            ctx.org_id, self._clock(), self._options.sweep_batch
        ):
            if await self._settle(ctx, request, RequestStatus.EXPIRED, None):
                ended += 1
        ended += await self._cancel_stranded(ctx)
        free = await self._storage.read_free(ctx.org_id, self._options.resource_limit)
        lines: dict[ResourceKind, list[LeaseRequest]] = {}
        for resource in free:
            if resource.kind not in lines:
                lines[resource.kind] = await self._storage.read_waiting(
                    ctx.org_id, resource.kind, self._options.line_limit
                )
            if any(stands_in(r, resource) for r in lines[resource.kind]):
                await self._offer(ctx, resource.id)
        return ended

    # Reads.

    async def _standing(self, ctx: TenantContext, request: LeaseRequest) -> Standing:
        if request.status is RequestStatus.GRANTED and request.lease_id is not None:
            lease = await self._storage.read_lease(ctx.org_id, request.lease_id)
            return Standing(request=request, lease=lease)
        if request.status is not RequestStatus.WAITING:
            return Standing(request=request)
        resources = await self._storage.read_resources(
            ctx.org_id, request.kind, self._options.resource_limit
        )
        if request.resource_id is not None and all(r.id != request.resource_id for r in resources):
            named = await self._storage.read_resource(ctx.org_id, request.resource_id)
            resources = [*resources, *([] if named is None else [named])]
        waiting = await self._storage.read_waiting(
            ctx.org_id, request.kind, self._options.line_limit
        )
        return Standing(
            request=request,
            place=place_of(request, resources, waiting),
            estimate_seconds=replay(request, resources, waiting, self._clock()),
        )

    async def _candidates(self, ctx: TenantContext, request: LeaseRequest) -> list[Resource]:
        """The free resources a waiting request may take."""
        if request.resource_id is not None:
            named = await self._storage.read_resource(ctx.org_id, request.resource_id)
            found = [] if named is None else [named]
        else:
            found = await self._storage.read_resources(
                ctx.org_id, request.kind, self._options.resource_limit
            )
        return [r for r in found if is_grantable(r) and stands_in(request, r)]

    async def _still_waits(self, ctx: TenantContext, request: LeaseRequest) -> bool:
        if request.waiter_kind is None or request.waiter_id is None:
            return True
        waiter = self._waiters.get(request.waiter_kind)
        return waiter is not None and await waiter.still_waits(ctx, request.waiter_id)

    def _kind(self, kind: ResourceKind) -> ResourceKindInterface:
        hooks = self._kinds.get(kind)
        if hooks is None:
            raise ValidationFailed(f"no resource kind {kind.value} is registered")
        return hooks

    async def _held(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        """A lease its holder acts on: only the principal it was granted to."""
        lease = await self._lease(ctx, lease_id)
        if lease.holder_id != ctx.user_id:
            raise NotAuthorized(f"lease {lease_id} is not held by {ctx.user_id}")
        return lease

    async def _resource(self, ctx: TenantContext, resource_id: UUID) -> Resource:
        resource = await self._storage.read_resource(ctx.org_id, resource_id)
        if resource is None:
            raise NotFound(f"resource {resource_id} not found")
        return resource

    async def _request(self, ctx: TenantContext, request_id: UUID) -> LeaseRequest:
        request = await self._storage.read_request(ctx.org_id, request_id)
        if request is None:
            raise NotFound(f"lease request {request_id} not found")
        return request

    async def _lease(self, ctx: TenantContext, lease_id: UUID) -> Lease:
        lease = await self._storage.read_lease(ctx.org_id, lease_id)
        if lease is None:
            raise NotFound(f"lease {lease_id} not found")
        return lease
