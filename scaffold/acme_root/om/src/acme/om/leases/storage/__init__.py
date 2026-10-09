"""Storage of the leases swimlane. Every operation takes org_id first but the
two the sweep runs across tenants. Every write that moves a lease or the
line locks the resource's row first, the anchor, and then the request's, so
two writers of one resource queue behind each other and never deadlock."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.leases.types.lease import Grant, Lease, LeaseStatus
from acme.om.leases.types.request import (
    EndReason,
    HistoryMark,
    LeaseEntry,
    LeaseRequest,
    RequestStatus,
    WaiterKind,
)
from acme.om.leases.types.resource import Resource, ResourceKind, ResourceUpdate
from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox.types.row import OutboxRow


class ResourceLandingInterface(ABC):
    """What another namespace's memory storage needs to register, update, or
    retire a resource in the commit of its own row, the twin of the
    statements the Postgres impls share
    (`leases.storage.impl.postgres.register_statement`, `update_statement`,
    and `retire_statement`)."""

    @abstractmethod
    def land_resource(self, org_id: UUID, resource: Resource) -> None:
        """Registers the resource, unless its kind and row have one already."""
        ...

    @abstractmethod
    def land_update(
        self,
        org_id: UUID,
        kind: ResourceKind,
        ref_id: UUID,
        change: ResourceUpdate,
        at: datetime,
        actor: UUID,
    ) -> None:
        """Updates the live resource of the kind for the row: each field the
        update names, and nothing it leaves out. Its lease and its anchor
        stay as they are. The line is offered it by the manager after the
        commit (`offer`), or at the next sweep."""
        ...

    @abstractmethod
    def land_retirement(
        self, org_id: UUID, kind: ResourceKind, ref_id: UUID, at: datetime, actor: UUID
    ) -> None:
        """Retires the kind's resource for the row. The requests that name it
        wait on until the manager takes them out of line (`read_stranded`),
        each with its waiter's wake, which this commit knows nothing of."""
        ...


class LeasesStorageInterface(ABC):
    # Resources.

    @abstractmethod
    async def create_resource(
        self, org_id: UUID, resource: Resource, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The registration, with its rows in one commit; False, with nothing
        landed, when the id is written already or the org has a resource of
        the kind for the row."""
        ...

    @abstractmethod
    async def read_resource(self, org_id: UUID, resource_id: UUID) -> Resource | None: ...

    @abstractmethod
    async def read_resource_by_ref(
        self, org_id: UUID, kind: ResourceKind, ref_id: UUID
    ) -> Resource | None: ...

    @abstractmethod
    async def read_resources(self, org_id: UUID, kind: ResourceKind, limit: int) -> list[Resource]:
        """The org's live resources of a kind, by id."""
        ...

    @abstractmethod
    async def read_free(self, org_id: UUID, limit: int) -> list[Resource]:
        """The org's live, available resources no lease holds, by id."""
        ...

    @abstractmethod
    async def write_availability(
        self,
        org_id: UUID,
        resource_id: UUID,
        available: bool,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        """Sets whether a live resource may be granted; None when there is no
        live resource by the id."""
        ...

    @abstractmethod
    async def update_resource(
        self,
        org_id: UUID,
        resource_id: UUID,
        change: ResourceUpdate,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        """The owner's update of a live resource, under the anchor's lock, with
        its rows in one commit; its lease and its anchor stay as they are.
        None when there is no live resource by the id."""
        ...

    @abstractmethod
    async def retire_resource(
        self,
        org_id: UUID,
        resource_id: UUID,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        """Retires a live resource. Its lease runs on until it ends, and is
        never renewed; the requests that name it wait on until the manager
        takes them out of line (`read_stranded`). None when there is no live
        resource by the id."""
        ...

    # Requests.

    @abstractmethod
    async def create_request(
        self, org_id: UUID, request: LeaseRequest, outbox_rows: tuple[OutboxRow, ...]
    ) -> tuple[LeaseRequest, bool]:
        """Joins the line: the request lands last in the org's rank order, one
        above the highest rank waiting, with its rows in one commit, and is
        answered with True. An id or a key written already answers the stored
        request and False, with nothing landed."""
        ...

    @abstractmethod
    async def read_request(self, org_id: UUID, request_id: UUID) -> LeaseRequest | None: ...

    @abstractmethod
    async def read_waiting(
        self, org_id: UUID, kind: ResourceKind, limit: int
    ) -> list[LeaseRequest]:
        """The org's waiting requests of a kind in rank order: by rank, then id."""
        ...

    @abstractmethod
    async def read_overdue(self, org_id: UUID, now: datetime, limit: int) -> list[LeaseRequest]:
        """The org's waiting requests past their wait, oldest first."""
        ...

    @abstractmethod
    async def read_stranded(self, org_id: UUID, limit: int) -> list[LeaseRequest]:
        """At most `limit` waiting requests that name a retired resource."""
        ...

    @abstractmethod
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
        """Takes a waiting request out of line as cancelled or expired, under
        its row's lock; None, with nothing landed, when it no longer waits."""
        ...

    @abstractmethod
    async def rank_request(
        self,
        org_id: UUID,
        request_id: UUID,
        rank: float,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> LeaseRequest | None:
        """Moves a waiting request to `rank`; None when it no longer waits."""
        ...

    @abstractmethod
    async def park_waiting(
        self, org_id: UUID, request_id: UUID, step: Step, outbox_rows: tuple[OutboxRow, ...]
    ) -> LeaseRequest | None:
        """Lands the waiter's park (`Step`, with the record's rows) only while
        the request still waits,
        under its row's lock, which the grant takes too: a grant that came
        first leaves the record running, and one that comes after finds it
        parked and wakes it. Answers the request as it stood; raises
        `PreconditionFailed`, landing nothing, when the record moved."""
        ...

    @abstractmethod
    async def cancel_waiting_of(
        self, org_id: UUID, waiter_kind: WaiterKind, waiter_id: UUID, at: datetime, actor: UUID
    ) -> int:
        """Takes every waiting request of one waiter out of line as
        `waiter_gone`; returns how many."""
        ...

    # Leases.

    @abstractmethod
    async def grant(
        self, org_id: UUID, grant: Grant, outbox_rows: tuple[OutboxRow, ...]
    ) -> Lease | None:
        """The grant, under the anchor's lock and then the request's: lands
        the lease, the anchor's next token and holder, the request's answer,
        and the rows, in one commit, only while the anchor holds
        `expected_token` and no lease, the resource is live and available,
        and the request still waits and still fits the resource as its row
        stands (`grant_fits`). None, with nothing landed, otherwise.
        The unique index over a resource's active leases is the second fence."""
        ...

    @abstractmethod
    async def read_lease(self, org_id: UUID, lease_id: UUID) -> Lease | None: ...

    @abstractmethod
    async def read_leases(
        self, org_id: UUID, resource_id: UUID | None, after: HistoryMark | None, limit: int
    ) -> list[LeaseEntry]:
        """The org's leases, or one resource's, newest first (by their grant,
        then their id), whatever their status, each with the request it
        answered, in one read; with `after`, those that sort before it."""
        ...

    @abstractmethod
    async def read_lapsed(self, org_id: UUID, lapsed_before: datetime, limit: int) -> list[Lease]:
        """The org's active leases past their expiry and the margin, oldest first."""
        ...

    @abstractmethod
    async def renew_lease(
        self, org_id: UUID, lease_id: UUID, now: datetime, expires_at: datetime, actor: UUID
    ) -> Lease | None:
        """Moves an active lease's expiry, and the anchor's, to `expires_at`,
        while it has not expired by `now` and holds a live resource; None
        otherwise."""
        ...

    @abstractmethod
    async def start_lease(
        self,
        org_id: UUID,
        lease_id: UUID,
        now: datetime,
        lapsed_before: datetime,
        expires_at: datetime,
        actor: UUID,
    ) -> Lease | None:
        """Starts an active lease's job at `now`, and moves its expiry, and
        the anchor's, to `expires_at`, while its job has not started, it has
        not expired by `lapsed_before` (the clock less the margin), and it
        holds a live resource; None otherwise. A job that waited in its lane
        to the end of its window starts here, where a renewal is refused."""
        ...

    @abstractmethod
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
        """Ends an active lease and frees its anchor, keeping its token, with
        the resource's new measured hold and the rows, in one commit. With
        `lapsed_before`, only a lease that expired before it. None when the
        lease is no longer active (or has been renewed since)."""
        ...

    # The sweep.

    @abstractmethod
    async def read_due_orgs(
        self, now: datetime, lapsed_before: datetime, limit: int, after: UUID | None = None
    ) -> list[UUID]:
        """Cross-tenant, for the sweep, in the system scope: the orgs with
        something due: a lease lapsed before `lapsed_before`, a request past
        its wait or naming a retired resource, or a free resource with a
        request in its line. In the
        order of their ids, after `after` when it is given, so the sweep
        reads past an org it skips."""
        ...

    @abstractmethod
    async def purge_settled(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope: at most `limit`
        each of the leases that ended, the requests that settled, and the
        retired resources no lease holds, before `before`, skipping rows
        another transaction holds; returns how many."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` rows of each table of a deleted tenant past its
        retention; returns how many."""
        ...
