from datetime import datetime
from uuid import UUID

from acme.om.exceptions import TenantMismatch
from acme.om.leases.rules import stands_in
from acme.om.leases.storage import LeasesStorageInterface, ResourceLandingInterface
from acme.om.leases.types.lease import Grant, Lease, LeaseStatus
from acme.om.leases.types.request import (
    EndReason,
    LeaseRequest,
    RequestStatus,
    WaiterKind,
)
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.orchestrations.storage import StepLandingInterface
from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class LeasesStorageMemoryImpl(MemoryStorageBase, LeasesStorageInterface, ResourceLandingInterface):
    """The twin. Its one lock stands in for the anchor's row lock and the
    request's: every write reads and writes under it with no await between,
    so the caller that arrives second is refused against the state the first
    left."""

    def __init__(
        self,
        outbox: OutboxLandingInterface | None = None,
        steps: StepLandingInterface | None = None,
    ) -> None:
        super().__init__(outbox)
        self._steps = steps
        self._resources: MemoryTable[Resource] = {}
        self._leases: MemoryTable[Lease] = {}
        self._requests: MemoryTable[LeaseRequest] = {}

    # Resources.

    async def create_resource(
        self, org_id: UUID, resource: Resource, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            if self._by_ref(org_id, resource.kind, resource.ref_id) is not None:
                return False  # the (org, kind, ref) key, as the index is in Postgres
            return self._insert(self._resources, org_id, resource, outbox_rows)

    async def read_resource(self, org_id: UUID, resource_id: UUID) -> Resource | None:
        return self._get(self._resources, org_id, resource_id)

    async def read_resource_by_ref(
        self, org_id: UUID, kind: ResourceKind, ref_id: UUID
    ) -> Resource | None:
        return self._by_ref(org_id, kind, ref_id)

    async def read_resources(self, org_id: UUID, kind: ResourceKind, limit: int) -> list[Resource]:
        return [
            r
            for r in self._rows(self._resources, org_id)
            if r.kind is kind and r.retired_at is None
        ][:limit]

    async def read_free(self, org_id: UUID, limit: int) -> list[Resource]:
        return [
            r
            for r in self._rows(self._resources, org_id)
            if r.lease_id is None and r.available and r.retired_at is None
        ][:limit]

    async def write_availability(
        self,
        org_id: UUID,
        resource_id: UUID,
        available: bool,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        async with self._lock:
            resource = self._get(self._resources, org_id, resource_id)
            if resource is None or resource.retired_at is not None:
                return None
            written = resource.model_copy(
                update={"available": available, "updated_at": at, "updated_by": actor}
            )
            self._put(self._resources, org_id, written, outbox_rows)
            return written

    async def retire_resource(
        self,
        org_id: UUID,
        resource_id: UUID,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        async with self._lock:
            resource = self._get(self._resources, org_id, resource_id)
            if resource is None or resource.retired_at is not None:
                return None
            self._land(org_id, outbox_rows)
            self.land_retirement(org_id, resource.kind, resource.ref_id, at, actor)
            return self._get(self._resources, org_id, resource_id)

    # Requests.

    async def create_request(
        self, org_id: UUID, request: LeaseRequest, outbox_rows: tuple[OutboxRow, ...]
    ) -> tuple[LeaseRequest, bool]:
        async with self._lock:
            stored = next(
                (
                    r
                    for r in self._rows(self._requests, org_id)
                    if r.idempotency_key == request.idempotency_key
                ),
                None,
            ) or self._get(self._requests, org_id, request.id)
            if stored is not None:
                return stored, False
            if request.id in self._requests:
                raise TenantMismatch(f"lease request {request.id} is not in {org_id}")
            ranks = [r.rank for r in self._waiting_in(org_id)]
            ranked = request.model_copy(update={"rank": (max(ranks) if ranks else 0.0) + 1.0})
            self._insert(self._requests, org_id, ranked, outbox_rows)
            return ranked, True

    async def read_request(self, org_id: UUID, request_id: UUID) -> LeaseRequest | None:
        return self._get(self._requests, org_id, request_id)

    async def read_waiting(
        self, org_id: UUID, kind: ResourceKind, limit: int
    ) -> list[LeaseRequest]:
        found = [r for r in self._waiting_in(org_id) if r.kind is kind]
        return sorted(found, key=lambda r: (r.rank, r.id))[:limit]

    async def read_overdue(self, org_id: UUID, now: datetime, limit: int) -> list[LeaseRequest]:
        found = [
            r for r in self._waiting_in(org_id) if r.wait_until is not None and r.wait_until <= now
        ]
        return sorted(found, key=lambda r: (r.wait_until or now, r.id))[:limit]

    async def settle_request(
        self,
        org_id: UUID,
        request_id: UUID,
        status: RequestStatus,
        reason: EndReason | None,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> LeaseRequest | None:
        async with self._lock:
            return self._write_waiting(
                org_id,
                request_id,
                outbox_rows,
                status=status,
                end_reason=reason,
                updated_at=at,
                updated_by=actor,
            )

    async def rank_request(
        self,
        org_id: UUID,
        request_id: UUID,
        rank: float,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> LeaseRequest | None:
        async with self._lock:
            return self._write_waiting(
                org_id, request_id, outbox_rows, rank=rank, updated_at=at, updated_by=actor
            )

    async def park_waiting(
        self, org_id: UUID, request_id: UUID, step: Step, outbox_rows: tuple[OutboxRow, ...]
    ) -> LeaseRequest | None:
        if self._steps is None:
            raise RuntimeError("this memory storage was built without the records a step lands in")
        async with self._lock:
            request = self._get(self._requests, org_id, request_id)
            if request is None or request.status is not RequestStatus.WAITING:
                return request
            self._steps.check_step(org_id, step)
            self._land(org_id, outbox_rows)
            self._steps.land_step(org_id, step, 0)
            return request

    async def cancel_waiting_of(
        self, org_id: UUID, waiter_kind: WaiterKind, waiter_id: UUID, at: datetime, actor: UUID
    ) -> int:
        async with self._lock:
            mine = [
                r
                for r in self._waiting_in(org_id)
                if r.waiter_kind is waiter_kind and r.waiter_id == waiter_id
            ]
            for request in mine:
                self._requests[request.id] = (
                    org_id,
                    request.model_copy(
                        update={
                            "status": RequestStatus.CANCELLED,
                            "end_reason": EndReason.WAITER_GONE,
                            "updated_at": at,
                            "updated_by": actor,
                        }
                    ),
                )
            return len(mine)

    # Leases.

    async def grant(
        self, org_id: UUID, grant: Grant, outbox_rows: tuple[OutboxRow, ...]
    ) -> Lease | None:
        lease = grant.lease
        if lease.token != grant.expected_token + 1:
            raise ValueError("a grant's token is one above the anchor's")
        async with self._lock:
            anchor = self._get(self._resources, org_id, lease.resource_id)
            request = self._get(self._requests, org_id, lease.request_id)
            if (
                anchor is None
                or anchor.token != grant.expected_token
                or anchor.lease_id is not None
                or not anchor.available
                or anchor.retired_at is not None
                or request is None
                or request.status is not RequestStatus.WAITING
            ):
                return None
            # The second fence, as the unique indexes are in Postgres.
            if any(
                (held.resource_id == lease.resource_id and held.status is LeaseStatus.ACTIVE)
                or held.request_id == lease.request_id
                for held in self._rows(self._leases, org_id)
            ):
                return None
            at, actor = lease.created_at, lease.created_by
            self._land(org_id, outbox_rows)
            self._leases[lease.id] = (org_id, lease)
            self._resources[anchor.id] = (
                org_id,
                anchor.model_copy(
                    update={
                        "token": lease.token,
                        "lease_id": lease.id,
                        "held_until": lease.expires_at,
                        "updated_at": at,
                        "updated_by": actor,
                    }
                ),
            )
            self._requests[request.id] = (
                org_id,
                request.model_copy(
                    update={
                        "status": RequestStatus.GRANTED,
                        "lease_id": lease.id,
                        "updated_at": at,
                        "updated_by": actor,
                    }
                ),
            )
            return lease

    async def read_lease(self, org_id: UUID, lease_id: UUID) -> Lease | None:
        return self._get(self._leases, org_id, lease_id)

    async def read_lapsed(self, org_id: UUID, lapsed_before: datetime, limit: int) -> list[Lease]:
        found = [
            lease
            for lease in self._rows(self._leases, org_id)
            if lease.status is LeaseStatus.ACTIVE and lease.expires_at <= lapsed_before
        ]
        return sorted(found, key=lambda lease: (lease.expires_at, lease.id))[:limit]

    async def renew_lease(
        self, org_id: UUID, lease_id: UUID, now: datetime, expires_at: datetime, actor: UUID
    ) -> Lease | None:
        async with self._lock:
            lease = self._get(self._leases, org_id, lease_id)
            if lease is None or lease.status is not LeaseStatus.ACTIVE or lease.expires_at <= now:
                return None
            anchor = self._get(self._resources, org_id, lease.resource_id)
            if anchor is None or anchor.lease_id != lease_id or anchor.retired_at is not None:
                return None
            renewed = lease.model_copy(
                update={"expires_at": expires_at, "updated_at": now, "updated_by": actor}
            )
            self._leases[lease_id] = (org_id, renewed)
            self._resources[anchor.id] = (
                org_id,
                anchor.model_copy(
                    update={"held_until": expires_at, "updated_at": now, "updated_by": actor}
                ),
            )
            return renewed

    async def end_lease(
        self,
        org_id: UUID,
        lease_id: UUID,
        status: LeaseStatus,
        at: datetime,
        actor: UUID,
        mean_hold_seconds: float,
        outbox_rows: tuple[OutboxRow, ...],
        lapsed_before: datetime | None = None,
    ) -> Lease | None:
        async with self._lock:
            lease = self._get(self._leases, org_id, lease_id)
            if lease is None or lease.status is not LeaseStatus.ACTIVE:
                return None
            if lapsed_before is not None and lease.expires_at > lapsed_before:
                return None
            ended = lease.model_copy(
                update={"status": status, "ended_at": at, "updated_at": at, "updated_by": actor}
            )
            self._land(org_id, outbox_rows)
            self._leases[lease_id] = (org_id, ended)
            anchor = self._get(self._resources, org_id, lease.resource_id)
            if anchor is not None and anchor.lease_id == lease_id:
                self._resources[anchor.id] = (
                    org_id,
                    anchor.model_copy(
                        update={
                            "lease_id": None,
                            "held_until": None,
                            "mean_hold_seconds": mean_hold_seconds,
                            "updated_at": at,
                            "updated_by": actor,
                        }
                    ),
                )
            return ended

    # The sweep.

    async def read_due_orgs(self, now: datetime, lapsed_before: datetime, limit: int) -> list[UUID]:
        due: set[UUID] = set()
        for org_id, lease in self._rows_across_tenants(self._leases):
            if lease.status is LeaseStatus.ACTIVE and lease.expires_at <= lapsed_before:
                due.add(org_id)
        waiting: dict[UUID, list[LeaseRequest]] = {}
        for org_id, request in self._rows_across_tenants(self._requests):
            if request.status is not RequestStatus.WAITING:
                continue
            waiting.setdefault(org_id, []).append(request)
            if request.wait_until is not None and request.wait_until <= now:
                due.add(org_id)
        for org_id, resource in self._rows_across_tenants(self._resources):
            # Free, with a request in its own line, as the Postgres impl reads it.
            free = resource.lease_id is None and resource.available and resource.retired_at is None
            if free and any(stands_in(request, resource) for request in waiting.get(org_id, ())):
                due.add(org_id)
        return sorted(due)[:limit]

    async def purge_settled(self, before: datetime, limit: int) -> int:
        async with self._lock:
            leases = [
                lease.id
                for _, lease in self._rows_across_tenants(self._leases)
                if lease.ended_at is not None and lease.ended_at < before
            ][:limit]
            requests = [
                r.id
                for _, r in self._rows_across_tenants(self._requests)
                if r.status is not RequestStatus.WAITING and r.updated_at < before
            ][:limit]
            resources = [
                r.id
                for _, r in self._rows_across_tenants(self._resources)
                if r.retired_at is not None and r.retired_at < before and r.lease_id is None
            ][:limit]
            for table, gone in (
                (self._leases, leases),
                (self._requests, requests),
                (self._resources, resources),
            ):
                for row_id in gone:
                    del table[row_id]
            return len(leases) + len(requests) + len(resources)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            purged = 0
            for table in (self._leases, self._requests, self._resources):
                gone = [row_id for row_id, (row_org, _) in table.items() if row_org == org_id]
                for row_id in gone[:limit]:
                    del table[row_id]
                purged += len(gone[:limit])
            return purged

    # The landing: called by another namespace's memory storage, in the same
    # step as its own row, as the statements share its transaction in
    # Postgres. Both are synchronous, so nothing runs between them.

    def land_resource(self, org_id: UUID, resource: Resource) -> None:
        if self._by_ref(org_id, resource.kind, resource.ref_id) is None:
            self._resources.setdefault(resource.id, (org_id, resource))

    def land_retirement(
        self, org_id: UUID, kind: ResourceKind, ref_id: UUID, at: datetime, actor: UUID
    ) -> None:
        resource = self._by_ref(org_id, kind, ref_id)
        if resource is None or resource.retired_at is not None:
            return
        self._resources[resource.id] = (
            org_id,
            resource.model_copy(
                update={
                    "retired_at": at,
                    "available": False,
                    "updated_at": at,
                    "updated_by": actor,
                }
            ),
        )
        for request in self._waiting_in(org_id):
            if request.resource_id == resource.id:
                self._requests[request.id] = (
                    org_id,
                    request.model_copy(
                        update={
                            "status": RequestStatus.CANCELLED,
                            "end_reason": EndReason.RETIRED,
                            "updated_at": at,
                            "updated_by": actor,
                        }
                    ),
                )

    # The shared steps.

    def _by_ref(self, org_id: UUID, kind: ResourceKind, ref_id: UUID) -> Resource | None:
        return next(
            (
                r
                for r in self._rows(self._resources, org_id)
                if r.kind is kind and r.ref_id == ref_id
            ),
            None,
        )

    def _waiting_in(self, org_id: UUID) -> list[LeaseRequest]:
        return [r for r in self._rows(self._requests, org_id) if r.status is RequestStatus.WAITING]

    def _write_waiting(
        self,
        org_id: UUID,
        request_id: UUID,
        outbox_rows: tuple[OutboxRow, ...],
        **fields: object,
    ) -> LeaseRequest | None:
        request = self._get(self._requests, org_id, request_id)
        if request is None or request.status is not RequestStatus.WAITING:
            return None
        written = request.model_copy(update=fields)
        self._land(org_id, outbox_rows)
        self._requests[request_id] = (org_id, written)
        return written
