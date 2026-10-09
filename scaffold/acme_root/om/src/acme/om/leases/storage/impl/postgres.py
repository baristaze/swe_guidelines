from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    CursorResult,
    Insert,
    Update,
    and_,
    exists,
    func,
    or_,
    select,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from acme.om.base import EMPTY_UUID
from acme.om.exceptions import TenantMismatch, UniqueKeyTaken
from acme.om.leases.storage import LeasesStorageInterface
from acme.om.leases.storage.tables.lease_requests import LeaseRequests
from acme.om.leases.storage.tables.leases import Leases
from acme.om.leases.storage.tables.resources import Resources
from acme.om.leases.types.lease import Grant, Lease, LeaseStatus
from acme.om.leases.types.request import EndReason, LeaseRequest, RequestStatus, WaiterKind
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.orchestrations.storage.impl.postgres import land_step
from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PLAN_WITH_VALUES, PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values

REF_KEY = "uq_resources_org_id_kind_ref_id"
WAITING = RequestStatus.WAITING.value
ACTIVE = LeaseStatus.ACTIVE.value


def register_statement(org_id: UUID, resource: Resource) -> Insert:
    """The registration as a companion statement, run by the storage of the
    namespace that owns the kind, in the transaction that writes its own row:
    the resource lands with it, or not at all. A kind and row registered
    already leave the stored resource as it is."""
    values = {**to_values(resource, Resources), "org_id": org_id}
    return pg_insert(Resources).values(**values).on_conflict_do_nothing()


def retire_statements(
    org_id: UUID, kind: ResourceKind, ref_id: UUID, at: datetime, actor: UUID
) -> tuple[Update, Update]:
    """The retirement as two companion statements, in the transaction that
    retires the owner's row: the resource first, which takes the anchor's lock,
    then the requests waiting for it by name, cancelled as `retired`. Its
    lease runs on until it ends, and is never renewed."""
    retired = (
        update(Resources)
        .where(
            Resources.org_id == org_id,
            Resources.kind == kind.value,
            Resources.ref_id == ref_id,
            Resources.retired_at.is_(None),
        )
        .values(retired_at=at, available=False, updated_at=at, updated_by=actor)
    )
    named = select(Resources.id).where(
        Resources.org_id == org_id, Resources.kind == kind.value, Resources.ref_id == ref_id
    )
    cancelled = (
        update(LeaseRequests)
        .where(
            LeaseRequests.org_id == org_id,
            LeaseRequests.status == WAITING,
            LeaseRequests.resource_id.in_(named.scalar_subquery()),
        )
        .values(
            status=RequestStatus.CANCELLED.value,
            end_reason=EndReason.RETIRED.value,
            updated_at=at,
            updated_by=actor,
        )
    )
    return retired, cancelled


def _land(session: AsyncSession, org_id: UUID, outbox_rows: tuple[OutboxRow, ...]) -> None:
    for outbox_row in outbox_rows:
        session.add(to_row(outbox_row, OutboxRows, org_id=org_id))


def _stamp(row: Any, at: datetime, actor: UUID) -> None:
    row.updated_at = at
    row.updated_by = actor


class LeasesStoragePostgresImpl(PgStorageBase, LeasesStorageInterface):
    # Resources.

    async def create_resource(
        self, org_id: UUID, resource: Resource, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        try:
            return await self._insert(Resources, org_id, resource, outbox_rows)
        except UniqueKeyTaken as taken:
            # The kind and row are registered under another id; that one stands.
            if REF_KEY in taken.message:
                return False
            raise

    async def read_resource(self, org_id: UUID, resource_id: UUID) -> Resource | None:
        stmt = select(Resources).where(Resources.org_id == org_id, Resources.id == resource_id)
        return await self._one(stmt, org_id, Resource)

    async def read_resource_by_ref(
        self, org_id: UUID, kind: ResourceKind, ref_id: UUID
    ) -> Resource | None:
        stmt = select(Resources).where(
            Resources.org_id == org_id, Resources.kind == kind.value, Resources.ref_id == ref_id
        )
        return await self._one(stmt, org_id, Resource)

    async def read_resources(self, org_id: UUID, kind: ResourceKind, limit: int) -> list[Resource]:
        stmt = (
            select(Resources)
            .where(
                Resources.org_id == org_id,
                Resources.kind == kind.value,
                Resources.retired_at.is_(None),
            )
            .order_by(Resources.id)
            .limit(limit)
        )
        return await self._all(stmt, org_id, Resource)

    async def read_free(self, org_id: UUID, limit: int) -> list[Resource]:
        stmt = (
            select(Resources)
            .where(
                Resources.org_id == org_id,
                Resources.lease_id.is_(None),
                Resources.available.is_(True),
                Resources.retired_at.is_(None),
            )
            .order_by(Resources.id)
            .limit(limit)
        )
        return await self._all(stmt, org_id, Resource)

    async def write_availability(
        self,
        org_id: UUID,
        resource_id: UUID,
        available: bool,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        async with self._session_for(Resources, org_id=org_id) as session:
            anchor = await self._anchor(session, org_id, resource_id)
            if anchor is None or anchor.retired_at is not None:
                await session.rollback()
                return None
            anchor.available = available
            _stamp(anchor, at, actor)
            _land(session, org_id, outbox_rows)
            await session.flush()
            stored = to_model(anchor, Resource)
            await session.commit()
            return stored

    async def retire_resource(
        self,
        org_id: UUID,
        resource_id: UUID,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Resource | None:
        async with self._session_for(Resources, org_id=org_id) as session:
            anchor = await self._anchor(session, org_id, resource_id)
            if anchor is None or anchor.retired_at is not None:
                await session.rollback()
                return None
            retired, cancelled = retire_statements(
                org_id, ResourceKind(anchor.kind), anchor.ref_id, at, actor
            )
            await session.execute(retired)
            await session.execute(cancelled)
            _land(session, org_id, outbox_rows)
            await session.commit()
        return await self.read_resource(org_id, resource_id)

    # Requests.

    async def create_request(
        self, org_id: UUID, request: LeaseRequest, outbox_rows: tuple[OutboxRow, ...]
    ) -> tuple[LeaseRequest, bool]:
        async with self._session_for(LeaseRequests, org_id=org_id) as session:
            # Last in the org's one order. Two asks at once may take one rank;
            # the id, minted in time order, breaks the tie as it does everywhere.
            top = (
                await session.execute(
                    select(func.max(LeaseRequests.rank)).where(
                        LeaseRequests.org_id == org_id, LeaseRequests.status == WAITING
                    )
                )
            ).scalar_one_or_none()
            ranked = request.model_copy(update={"rank": (top if top is not None else 0.0) + 1.0})
            values = {**to_values(ranked, LeaseRequests), "org_id": org_id}
            stmt = (
                pg_insert(LeaseRequests)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(LeaseRequests.id)
            )
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                _land(session, org_id, outbox_rows)
                await session.commit()
                return ranked, True
            await session.rollback()
        # Asked before: by its key, or by its id when a retry kept the id.
        by_key = select(LeaseRequests).where(
            LeaseRequests.org_id == org_id, LeaseRequests.idempotency_key == request.idempotency_key
        )
        stored = await self._one(by_key, org_id, LeaseRequest)
        if stored is None:
            stored = await self.read_request(org_id, request.id)
        if stored is None:
            # The id is another tenant's, which the policy hides.
            raise TenantMismatch(f"lease request {request.id} is not in {org_id}")
        return stored, False

    async def read_request(self, org_id: UUID, request_id: UUID) -> LeaseRequest | None:
        stmt = select(LeaseRequests).where(
            LeaseRequests.org_id == org_id, LeaseRequests.id == request_id
        )
        return await self._one(stmt, org_id, LeaseRequest)

    async def read_waiting(
        self, org_id: UUID, kind: ResourceKind, limit: int
    ) -> list[LeaseRequest]:
        stmt = (
            select(LeaseRequests)
            .where(
                LeaseRequests.org_id == org_id,
                LeaseRequests.kind == kind.value,
                LeaseRequests.status == WAITING,
            )
            .order_by(LeaseRequests.rank, LeaseRequests.id)
            .limit(limit)
        )
        return await self._all(stmt, org_id, LeaseRequest)

    async def read_overdue(self, org_id: UUID, now: datetime, limit: int) -> list[LeaseRequest]:
        stmt = (
            select(LeaseRequests)
            .where(
                LeaseRequests.org_id == org_id,
                LeaseRequests.status == WAITING,
                LeaseRequests.wait_until <= now,
            )
            .order_by(LeaseRequests.wait_until)
            .limit(limit)
        )
        return await self._all(stmt, org_id, LeaseRequest)

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
        async with self._session_for(LeaseRequests, org_id=org_id) as session:
            row = await self._waiting(session, org_id, request_id)
            if row is None:
                await session.rollback()
                return None
            row.status = status.value
            row.end_reason = None if reason is None else reason.value
            _stamp(row, at, actor)
            return await self._commit(session, org_id, row, outbox_rows)

    async def rank_request(
        self,
        org_id: UUID,
        request_id: UUID,
        rank: float,
        at: datetime,
        actor: UUID,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> LeaseRequest | None:
        async with self._session_for(LeaseRequests, org_id=org_id) as session:
            row = await self._waiting(session, org_id, request_id)
            if row is None:
                await session.rollback()
                return None
            row.rank = rank
            _stamp(row, at, actor)
            return await self._commit(session, org_id, row, outbox_rows)

    async def park_waiting(
        self, org_id: UUID, request_id: UUID, step: Step, outbox_rows: tuple[OutboxRow, ...]
    ) -> LeaseRequest | None:
        async with self._session_for(LeaseRequests, org_id=org_id) as session:
            stmt = (
                select(LeaseRequests)
                .where(LeaseRequests.org_id == org_id, LeaseRequests.id == request_id)
                .with_for_update()
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                await session.rollback()
                return None
            stood = to_model(row, LeaseRequest)
            if row.status != WAITING:
                await session.rollback()
                return stood
            await land_step(session, org_id, step, 0)
            _land(session, org_id, outbox_rows)
            await session.commit()
            return stood

    async def cancel_waiting_of(
        self, org_id: UUID, waiter_kind: WaiterKind, waiter_id: UUID, at: datetime, actor: UUID
    ) -> int:
        stmt = (
            update(LeaseRequests)
            .where(
                LeaseRequests.org_id == org_id,
                LeaseRequests.status == WAITING,
                LeaseRequests.waiter_kind == waiter_kind.value,
                LeaseRequests.waiter_id == waiter_id,
            )
            .values(
                status=RequestStatus.CANCELLED.value,
                end_reason=EndReason.WAITER_GONE.value,
                updated_at=at,
                updated_by=actor,
            )
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            count = cast(CursorResult[Any], await session.execute(stmt)).rowcount
            await session.commit()
            return count

    # Leases.

    async def grant(
        self, org_id: UUID, grant: Grant, outbox_rows: tuple[OutboxRow, ...]
    ) -> Lease | None:
        lease = grant.lease
        if lease.token != grant.expected_token + 1:
            raise ValueError("a grant's token is one above the anchor's")
        async with self._session_for(Resources, org_id=org_id) as session:
            anchor = await self._anchor(session, org_id, lease.resource_id)
            if (
                anchor is None
                or anchor.token != grant.expected_token
                or anchor.lease_id is not None
                or not anchor.available
                or anchor.retired_at is not None
            ):
                await session.rollback()
                return None
            request = await self._waiting(session, org_id, lease.request_id)
            if request is None:
                await session.rollback()
                return None
            session.add(to_row(lease, Leases, org_id=org_id))
            anchor.token = lease.token
            anchor.lease_id = lease.id
            anchor.held_until = lease.expires_at
            _stamp(anchor, lease.created_at, lease.created_by)
            request.status = RequestStatus.GRANTED.value
            request.lease_id = lease.id
            _stamp(request, lease.created_at, lease.created_by)
            _land(session, org_id, outbox_rows)
            try:
                await session.commit()
            except IntegrityError:
                # The second fence: an active lease of the resource, or a lease
                # of the request, landed past the lock.
                await session.rollback()
                return None
            return lease

    async def read_lease(self, org_id: UUID, lease_id: UUID) -> Lease | None:
        stmt = select(Leases).where(Leases.org_id == org_id, Leases.id == lease_id)
        return await self._one(stmt, org_id, Lease)

    async def read_lapsed(self, org_id: UUID, lapsed_before: datetime, limit: int) -> list[Lease]:
        stmt = (
            select(Leases)
            .where(
                Leases.org_id == org_id,
                Leases.status == ACTIVE,
                Leases.expires_at <= lapsed_before,
            )
            .order_by(Leases.expires_at)
            .limit(limit)
        )
        return await self._all(stmt, org_id, Lease)

    async def renew_lease(
        self, org_id: UUID, lease_id: UUID, now: datetime, expires_at: datetime, actor: UUID
    ) -> Lease | None:
        async with self._session_for(Leases, org_id=org_id) as session:
            anchor = await self._anchor_of(session, org_id, lease_id)
            if anchor is None or anchor.lease_id != lease_id or anchor.retired_at is not None:
                await session.rollback()
                return None
            stmt = (
                select(Leases)
                .where(
                    Leases.org_id == org_id,
                    Leases.id == lease_id,
                    Leases.status == ACTIVE,
                    Leases.expires_at > now,
                )
                .with_for_update()
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                await session.rollback()
                return None
            row.expires_at = expires_at
            _stamp(row, now, actor)
            anchor.held_until = expires_at
            _stamp(anchor, now, actor)
            return await self._commit(session, org_id, row, ())

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
        async with self._session_for(Leases, org_id=org_id) as session:
            anchor = await self._anchor_of(session, org_id, lease_id)
            stmt = select(Leases).where(
                Leases.org_id == org_id, Leases.id == lease_id, Leases.status == ACTIVE
            )
            if lapsed_before is not None:
                stmt = stmt.where(Leases.expires_at <= lapsed_before)
            row = (await session.execute(stmt.with_for_update())).scalar_one_or_none()
            if row is None:
                await session.rollback()
                return None
            row.status = status.value
            row.ended_at = at
            _stamp(row, at, actor)
            if anchor is not None and anchor.lease_id == lease_id:
                anchor.lease_id = None
                anchor.held_until = None
                anchor.mean_hold_seconds = mean_hold_seconds
                _stamp(anchor, at, actor)
            return await self._commit(session, org_id, row, outbox_rows)

    # The sweep.

    async def read_due_orgs(
        self, now: datetime, lapsed_before: datetime, limit: int, after: UUID | None = None
    ) -> list[UUID]:
        lapsed = select(Leases.org_id).where(
            Leases.status == ACTIVE, Leases.expires_at <= lapsed_before
        )
        overdue = select(LeaseRequests.org_id).where(
            LeaseRequests.status == WAITING, LeaseRequests.wait_until <= now
        )
        # A free resource with a request in its line: one that names it, or a
        # selector whose labels are all among its own. A waiter in another
        # line of the kind does not make it due, or an org would stay due
        # for as long as that line's resource is held, and enough such orgs
        # would keep every other out of the batch.
        in_line = exists().where(
            LeaseRequests.org_id == Resources.org_id,
            LeaseRequests.kind == Resources.kind,
            LeaseRequests.status == WAITING,
            or_(
                LeaseRequests.resource_id == Resources.id,
                and_(
                    LeaseRequests.resource_id.is_(None),
                    Resources.labels.contains(LeaseRequests.labels),
                ),
            ),
        )
        free = select(Resources.org_id).where(
            Resources.lease_id.is_(None),
            Resources.available.is_(True),
            Resources.retired_at.is_(None),
            in_line,
        )
        due = union(lapsed, overdue, free).subquery()
        stmt = select(due.c.org_id).order_by(due.c.org_id).limit(limit)
        if after is not None:
            stmt = stmt.where(due.c.org_id > after)
        # Every tenant's, so the system scope, spelled here.
        async with self._session_for(Leases, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            return [org_id for (org_id,) in (await session.execute(stmt)).all()]

    async def purge_settled(self, before: datetime, limit: int) -> int:
        statements = (
            delete_batch(
                Leases, Leases.ended_at.is_not(None), Leases.ended_at < before, limit=limit
            ),
            delete_batch(
                LeaseRequests,
                LeaseRequests.status != WAITING,
                LeaseRequests.updated_at < before,
                limit=limit,
            ),
            delete_batch(
                Resources,
                Resources.retired_at.is_not(None),
                Resources.retired_at < before,
                Resources.lease_id.is_(None),
                limit=limit,
            ),
        )
        purged = 0
        async with self._session_for(Leases, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            for stmt in statements:
                purged += deleted(await session.execute(stmt))
            await session.commit()
        return purged

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        purged = 0
        async with self._session_for(Leases, org_id=org_id) as session:
            for table in (Leases, LeaseRequests, Resources):
                stmt = delete_batch(table, table.org_id == org_id, limit=limit)
                purged += deleted(await session.execute(stmt))
            await session.commit()
        return purged

    # The shared steps.

    @staticmethod
    async def _anchor(session: AsyncSession, org_id: UUID, resource_id: UUID) -> Resources | None:
        """The anchor's row, locked: every writer of a lease or a line of the
        resource takes it first."""
        stmt = (
            select(Resources)
            .where(Resources.org_id == org_id, Resources.id == resource_id)
            .with_for_update()
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _anchor_of(
        self, session: AsyncSession, org_id: UUID, lease_id: UUID
    ) -> Resources | None:
        """The anchor of a lease's resource, locked, before the lease's row."""
        stmt = select(Leases.resource_id).where(Leases.org_id == org_id, Leases.id == lease_id)
        resource_id = (await session.execute(stmt)).scalar_one_or_none()
        if resource_id is None:
            return None
        return await self._anchor(session, org_id, resource_id)

    @staticmethod
    async def _waiting(
        session: AsyncSession, org_id: UUID, request_id: UUID
    ) -> LeaseRequests | None:
        """A request's row, locked, while it waits."""
        stmt = (
            select(LeaseRequests)
            .where(
                LeaseRequests.org_id == org_id,
                LeaseRequests.id == request_id,
                LeaseRequests.status == WAITING,
            )
            .with_for_update()
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _commit(
        session: AsyncSession, org_id: UUID, row: Any, outbox_rows: tuple[OutboxRow, ...]
    ) -> Any:
        """Lands the rows beside the changed row, and answers it as written."""
        _land(session, org_id, outbox_rows)
        await session.flush()
        stored = to_model(row, Lease if isinstance(row, Leases) else LeaseRequest)
        await session.commit()
        return stored

    async def _one[M: Resource | Lease | LeaseRequest](
        self, stmt: Any, org_id: UUID, model: type[M]
    ) -> M | None:
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, model)

    async def _all[M: Resource | Lease | LeaseRequest](
        self, stmt: Any, org_id: UUID, model: type[M]
    ) -> list[M]:
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, model) for row in (await session.execute(stmt)).scalars()]
