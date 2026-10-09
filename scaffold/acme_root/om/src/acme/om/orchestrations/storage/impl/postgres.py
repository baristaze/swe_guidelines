from datetime import datetime
from uuid import UUID

from sqlalchemy import Update, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from acme.om.base import EMPTY_UUID
from acme.om.exceptions import PreconditionFailed, UniqueKeyTaken
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.tables.orchestrations import Orchestrations
from acme.om.orchestrations.types.orchestration import (
    SETTLED,
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
    Step,
)
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PLAN_WITH_VALUES, PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values

PERIOD_KEY = "uq_orchestrations_org_id_kind_period"


def cas_statement(org_id: UUID, record: Orchestration, expected_version: int) -> Update:
    """The compare-and-set of one record: the version is in the WHERE, so two
    writers from one snapshot cannot both land. Returns the id when it hit."""
    values = {k: v for k, v in to_values(record, Orchestrations).items() if k != "id"}
    return (
        update(Orchestrations)
        .where(
            Orchestrations.id == record.id,
            Orchestrations.org_id == org_id,
            Orchestrations.version == expected_version,
        )
        .values(**values)
        .returning(Orchestrations.id)
    )


def step_statement(org_id: UUID, step: Step, applied: int) -> Update:
    """The step's companion statement, run by the storage of the namespace
    whose rows the step changes, in the transaction that changes them: the
    record as the step left it, its `applied` grown by the rows the effect
    wrote, conditioned on the version the step read. A statement that hits
    no row is a record another holder moved; the caller rolls back."""
    record = step.record.model_copy(update={"applied": step.record.applied + applied})
    return cas_statement(org_id, record, step.expected_version)


async def land_step(session: AsyncSession, org_id: UUID, step: Step, applied: int) -> None:
    """Runs `step_statement` in the caller's transaction and rolls it back,
    with the effect, when the record moved: `PreconditionFailed`."""
    if (await session.execute(step_statement(org_id, step, applied))).scalar_one_or_none() is None:
        await session.rollback()
        raise PreconditionFailed(
            f"orchestration {step.record.id} is no longer at version {step.expected_version}"
        )


class OrchestrationsStoragePostgresImpl(PgStorageBase, OrchestrationsStorageInterface):
    async def create_orchestration(
        self, org_id: UUID, record: Orchestration, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._session_for(Orchestrations, org_id=org_id) as session:
            if record.period is not None:
                # The period's record may exist under another id: the unique
                # key answers, read first so the insert never meets it.
                held = select(Orchestrations.id).where(
                    Orchestrations.org_id == org_id,
                    Orchestrations.kind == record.kind.value,
                    Orchestrations.period == record.period,
                )
                if (await session.execute(held)).first() is not None:
                    return False
        try:
            return await self._insert(Orchestrations, org_id, record, outbox_rows)
        except UniqueKeyTaken as taken:
            # Two sweeps opened the period at once; the other one's record stands.
            if PERIOD_KEY in taken.message:
                return False
            raise

    async def read_orchestration(self, org_id: UUID, record_id: UUID) -> Orchestration | None:
        stmt = select(Orchestrations).where(
            Orchestrations.org_id == org_id, Orchestrations.id == record_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Orchestration)

    async def read_recent(
        self, org_id: UUID, kind: OrchestrationKind, limit: int
    ) -> list[Orchestration]:
        stmt = (
            select(Orchestrations)
            .where(Orchestrations.org_id == org_id, Orchestrations.kind == kind.value)
            .order_by(Orchestrations.id.desc())
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, Orchestration) for row in (await session.execute(stmt)).scalars()]

    async def read_parked(
        self, org_id: UUID, reason: ParkReason, limit: int, after: UUID | None = None
    ) -> list[Orchestration]:
        stmt = (
            select(Orchestrations)
            .where(
                Orchestrations.org_id == org_id,
                Orchestrations.status == OrchestrationStatus.PARKED.value,
                Orchestrations.park_reason == reason.value,
            )
            .order_by(Orchestrations.id)
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(Orchestrations.id > after)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, Orchestration) for row in (await session.execute(stmt)).scalars()]

    async def write_orchestration(
        self,
        org_id: UUID,
        record: Orchestration,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(Orchestrations, org_id=org_id) as session:
            stmt = cas_statement(org_id, record, expected_version)
            if (await session.execute(stmt)).scalar_one_or_none() is None:
                # Moved by another writer, or gone: either way the caller's
                # snapshot is stale. Only a record read under this tenant is
                # ever written, so the question of whose row it is never arises.
                await session.rollback()
                raise PreconditionFailed(
                    f"orchestration {record.id} is no longer at version {expected_version}"
                )
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()

    async def purge_settled(self, before: datetime, limit: int) -> int:
        stmt = delete_batch(
            Orchestrations,
            Orchestrations.status.in_([s.value for s in SETTLED]),
            Orchestrations.updated_at < before,
            limit=limit,
        )
        # Every tenant's settled records, so the system scope, spelled here.
        # Planned with its values, so the index serves an idle pass too.
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(Orchestrations, Orchestrations.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
