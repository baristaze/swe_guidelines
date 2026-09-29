from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Integer,
    Numeric,
    Table,
    Uuid,
    and_,
    bindparam,
    func,
    inspect,
    literal,
    literal_column,
    or_,
    select,
    true,
    tuple_,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.sql.dml import ReturningUpdate

from acme.om.base import EMPTY_UUID
from acme.om.exceptions import PreconditionFailed, TenantMismatch
from acme.om.orchestrations.storage.impl.postgres import land_step
from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PLAN_WITH_VALUES, PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values
from acme.om.tasks.rules import RANK_SCALE_BOUND, Place
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.storage.tables.tasks import Tasks
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus

WRITTEN: tuple[str, ...] = tuple(name for name in inspect(Tasks).columns.keys() if name != "org_id")
"""The columns a bulk update writes from the task: the mapped ones, so a
column the mapping leaves out is never named; `org_id` never moves."""


def _update_if_current_statement() -> ReturningUpdate[Any]:
    """A batch of a bulk change, in one statement. The tasks arrive as one
    array per column, beside the version each was read at, so the statement
    is the same for one task and for a hundred, and it is compiled and
    prepared once. Each row is written only when it is the tenant's and
    still at its version: the fence is per row, in the join, and a row that
    moved is left out without refusing the rest. Returns the ids written.

    Built on the table and not the mapped class, so it runs as one Core
    statement with its arrays as plain parameters. No parameter takes a
    column's name (`new_<column>`, `tenant`): those are the SET clause's."""
    table = cast(Table, Tasks.__table__)
    incoming = (
        func.unnest(
            *(bindparam(f"new_{name}", type_=ARRAY(table.c[name].type)) for name in WRITTEN),
            bindparam("expected_version", type_=ARRAY(Integer())),
        )
        .table_valued(*WRITTEN, "expected_version")
        .render_derived(name="incoming")
    )
    return (
        update(table)
        .where(
            table.c.id == incoming.c.id,
            table.c.org_id == bindparam("tenant", type_=Uuid()),
            table.c.version == incoming.c.expected_version,
        )
        .values({name: incoming.c[name] for name in WRITTEN if name != "id"})
        .returning(table.c.id)
    )


UPDATE_IF_CURRENT = _update_if_current_statement()


def _live(org_id: UUID, status: TaskStatus) -> ColumnElement[bool]:
    return and_(Tasks.org_id == org_id, Tasks.status == status.value, Tasks.deleted_at.is_(None))


def _archivable(org_id: UUID, before: datetime) -> ColumnElement[bool]:
    """Mirrors tasks.rules.is_archivable in SQL."""
    return and_(
        _live(org_id, TaskStatus.DONE), Tasks.archived_at.is_(None), Tasks.updated_at < before
    )


def _visible(criterion: TaskFilter) -> ColumnElement[bool]:
    """Mirrors tasks.rules.is_visible in SQL."""
    if criterion.scope is TaskScope.TEAM:
        return true()
    return or_(
        Tasks.assignee_id == criterion.user_id,
        and_(Tasks.assignee_id.is_(None), Tasks.created_by == criterion.user_id),
    )


def _before(cursor: TaskCursor) -> ColumnElement[bool]:
    """Mirrors tasks.rules.is_before in SQL."""
    return tuple_(Tasks.updated_at, Tasks.id) < tuple_(
        literal(cursor.updated_at, DateTime(timezone=True)), literal(cursor.id, Uuid())
    )


def _place(place: Place) -> ColumnElement[Any]:
    return tuple_(literal(place[0], Numeric()), literal(place[1], Uuid()))


def _after(cursor: OpenTaskCursor) -> ColumnElement[bool]:
    """Mirrors tasks.rules.is_after in SQL."""
    return tuple_(Tasks.rank, Tasks.id) > _place((cursor.rank, cursor.id))


_LONG_RANK = func.scale(Tasks.rank) > literal_column(str(RANK_SCALE_BOUND))
"""Mirrors tasks.rules.needs_respace in SQL, spelled as the partial index's
predicate spells it: a literal, never a bound value, which a generic plan
could not match to the index. A rank is written without trailing zeros
(tasks.rules.spread), so its scale is its length after the point."""

_DONE_SHELF = and_(
    Tasks.status == literal_column(f"'{TaskStatus.DONE.value}'"),
    Tasks.archived_at.is_(None),
    Tasks.deleted_at.is_(None),
)
"""The done tasks not archived, of every tenant, spelled as the predicate of
the partial index the read across tenants walks: the status a literal, never
a bound value, so a generic plan proves the predicate and reads the index."""


class TasksStoragePostgresImpl(PgStorageBase, TasksStorageInterface):
    async def read_open_tasks(
        self, org_id: UUID, criterion: TaskFilter, after: OpenTaskCursor | None, limit: int
    ) -> list[Task]:
        stmt = select(Tasks).where(_live(org_id, TaskStatus.OPEN), _visible(criterion))
        if after is not None:
            stmt = stmt.where(_after(after))
        stmt = stmt.order_by(Tasks.rank, Tasks.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Task) for row in result.scalars()]

    async def read_recent_open_tasks(
        self, org_id: UUID, criterion: TaskFilter, limit: int
    ) -> list[Task]:
        stmt = (
            select(Tasks)
            .where(_live(org_id, TaskStatus.OPEN), _visible(criterion))
            .order_by(Tasks.id.desc())
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Task) for row in result.scalars()]

    async def count_open_tasks(self, org_id: UUID, criterion: TaskFilter) -> int:
        stmt = (
            select(func.count())
            .select_from(Tasks)
            .where(_live(org_id, TaskStatus.OPEN), _visible(criterion))
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return (await session.execute(stmt)).scalar_one()

    async def count_done_tasks(self, org_id: UUID, criterion: TaskFilter) -> int:
        stmt = (
            select(func.count())
            .select_from(Tasks)
            .where(_live(org_id, TaskStatus.DONE), Tasks.archived_at.is_(None), _visible(criterion))
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return (await session.execute(stmt)).scalar_one()

    async def read_tasks(self, org_id: UUID, task_ids: Sequence[UUID]) -> dict[UUID, Task]:
        if not task_ids:
            return {}
        stmt = select(Tasks).where(Tasks.org_id == org_id, Tasks.id.in_(list(task_ids)))
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return {row.id: to_model(row, Task) for row in result.scalars()}

    async def read_done_tasks(
        self, org_id: UUID, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> list[Task]:
        return await self._done(org_id, criterion, before, limit, archived=False)

    async def read_archived_tasks(
        self, org_id: UUID, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> list[Task]:
        return await self._done(org_id, criterion, before, limit, archived=True)

    async def _done(
        self,
        org_id: UUID,
        criterion: TaskFilter,
        before: TaskCursor | None,
        limit: int,
        *,
        archived: bool,
    ) -> list[Task]:
        shelf = Tasks.archived_at.is_not(None) if archived else Tasks.archived_at.is_(None)
        stmt = select(Tasks).where(_live(org_id, TaskStatus.DONE), shelf, _visible(criterion))
        if before is not None:
            stmt = stmt.where(_before(before))
        stmt = stmt.order_by(Tasks.updated_at.desc(), Tasks.id.desc()).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            result = await session.execute(stmt)
            return [to_model(row, Task) for row in result.scalars()]

    async def read_last_place(self, org_id: UUID) -> Place | None:
        stmt = (
            select(Tasks.rank, Tasks.id)
            .where(_live(org_id, TaskStatus.OPEN))
            .order_by(Tasks.rank.desc(), Tasks.id.desc())
            .limit(1)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            found = (await session.execute(stmt)).one_or_none()
            return None if found is None else (found.rank, found.id)

    async def read_archivable(self, org_id: UUID, before: datetime, limit: int) -> list[UUID]:
        stmt = (
            select(Tasks.id)
            .where(_archivable(org_id, before))
            .order_by(Tasks.updated_at, Tasks.id)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return list((await session.execute(stmt)).scalars())

    async def create_tasks_in_step(
        self,
        org_id: UUID,
        tasks: Sequence[tuple[Task, tuple[OutboxRow, ...]]],
        step: Step,
        step_rows: tuple[OutboxRow, ...],
    ) -> tuple[bool, ...]:
        # One transaction: the tasks whose id is not written yet, the rows
        # that announce them, the record's compare-and-set, and its rows. A
        # record another holder moved rolls all of it back.
        async with self._session_for(Tasks, org_id=org_id) as session:
            written: set[UUID] = set()
            if tasks:
                stmt = (
                    insert(Tasks)
                    .values([{"org_id": org_id, **to_values(task, Tasks)} for task, _ in tasks])
                    .on_conflict_do_nothing(index_elements=["id"])
                    .returning(Tasks.id)
                )
                written = set((await session.execute(stmt)).scalars())
            for task, outbox_rows in tasks:
                if task.id in written:
                    for outbox_row in outbox_rows:
                        session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await land_step(session, org_id, step, len(written))
            for outbox_row in step_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
            return tuple(task.id in written for task, _ in tasks)

    async def update_archived_in_step(
        self,
        org_id: UUID,
        candidates: Sequence[tuple[UUID, tuple[OutboxRow, ...]]],
        before: datetime,
        archived_at: datetime,
        actor: UUID,
        step: Step,
        step_rows: tuple[OutboxRow, ...],
    ) -> tuple[bool, ...]:
        # The condition is the statement's: a candidate reopened, edited, or
        # deleted since it was read no longer matches it and is left alone.
        async with self._session_for(Tasks, org_id=org_id) as session:
            archived: set[UUID] = set()
            if candidates:
                stmt = (
                    update(Tasks)
                    .where(
                        Tasks.id.in_([task_id for task_id, _ in candidates]),
                        _archivable(org_id, before),
                    )
                    .values(
                        archived_at=archived_at,
                        updated_at=archived_at,
                        updated_by=actor,
                        version=Tasks.version + 1,
                    )
                    .returning(Tasks.id)
                )
                archived = set((await session.execute(stmt)).scalars())
            for task_id, outbox_rows in candidates:
                if task_id in archived:
                    for outbox_row in outbox_rows:
                        session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await land_step(session, org_id, step, len(archived))
            for outbox_row in step_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
            return tuple(task_id in archived for task_id, _ in candidates)

    async def read_open_places(
        self, org_id: UUID, exclude: UUID | None, after: Place | None, limit: int
    ) -> list[Place]:
        # Ordered by (rank, id), the order the open list reads: ranks tie, and
        # the id decides between two that do (tasks.rules.Place).
        stmt = select(Tasks.rank, Tasks.id).where(_live(org_id, TaskStatus.OPEN))
        if exclude is not None:
            stmt = stmt.where(Tasks.id != exclude)
        if after is not None:
            stmt = stmt.where(tuple_(Tasks.rank, Tasks.id) > _place(after))
        stmt = stmt.order_by(Tasks.rank, Tasks.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [(rank, task_id) for rank, task_id in (await session.execute(stmt))]

    async def count_open_and_read_places(
        self, org_id: UUID, criterion: TaskFilter, exclude: UUID | None, limit: int
    ) -> tuple[int, list[Place]]:
        count = (
            select(func.count())
            .select_from(Tasks)
            .where(_live(org_id, TaskStatus.OPEN), _visible(criterion))
        )
        places = select(Tasks.rank, Tasks.id).where(_live(org_id, TaskStatus.OPEN))
        if exclude is not None:
            places = places.where(Tasks.id != exclude)
        places = places.order_by(Tasks.rank, Tasks.id).limit(limit)
        async with self._session_for(Tasks, org_id=org_id) as session:
            counted = (await session.execute(count)).scalar_one()
            top = [(rank, task_id) for rank, task_id in (await session.execute(places))]
            return counted, top

    async def read_open_places_before(self, org_id: UUID, before: Place, limit: int) -> list[Place]:
        stmt = (
            select(Tasks.rank, Tasks.id)
            .where(_live(org_id, TaskStatus.OPEN), tuple_(Tasks.rank, Tasks.id) < _place(before))
            .order_by(Tasks.rank.desc(), Tasks.id.desc())
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [(rank, task_id) for rank, task_id in (await session.execute(stmt))]

    async def read_long_place(self, org_id: UUID) -> Place | None:
        # The partial index holds the long ranks of every status; the status
        # is bound, so it is a filter over those few rows, never the index's.
        stmt = (
            select(Tasks.rank, Tasks.id)
            .where(Tasks.org_id == org_id, _LONG_RANK, Tasks.deleted_at.is_(None))
            .where(Tasks.status == TaskStatus.OPEN.value)
            .order_by(Tasks.rank, Tasks.id)
            .limit(1)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            found = (await session.execute(stmt)).one_or_none()
            return None if found is None else (found.rank, found.id)

    async def read_task(self, org_id: UUID, task_id: UUID) -> Task | None:
        stmt = select(Tasks).where(Tasks.org_id == org_id, Tasks.id == task_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Task)

    async def count_created_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(Tasks).where(Tasks.created_at >= since)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return (await session.execute(stmt)).scalar_one()

    async def read_deleted(self, before: datetime, limit: int) -> list[tuple[UUID, UUID]]:
        stmt = (
            select(Tasks.org_id, Tasks.id)
            .where(Tasks.deleted_at < before)
            .order_by(Tasks.deleted_at)
            .limit(limit)
        )
        # Every tenant's deleted tasks, so the system scope, spelled here.
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return [(org_id, task_id) for org_id, task_id in await session.execute(stmt)]

    async def read_tenants_with_chores(
        self, archivable_before: datetime, after: UUID | None, limit: int
    ) -> list[UUID]:
        # Two arms, each on a partial index that holds only the rows it may
        # find: the done shelf by its last change, so the cut is a range and
        # only the archivable tasks are read (mirrors tasks.rules.is_archivable),
        # and the long ranks. A tenant with neither is in no index this reads.
        # Planned with its values, as the purges across tenants are: a generic
        # plan knows neither the cut nor the tenant a page starts after, and
        # would walk the tenant-led index of every open and done task.
        archivable = select(Tasks.org_id).where(_DONE_SHELF, Tasks.updated_at < archivable_before)
        long = select(Tasks.org_id).where(
            _LONG_RANK, Tasks.deleted_at.is_(None), Tasks.status == TaskStatus.OPEN.value
        )
        if after is not None:
            archivable = archivable.where(Tasks.org_id > after)
            long = long.where(Tasks.org_id > after)
        due = union(archivable, long).subquery("due")
        stmt = select(due.c.org_id).order_by(due.c.org_id).limit(limit)
        # Every tenant's tasks, so the system scope, spelled here.
        async with self._session_for(Tasks, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            return list((await session.execute(stmt)).scalars())

    async def purge_deleted(self, before: datetime, task_ids: list[UUID]) -> int:
        if not task_ids:
            return 0
        stmt = delete_batch(
            Tasks, Tasks.id.in_(task_ids), Tasks.deleted_at < before, limit=len(task_ids)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(Tasks, Tasks.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged

    async def create_task(
        self, org_id: UUID, task: Task, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(Tasks, org_id, task, outbox_rows)

    async def update_task(
        self, org_id: UUID, task: Task, expected_version: int, outbox_rows: tuple[OutboxRow, ...]
    ) -> None:
        await self.update_tasks(org_id, [(task, expected_version, outbox_rows)])

    async def update_tasks(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> None:
        # The compare-and-set is the statement itself: the version is in the
        # WHERE, so two writers from one snapshot cannot both land. The outbox
        # rows join the commit only when every update hit a row; one that
        # missed rolls the transaction back with nothing landed.
        async with self._session_for(Tasks, org_id=org_id) as session:
            for task, expected_version, _ in updates:
                values = {k: v for k, v in to_values(task, Tasks).items() if k != "id"}
                stmt = (
                    update(Tasks)
                    .where(
                        Tasks.id == task.id,
                        Tasks.org_id == org_id,
                        Tasks.version == expected_version,
                    )
                    .values(**values)
                    .returning(Tasks.id)
                )
                if (await session.execute(stmt)).scalar_one_or_none() is None:
                    await session.rollback()
                    raise await self._why_not(org_id, task.id, expected_version)
            for _, _, outbox_rows in updates:
                for outbox_row in outbox_rows:
                    session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()

    async def update_tasks_if_current(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> tuple[bool, ...]:
        # The same compare-and-set as `update_tasks`, every task in one
        # statement; a task the statement did not write is one that moved,
        # left alone while the others land. Only the rows of the tasks that
        # landed join the commit, and they go in one batch at the flush.
        if not updates:
            return ()
        written = [to_values(task, Tasks) for task, _, _ in updates]
        params: dict[str, Any] = {
            f"new_{name}": [values[name] for values in written] for name in WRITTEN
        }
        params["expected_version"] = [expected for _, expected, _ in updates]
        params["tenant"] = org_id
        async with self._session_for(Tasks, org_id=org_id) as session:
            hit = set((await session.execute(UPDATE_IF_CURRENT, params)).scalars())
            session.add_all(
                to_row(outbox_row, OutboxRows, org_id=org_id)
                for task, _, outbox_rows in updates
                if task.id in hit
                for outbox_row in outbox_rows
            )
            await session.commit()
            return tuple(task.id in hit for task, _, _ in updates)

    async def _why_not(
        self, org_id: UUID, task_id: UUID, expected_version: int
    ) -> TenantMismatch | PreconditionFailed:
        """Which of the three conditions the compare-and-set missed: the row is
        gone, it is another tenant's, or it has moved. The row is read after
        the failed statement, so the version it names is a report, never a
        value to retry with.

        The question is cross-tenant by nature. It asks where a row this
        tenant could not write is, and a transaction narrowed to this tenant
        cannot tell "another tenant holds it" from "nobody does": the policy
        answers both with no row. So the read takes the system scope, spelled
        here, in a session of its own, since the rollback above already ended
        the transaction the settings lived in. It reads two columns of one id
        and reports which condition missed, which is what it reported before
        the policy existed."""
        stmt = select(Tasks.org_id, Tasks.version).where(Tasks.id == task_id)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            found = (await session.execute(stmt)).one_or_none()
        if found is None:
            return PreconditionFailed(f"task {task_id} is gone")
        if found.org_id != org_id:
            return TenantMismatch(f"tasks {task_id} is not in {org_id}")
        return PreconditionFailed(
            f"task {task_id} is at version {found.version}, not {expected_version}"
        )

    async def mark_reminded(
        self,
        org_id: UUID,
        task_id: UUID,
        due_on: date,
        reminded_at: datetime,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Task | None:
        stmt = (
            update(Tasks)
            .where(
                Tasks.id == task_id,
                Tasks.org_id == org_id,
                Tasks.status == TaskStatus.OPEN.value,
                Tasks.deleted_at.is_(None),
                Tasks.due_on == due_on,
                Tasks.reminded_at.is_(None),
            )
            .values(reminded_at=reminded_at, version=Tasks.version + 1)
            .returning(Tasks)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                await session.rollback()
                return None
            written = to_model(row, Task)
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await session.commit()
            return written
