from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from acme.om.exceptions import PreconditionFailed, TenantMismatch
from acme.om.orchestrations.storage import StepLandingInterface
from acme.om.orchestrations.types.orchestration import Step
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable
from acme.om.tasks.rules import (
    Place,
    is_after,
    is_archivable,
    is_before,
    is_visible,
    needs_respace,
)
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.task import Task, TaskStatus


class TasksStorageMemoryImpl(MemoryStorageBase, TasksStorageInterface):
    def __init__(
        self,
        outbox: OutboxLandingInterface | None = None,
        steps: StepLandingInterface | None = None,
    ) -> None:
        """`steps` is where a step of a long-running record lands beside the
        tasks it changes, the twin of the statement Postgres runs in the same
        transaction."""
        super().__init__(outbox)
        self._steps = steps
        self._tasks: MemoryTable[Task] = {}

    def _live(self, org_id: UUID, status: TaskStatus) -> list[Task]:
        return [
            t
            for t in self._rows(self._tasks, org_id)
            if t.deleted_at is None and t.status == status
        ]

    async def read_open_tasks(
        self, org_id: UUID, criterion: TaskFilter, after: OpenTaskCursor | None, limit: int
    ) -> list[Task]:
        tasks = [
            t
            for t in self._live(org_id, TaskStatus.OPEN)
            if is_visible(t, criterion) and (after is None or is_after(t, after))
        ]
        return sorted(tasks, key=lambda t: (t.rank, t.id))[:limit]

    async def read_recent_open_tasks(
        self, org_id: UUID, criterion: TaskFilter, limit: int
    ) -> list[Task]:
        tasks = [t for t in self._live(org_id, TaskStatus.OPEN) if is_visible(t, criterion)]
        return sorted(tasks, key=lambda t: t.id, reverse=True)[:limit]

    async def count_open_tasks(self, org_id: UUID, criterion: TaskFilter) -> int:
        return sum(1 for t in self._live(org_id, TaskStatus.OPEN) if is_visible(t, criterion))

    async def count_done_tasks(self, org_id: UUID, criterion: TaskFilter) -> int:
        return sum(
            1
            for t in self._live(org_id, TaskStatus.DONE)
            if t.archived_at is None and is_visible(t, criterion)
        )

    async def read_tasks(self, org_id: UUID, task_ids: Sequence[UUID]) -> dict[UUID, Task]:
        found = {task_id: self._get(self._tasks, org_id, task_id) for task_id in task_ids}
        return {task_id: task for task_id, task in found.items() if task is not None}

    async def read_done_tasks(
        self, org_id: UUID, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> list[Task]:
        return self._done(org_id, criterion, before, limit, archived=False)

    async def read_archived_tasks(
        self, org_id: UUID, criterion: TaskFilter, before: TaskCursor | None, limit: int
    ) -> list[Task]:
        return self._done(org_id, criterion, before, limit, archived=True)

    def _done(
        self,
        org_id: UUID,
        criterion: TaskFilter,
        before: TaskCursor | None,
        limit: int,
        *,
        archived: bool,
    ) -> list[Task]:
        tasks = [
            t
            for t in self._live(org_id, TaskStatus.DONE)
            if (t.archived_at is not None) == archived
            and is_visible(t, criterion)
            and (before is None or is_before(t, before))
        ]
        return sorted(tasks, key=lambda t: (t.updated_at, t.id), reverse=True)[:limit]

    async def read_last_place(self, org_id: UUID) -> Place | None:
        places = [(t.rank, t.id) for t in self._live(org_id, TaskStatus.OPEN)]
        return max(places) if places else None

    async def read_archivable(self, org_id: UUID, before: datetime, limit: int) -> list[UUID]:
        found = [t for t in self._rows(self._tasks, org_id) if is_archivable(t, before)]
        return [t.id for t in sorted(found, key=lambda t: (t.updated_at, t.id))][:limit]

    async def create_tasks_in_step(
        self,
        org_id: UUID,
        tasks: Sequence[tuple[Task, tuple[OutboxRow, ...]]],
        step: Step,
        step_rows: tuple[OutboxRow, ...],
    ) -> tuple[bool, ...]:
        # The record's check first, then every write, one step under the lock,
        # as the statements share one transaction in Postgres.
        async with self._lock:
            self._landing().check_step(org_id, step)
            written = tuple(
                self._insert(self._tasks, org_id, task, outbox_rows) for task, outbox_rows in tasks
            )
            self._land(org_id, step_rows)
            self._landing().land_step(org_id, step, sum(written))
            return written

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
        async with self._lock:
            self._landing().check_step(org_id, step)
            archived: list[bool] = []
            for task_id, outbox_rows in candidates:
                task = self._get(self._tasks, org_id, task_id)
                if task is None or not is_archivable(task, before):
                    archived.append(False)
                    continue
                written = task.model_copy(
                    update={
                        "archived_at": archived_at,
                        "updated_at": archived_at,
                        "updated_by": actor,
                        "version": task.version + 1,
                    }
                )
                self._put(self._tasks, org_id, written, outbox_rows)
                archived.append(True)
            self._land(org_id, step_rows)
            self._landing().land_step(org_id, step, sum(archived))
            return tuple(archived)

    def _landing(self) -> StepLandingInterface:
        if self._steps is None:
            raise RuntimeError("this memory storage was built without the records to land steps in")
        return self._steps

    async def read_open_places(
        self, org_id: UUID, exclude: UUID | None, after: Place | None, limit: int
    ) -> list[Place]:
        places = [(t.rank, t.id) for t in self._live(org_id, TaskStatus.OPEN) if t.id != exclude]
        return sorted(p for p in places if after is None or p > after)[:limit]

    async def count_open_and_read_places(
        self, org_id: UUID, criterion: TaskFilter, exclude: UUID | None, limit: int
    ) -> tuple[int, list[Place]]:
        return (
            await self.count_open_tasks(org_id, criterion),
            await self.read_open_places(org_id, exclude, None, limit),
        )

    async def read_open_places_before(self, org_id: UUID, before: Place, limit: int) -> list[Place]:
        places = [(t.rank, t.id) for t in self._live(org_id, TaskStatus.OPEN)]
        return sorted((p for p in places if p < before), reverse=True)[:limit]

    async def read_long_place(self, org_id: UUID) -> Place | None:
        places = [
            (t.rank, t.id) for t in self._live(org_id, TaskStatus.OPEN) if needs_respace(t.rank)
        ]
        return min(places) if places else None

    async def read_task(self, org_id: UUID, task_id: UUID) -> Task | None:
        return self._get(self._tasks, org_id, task_id)

    async def count_created_since(self, since: datetime) -> int:
        return sum(1 for task in self._every(self._tasks) if task.created_at >= since)

    async def read_deleted(self, before: datetime, limit: int) -> list[tuple[UUID, UUID]]:
        gone = [
            (org_id, t)
            for org_id, t in self._rows_across_tenants(self._tasks)
            if t.deleted_at is not None and t.deleted_at < before
        ]
        ordered = sorted(gone, key=lambda pair: (pair[1].deleted_at, pair[1].id))
        return [(org_id, t.id) for org_id, t in ordered][:limit]

    async def read_tenants_with_chores(
        self, archivable_before: datetime, after: UUID | None, limit: int
    ) -> list[UUID]:
        due = {
            org_id
            for org_id, t in self._rows_across_tenants(self._tasks)
            if is_archivable(t, archivable_before)
            or (t.deleted_at is None and t.status is TaskStatus.OPEN and needs_respace(t.rank))
        }
        return sorted(org_id for org_id in due if after is None or org_id > after)[:limit]

    async def purge_deleted(self, before: datetime, task_ids: list[UUID]) -> int:
        chosen = set(task_ids)
        gone = [
            t.id
            for _, t in self._rows_across_tenants(self._tasks)
            if t.id in chosen and t.deleted_at is not None and t.deleted_at < before
        ]
        for task_id in gone:
            del self._tasks[task_id]
        return len(gone)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        gone = [t.id for t in self._rows(self._tasks, org_id)][:limit]
        for task_id in gone:
            del self._tasks[task_id]
        return len(gone)

    async def create_task(
        self, org_id: UUID, task: Task, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._tasks, org_id, task, outbox_rows)

    async def update_task(
        self, org_id: UUID, task: Task, expected_version: int, outbox_rows: tuple[OutboxRow, ...]
    ) -> None:
        await self.update_tasks(org_id, [(task, expected_version, outbox_rows)])

    async def update_tasks(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> None:
        # Every check, then every write, one step under the lock, as the
        # conditional statements share one transaction in Postgres.
        async with self._lock:
            for task, expected_version, _ in updates:
                found = self._tasks.get(task.id)
                if found is None:
                    raise PreconditionFailed(f"task {task.id} is gone")
                if found[0] != org_id:
                    raise TenantMismatch(f"{task.id} is not in {org_id}")
                if found[1].version != expected_version:
                    raise PreconditionFailed(
                        f"task {task.id} is at version {found[1].version}, not {expected_version}"
                    )
            for task, _, outbox_rows in updates:
                self._put(self._tasks, org_id, task, outbox_rows)

    async def update_tasks_if_current(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> tuple[bool, ...]:
        # Each check and its write together, one batch under the lock, as the
        # one conditional statement writes the batch in Postgres.
        async with self._lock:
            landed: list[bool] = []
            for task, expected_version, outbox_rows in updates:
                found = self._tasks.get(task.id)
                hit = (
                    found is not None
                    and found[0] == org_id
                    and found[1].version == expected_version
                )
                landed.append(hit)
                if hit:
                    self._put(self._tasks, org_id, task, outbox_rows)
            return tuple(landed)

    async def mark_reminded(
        self,
        org_id: UUID,
        task_id: UUID,
        due_on: date,
        reminded_at: datetime,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> Task | None:
        async with self._lock:
            task = self._get(self._tasks, org_id, task_id)
            if (
                task is None
                or task.status != TaskStatus.OPEN
                or task.deleted_at is not None
                or task.due_on != due_on
                or task.reminded_at is not None
            ):
                return None
            written = task.model_copy(
                update={"reminded_at": reminded_at, "version": task.version + 1}
            )
            self._put(self._tasks, org_id, written, outbox_rows)
            return written
