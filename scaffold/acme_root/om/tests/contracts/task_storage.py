"""The tasks storage contract. The cases named in `CROSS_TENANT_CASES` are the
tenant fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes. The negative control that
says what they catch is in `docs/runbooks/tenant-isolation.md`."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed, TenantMismatch
from acme.om.orchestrations.rules import advanced
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    Step,
)
from acme.om.outbox.types.row import OutboxRow
from acme.om.tasks.rules import RANK_SCALE_BOUND, needs_respace
from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus


async def drained(storage: TasksStorageInterface) -> datetime:
    """A cut no other case's task is deleted before, with whatever an earlier
    run of these cases left behind it purged first: the read and the purge
    reach across tenants, so a case owns the rows behind its cut."""
    cut = utcnow() - timedelta(days=36500)
    while left := await storage.read_deleted(cut, 1000):
        await storage.purge_deleted(cut, [task_id for _, task_id in left])
    return cut


CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "update_archived_in_step",
        "count_done_tasks",
        "count_open_and_read_places",
        "count_open_tasks",
        "create_task",
        "create_tasks_in_step",
        "mark_reminded",
        "purge_tenant",
        "read_archivable",
        "read_archived_tasks",
        "read_done_tasks",
        "read_last_place",
        "read_long_place",
        "read_open_places",
        "read_open_places_before",
        "read_open_tasks",
        "read_recent_open_tasks",
        "read_task",
        "read_tasks",
        "update_task",
        "update_tasks",
        "update_tasks_if_current",
    }
)
"""Every method of `TasksStorageInterface` that takes a tenant has a case in
this module that presents another tenant's. `test_storage_exceptions.py` holds
the two sets to each other, so a new method arrives with its case."""


def team() -> TaskFilter:
    return TaskFilter(scope=TaskScope.TEAM, user_id=new_id())


def mine(user_id: UUID) -> TaskFilter:
    return TaskFilter(scope=TaskScope.MINE, user_id=user_id)


def after(task: Task) -> TaskCursor:
    return TaskCursor(updated_at=task.updated_at, id=task.id)


def past(task: Task) -> OpenTaskCursor:
    return OpenTaskCursor(rank=task.rank, id=task.id)


def make_row(org_id: UUID, task: Task, action: str = "created") -> OutboxRow:
    """The outbox row a write lands with; the memory outbox the root wires
    receives it, the Postgres one inserts it in the same commit."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind=f"tasks.task.{action}",
        target_id=task.id,
        payload={"title": task.title},
        actor_id=task.created_by,
        request_id=new_id(),
        app="api",
    )


async def seed(storage: TasksStorageInterface, org_id: UUID, task: Task) -> None:
    """Lands a task the way the manager does: through the create primitive.
    An update never inserts, so there is no other way in."""
    assert await storage.create_task(org_id, task, (make_row(org_id, task),)) is True


async def bump(storage: TasksStorageInterface, org_id: UUID, task: Task, **changes: object) -> Task:
    """The manager's copy on update, at the storage: the next version, written
    against the one the task carries."""
    changed = task.model_copy(update={**changes, "version": task.version + 1})
    await storage.update_task(
        org_id, changed, task.version, (make_row(org_id, changed, "updated"),)
    )
    return changed


def make_task(
    title: str = "Write the contract",
    *,
    created_by: UUID | None = None,
    assignee_id: UUID | None = None,
    status: TaskStatus = TaskStatus.OPEN,
    rank: float | str = 0,
    updated_ago: timedelta = timedelta(0),
) -> Task:
    """A task at `rank`, written as the manager writes one: the exact
    decimal."""
    exact = Decimal(str(rank))
    now = utcnow()
    return Task(
        id=new_id(),
        created_at=now,
        updated_at=now - updated_ago,
        created_by=created_by or new_id(),
        updated_by=created_by or new_id(),
        title=title,
        status=status,
        assignee_id=assignee_id,
        rank=exact,
    )


def make_record(kind: OrchestrationKind = OrchestrationKind.TASK_IMPORT) -> Orchestration:
    now = utcnow()
    actor = new_id()
    return Orchestration(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        kind=kind,
        input={"file_id": str(new_id())},
    )


def step_of(record: Orchestration, cursor: int, *, finished: bool = False) -> Step:
    """The step a worker writes after `record`: the next cursor, conditioned
    on the version it read."""
    after = advanced(
        record, utcnow(), record.created_by, cursor=cursor, total=None, finished=finished
    )
    return Step(record=after, expected_version=record.version)


def record_row(org_id: UUID, record: Orchestration) -> OutboxRow:
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="orchestrations.orchestration.updated",
        target_id=record.id,
        actor_id=record.created_by,
        request_id=new_id(),
        app="worker",
    )


class TaskStorageContract:
    @pytest.fixture
    def storage(self) -> TasksStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    @pytest.fixture
    def orchestrations(self) -> OrchestrationsStorageInterface:
        """The records a step lands beside the tasks, over the same database
        (or the same memory) the storage writes."""
        raise NotImplementedError("the concrete test class provides the records")

    async def _open(
        self, orchestrations: OrchestrationsStorageInterface, org_id: UUID
    ) -> Orchestration:
        record = make_record()
        assert await orchestrations.create_orchestration(org_id, record, ())
        return record

    async def test_a_step_creates_its_tasks_and_moves_the_record_in_one_commit(
        self, storage: TasksStorageInterface, orchestrations: OrchestrationsStorageInterface
    ) -> None:
        org = new_id()
        record = await self._open(orchestrations, org)
        first, second = make_task("one", rank=1.0), make_task("two", rank=2.0)
        step = step_of(record, 2)
        written = await storage.create_tasks_in_step(
            org,
            [(first, (make_row(org, first),)), (second, (make_row(org, second),))],
            step,
            (record_row(org, step.record),),
        )
        assert written == (True, True)
        stored = await orchestrations.read_orchestration(org, record.id)
        assert stored is not None and stored.cursor == 2 and stored.applied == 2
        assert stored.version == record.version + 1
        # The same rows stepped again (a worker that died after the commit,
        # its item handed to another): the tasks are there, nothing is made
        # twice, and nothing is counted twice.
        again = step_of(stored, 2, finished=True)
        assert await storage.create_tasks_in_step(
            org, [(first, (make_row(org, first),))], again, ()
        ) == (False,)
        final = await orchestrations.read_orchestration(org, record.id)
        assert final is not None and final.applied == 2
        assert final.status is OrchestrationStatus.SUCCEEDED
        assert await storage.count_open_tasks(org, team()) == 2

    async def test_a_step_on_a_record_that_moved_lands_nothing(
        self, storage: TasksStorageInterface, orchestrations: OrchestrationsStorageInterface
    ) -> None:
        org = new_id()
        record = await self._open(orchestrations, org)
        task = make_task()
        stale = Step(record=step_of(record, 1).record, expected_version=record.version + 5)
        with pytest.raises(PreconditionFailed):
            await storage.create_tasks_in_step(org, [(task, (make_row(org, task),))], stale, ())
        assert await storage.read_task(org, task.id) is None
        assert await orchestrations.read_orchestration(org, record.id) == record

    async def test_create_tasks_in_step_under_another_tenant_lands_nothing(
        self, storage: TasksStorageInterface, orchestrations: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        record = await self._open(orchestrations, org)
        task = make_task()
        with pytest.raises(PreconditionFailed):
            await storage.create_tasks_in_step(
                other, [(task, (make_row(other, task),))], step_of(record, 1), ()
            )
        assert await storage.read_task(other, task.id) is None
        assert await storage.read_task(org, task.id) is None
        assert await orchestrations.read_orchestration(org, record.id) == record

    async def test_the_last_place_is_the_bottom_of_the_tenants_open_list(
        self, storage: TasksStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        assert await storage.read_last_place(org) is None
        low, high = make_task(rank=1.0), make_task(rank=5.0)
        done = make_task(rank=9.0, status=TaskStatus.DONE)
        for task in (low, high, done):
            await seed(storage, org, task)
        await seed(storage, other, make_task(rank=50.0))
        assert await storage.read_last_place(org) == (5.0, high.id)
        assert await storage.read_last_place(new_id()) is None

    async def test_the_cleanup_archives_old_done_tasks_and_leaves_the_rest(
        self, storage: TasksStorageInterface, orchestrations: OrchestrationsStorageInterface
    ) -> None:
        org = new_id()
        record = await self._open(orchestrations, org)
        old = make_task("old", status=TaskStatus.DONE, updated_ago=timedelta(days=100))
        reopened = make_task("reopened", status=TaskStatus.DONE, updated_ago=timedelta(days=95))
        recent = make_task("recent", status=TaskStatus.DONE, updated_ago=timedelta(days=89))
        still_open = make_task("open", updated_ago=timedelta(days=200))
        for task in (old, reopened, recent, still_open):
            await seed(storage, org, task)
        before = utcnow() - timedelta(days=90)
        candidates = await storage.read_archivable(org, before, 10)
        assert candidates == [old.id, reopened.id]
        # A person reopens one between the read and the write: the
        # conditional write leaves it alone.
        await bump(storage, org, reopened, status=TaskStatus.OPEN, updated_at=utcnow())
        now = utcnow()
        step = step_of(record, len(candidates), finished=True)
        archived = await storage.update_archived_in_step(
            org,
            [(task_id, ()) for task_id in candidates],
            before,
            now,
            record.created_by,
            step,
            (record_row(org, step.record),),
        )
        assert archived == (True, False)
        stored = await storage.read_task(org, old.id)
        assert stored is not None and stored.archived_at == now
        assert stored.version == old.version + 1
        assert [t.title for t in await storage.read_done_tasks(org, team(), None, 10)] == ["recent"]
        assert [t.id for t in await storage.read_archived_tasks(org, team(), None, 10)] == [old.id]
        after = await orchestrations.read_orchestration(org, record.id)
        assert after is not None and after.applied == 1
        # A second run archives nothing more: every candidate is archived,
        # reopened, or too young.
        assert await storage.read_archivable(org, before, 10) == []

    async def test_update_archived_in_step_under_another_tenant_lands_nothing(
        self, storage: TasksStorageInterface, orchestrations: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        record = await self._open(orchestrations, org)
        old = make_task(status=TaskStatus.DONE, updated_ago=timedelta(days=100))
        await seed(storage, org, old)
        before = utcnow() - timedelta(days=90)
        with pytest.raises(PreconditionFailed):
            await storage.update_archived_in_step(
                other, [(old.id, ())], before, utcnow(), new_id(), step_of(record, 1), ()
            )
        stored = await storage.read_task(org, old.id)
        assert stored is not None and stored.archived_at is None

    async def test_read_archivable_and_the_archived_list_are_tenant_scoped(
        self, storage: TasksStorageInterface, orchestrations: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        record = await self._open(orchestrations, org)
        old = make_task(status=TaskStatus.DONE, updated_ago=timedelta(days=100))
        await seed(storage, org, old)
        before = utcnow() - timedelta(days=90)
        assert await storage.read_archivable(other, before, 10) == []
        await storage.update_archived_in_step(
            org, [(old.id, ())], before, utcnow(), new_id(), step_of(record, 1), ()
        )
        assert await storage.read_archived_tasks(other, team(), None, 10) == []
        assert len(await storage.read_archived_tasks(org, team(), None, 10)) == 1

    async def test_round_trip_and_update_by_copy(self, storage: TasksStorageInterface) -> None:
        org = new_id()
        task = make_task(assignee_id=new_id(), rank=-2.5)
        await seed(storage, org, task)
        assert await storage.read_task(org, task.id) == task
        done = await bump(storage, org, task, status=TaskStatus.DONE, updated_at=utcnow())
        assert await storage.read_task(org, task.id) == done
        assert await storage.read_open_tasks(org, team(), None, limit=10) == []
        assert await storage.read_done_tasks(org, team(), None, limit=10) == [done]

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: TasksStorageInterface
    ) -> None:
        # The create primitive: a retry presents the id it minted the first time;
        # the insert reports it, and neither the row nor the outbox is touched.
        org_id = new_id()
        task = make_task("Once")
        assert await storage.create_task(org_id, task, (make_row(org_id, task),)) is True
        again = task.model_copy(update={"title": "Twice"})
        assert await storage.create_task(org_id, again, (make_row(org_id, again),)) is False
        stored = await storage.read_task(org_id, task.id)
        assert stored is not None and stored.title == "Once"

    async def test_reads_are_tenant_scoped(self, storage: TasksStorageInterface) -> None:
        org_a, org_b = new_id(), new_id()
        task = make_task()
        await seed(storage, org_a, task)
        assert await storage.read_task(org_b, task.id) is None
        assert await storage.read_open_tasks(org_b, team(), None, limit=10) == []
        assert await storage.read_recent_open_tasks(org_b, team(), limit=10) == []
        assert await storage.read_open_places(org_b, exclude=None, after=None, limit=10) == []
        past_everything = (Decimal(10**6), new_id())
        assert await storage.read_open_places_before(org_b, past_everything, limit=10) == []
        long = make_task("long", rank="0." + "0" * RANK_SCALE_BOUND + "1")
        await seed(storage, org_a, long)
        assert await storage.read_long_place(org_b) is None

    async def test_the_open_count_is_the_tenants_live_open_tasks(
        self, storage: TasksStorageInterface
    ) -> None:
        """The active tasks a plan bounds: open and not deleted, in this
        tenant; a done one, a deleted one, and another tenant's are not."""
        org_a, org_b = new_id(), new_id()
        open_task, done, gone = make_task("Open"), make_task("Done"), make_task("Gone")
        for task in (open_task, done, gone):
            await seed(storage, org_a, task)
        await bump(storage, org_a, done, status=TaskStatus.DONE)
        await bump(storage, org_a, gone, deleted_at=utcnow(), deleted_by=new_id())
        assert await storage.count_open_tasks(org_a, team()) == 1
        assert await storage.count_open_tasks(org_b, team()) == 0

    async def test_the_count_and_the_top_places_are_read_together(
        self, storage: TasksStorageInterface
    ) -> None:
        """What the two reads answer on their own, and nothing of another
        tenant's."""
        org_a, org_b = new_id(), new_id()
        first, second, done = make_task("First"), make_task("Second"), make_task("Done")
        for task in (first, second, done):
            await seed(storage, org_a, task)
        await bump(storage, org_a, done, status=TaskStatus.DONE)
        places = await storage.read_open_places(org_a, exclude=first.id, after=None, limit=1)
        assert await storage.count_open_and_read_places(org_a, team(), first.id, 1) == (2, places)
        assert await storage.count_open_and_read_places(org_b, team(), None, 10) == (0, [])

    async def test_write_refuses_another_tenant(self, storage: TasksStorageInterface) -> None:
        org_a, org_b = new_id(), new_id()
        task = make_task()
        await seed(storage, org_a, task)
        with pytest.raises(TenantMismatch):
            await bump(storage, org_b, task, title="Stolen")
        assert await storage.read_task(org_a, task.id) == task

    async def test_the_done_list_is_tenant_scoped_on_its_own(
        self, storage: TasksStorageInterface
    ) -> None:
        """`read_done_tasks` writes its own `where`, so the open list's case
        says nothing about it: a predicate dropped here is invisible there.
        The filter travels with the tenant, so neither scope reaches across."""
        org_a, org_b = new_id(), new_id()
        author = new_id()
        theirs = [
            make_task(
                f"theirs{i}",
                created_by=author,
                status=TaskStatus.DONE,
                updated_ago=timedelta(minutes=i),
            )
            for i in range(3)
        ]
        for task in theirs:
            await seed(storage, org_a, task)
        assert await storage.read_done_tasks(org_b, team(), None, limit=10) == []
        assert await storage.read_done_tasks(org_b, mine(author), None, limit=10) == []
        assert len(await storage.read_done_tasks(org_a, team(), None, limit=10)) == 3

    async def test_the_open_count_is_the_open_list_of_one_tenant(
        self, storage: TasksStorageInterface
    ) -> None:
        """The open count counts what the open list shows: not a done task, not
        a deleted one, not another tenant's, and under `mine` not a task that
        is someone else's."""
        org_a, org_b = new_id(), new_id()
        author = new_id()
        for i in range(3):
            await seed(storage, org_a, make_task(f"open{i}", created_by=author))
        await seed(storage, org_a, make_task("theirs"))
        await seed(storage, org_a, make_task("done", status=TaskStatus.DONE))
        gone = make_task("deleted")
        await seed(storage, org_a, gone)
        await storage.update_task(
            org_a, gone.model_copy(update={"deleted_at": utcnow(), "version": 2}), 1, ()
        )
        assert await storage.count_open_tasks(org_a, team()) == 4
        assert await storage.count_open_tasks(org_a, mine(author)) == 3
        assert await storage.count_open_tasks(org_b, team()) == 0
        assert await storage.count_open_tasks(org_b, mine(author)) == 0

    async def test_the_created_count_spans_every_tenant_and_every_state(
        self, storage: TasksStorageInterface
    ) -> None:
        """The traffic figure the platform's size reads: tasks created at or
        after the cut, in whichever tenant and whatever became of them since;
        a task created before the cut is not traffic of the window."""
        cut = utcnow()
        assert await storage.count_created_since(cut) == 0
        org_a, org_b = new_id(), new_id()
        old = make_task("before the cut").model_copy(update={"created_at": cut - timedelta(days=2)})
        await seed(storage, org_a, old)
        done = make_task("done since", status=TaskStatus.DONE)
        await seed(storage, org_a, done)
        gone = make_task("deleted since")
        await seed(storage, org_b, gone)
        await storage.update_task(
            org_b, gone.model_copy(update={"deleted_at": utcnow(), "version": 2}), 1, ()
        )
        assert await storage.count_created_since(cut) == 2
        assert await storage.count_created_since(cut - timedelta(days=3)) == 3

    async def test_a_cursor_of_another_tenant_pages_nothing(
        self, storage: TasksStorageInterface
    ) -> None:
        """A cursor is a position in one tenant's order. Presented to another
        tenant it pages that tenant's own rows and never reaches across, on
        the open list and on the done list alike."""
        org_a, org_b = new_id(), new_id()
        theirs_open = make_task("theirs open", rank=1.0)
        theirs_done = make_task("theirs done", status=TaskStatus.DONE)
        await seed(storage, org_a, theirs_open)
        await seed(storage, org_a, theirs_done)
        mine_open = make_task("mine open", rank=2.0)
        mine_done = make_task("mine done", status=TaskStatus.DONE, updated_ago=timedelta(hours=1))
        await seed(storage, org_b, mine_open)
        await seed(storage, org_b, mine_done)
        paged_open = await storage.read_open_tasks(org_b, team(), past(theirs_open), limit=10)
        assert [t.title for t in paged_open] == ["mine open"]
        paged_done = await storage.read_done_tasks(org_b, team(), after(theirs_done), limit=10)
        assert [t.title for t in paged_done] == ["mine done"]
        # And the empty tenant stays empty however the cursor is placed.
        assert await storage.read_open_tasks(new_id(), team(), past(theirs_open), limit=10) == []
        assert await storage.read_done_tasks(new_id(), team(), after(theirs_done), limit=10) == []

    async def test_create_reports_another_tenants_id_and_lands_nothing(
        self, storage: TasksStorageInterface
    ) -> None:
        """The create primitive answers an id it cannot have the way it
        answers one of its own: it reports the id and writes nothing, so a
        caller presenting another tenant's id neither takes the row nor
        learns what stands on the other side."""
        org_a, org_b = new_id(), new_id()
        task = make_task("theirs")
        await seed(storage, org_a, task)
        stolen = task.model_copy(update={"title": "stolen"})
        assert await storage.create_task(org_b, stolen, (make_row(org_b, stolen),)) is False
        assert await storage.read_task(org_b, task.id) is None
        assert await storage.read_task(org_a, task.id) == task
        assert await storage.read_open_tasks(org_b, team(), None, limit=10) == []

    async def test_a_bulk_update_refuses_a_row_of_another_tenant_and_lands_none(
        self, storage: TasksStorageInterface
    ) -> None:
        """The one bulk write. Its all-or-nothing case tests versions; this one
        tests the tenant, and puts the foreign row second so the row before it
        would have landed had the statement not been fenced."""
        org_a, org_b = new_id(), new_id()
        mine = make_task("mine", rank=0.5)
        theirs = make_task("theirs", rank=0.75)
        await seed(storage, org_b, mine)
        await seed(storage, org_a, theirs)

        def placed(task: Task, rank: int) -> Task:
            return task.model_copy(update={"rank": Decimal(rank), "version": task.version + 1})

        with pytest.raises(TenantMismatch):
            await storage.update_tasks(
                org_b,
                [
                    (placed(mine, 0), mine.version, (make_row(org_b, mine, "updated"),)),
                    (placed(theirs, 1), theirs.version, (make_row(org_b, theirs, "updated"),)),
                ],
            )
        assert await storage.read_task(org_b, mine.id) == mine
        assert await storage.read_task(org_a, theirs.id) == theirs
        assert await storage.read_open_places(org_b, exclude=None, after=None, limit=10) == [
            (0.5, mine.id)
        ]

    async def test_the_done_count_is_the_done_list_of_one_tenant(
        self, storage: TasksStorageInterface
    ) -> None:
        """The done count counts what the done list shows: not an open task, not
        an archived one, not a deleted one, not another tenant's, and under
        `mine` not a task that is someone else's."""
        org_a, org_b = new_id(), new_id()
        author = new_id()
        for i in range(2):
            await seed(
                storage, org_a, make_task(f"done{i}", created_by=author, status=TaskStatus.DONE)
            )
        await seed(storage, org_a, make_task("theirs", status=TaskStatus.DONE))
        await seed(storage, org_a, make_task("open", created_by=author))
        shelved = make_task("archived", created_by=author, status=TaskStatus.DONE)
        await seed(storage, org_a, shelved)
        await bump(storage, org_a, shelved, archived_at=utcnow())
        gone = make_task("deleted", created_by=author, status=TaskStatus.DONE)
        await seed(storage, org_a, gone)
        await bump(storage, org_a, gone, deleted_at=utcnow(), deleted_by=author)
        assert await storage.count_done_tasks(org_a, team()) == 3
        assert await storage.count_done_tasks(org_a, mine(author)) == 2
        assert await storage.count_done_tasks(org_b, team()) == 0
        assert await storage.count_done_tasks(org_b, mine(author)) == 0

    async def test_read_tasks_answers_the_tenants_tasks_by_id(
        self, storage: TasksStorageInterface
    ) -> None:
        """A deleted task is read, so the bulk change can say why it skipped
        it; an id of another tenant, or of nothing, is absent."""
        org_a, org_b = new_id(), new_id()
        kept, theirs = make_task("kept"), make_task("theirs")
        gone = make_task("gone")
        await seed(storage, org_a, kept)
        await seed(storage, org_a, gone)
        await seed(storage, org_b, theirs)
        deleted = await bump(storage, org_a, gone, deleted_at=utcnow(), deleted_by=new_id())
        found = await storage.read_tasks(org_a, [kept.id, gone.id, theirs.id, new_id()])
        assert found == {kept.id: kept, gone.id: deleted}
        assert await storage.read_tasks(org_b, [kept.id, gone.id]) == {}
        assert await storage.read_tasks(org_a, []) == {}

    async def test_updates_if_current_land_each_on_its_own_version(
        self, storage: TasksStorageInterface
    ) -> None:
        """A batch of a bulk change: every task against its own version, in one
        commit, and a stale one is left alone without refusing the others,
        where `update_tasks` would land none."""
        org = new_id()
        first, second, third = make_task("first"), make_task("second"), make_task("third")
        for task in (first, second, third):
            await seed(storage, org, task)
        moved = await bump(storage, org, second, title="moved")  # now at version 2

        def done(task: Task) -> Task:
            return task.model_copy(update={"status": TaskStatus.DONE, "version": task.version + 1})

        landed = await storage.update_tasks_if_current(
            org,
            [
                (done(task), task.version, (make_row(org, task, "updated"),))
                for task in (first, second, third)
            ],
        )
        assert landed == (True, False, True)
        assert await storage.read_task(org, first.id) == done(first)
        assert await storage.read_task(org, second.id) == moved
        assert await storage.read_task(org, third.id) == done(third)
        assert await storage.update_tasks_if_current(org, []) == ()

    async def test_updates_if_current_write_every_field_each_task_carries(
        self, storage: TasksStorageInterface
    ) -> None:
        """A reopen writes a different rank to each task, clears what it
        clears, and keeps what it keeps: each task reads back as written,
        its exact decimal rank and its nulls included, the batch's order
        of ranks with it."""
        org = new_id()
        due = date(2030, 9, 30)
        tasks = [
            make_task(f"task {index}", status=TaskStatus.DONE, rank=index) for index in range(3)
        ]
        tasks[0] = tasks[0].model_copy(update={"archived_at": utcnow(), "due_on": due})
        tasks[1] = tasks[1].model_copy(update={"assignee_id": new_id(), "notes": "kept"})
        for task in tasks:
            await seed(storage, org, task)
        ranks = [Decimal("-1.000000000000000000000000000001"), Decimal("-2.5"), Decimal("-3")]
        reopened = [
            task.model_copy(
                update={
                    "status": TaskStatus.OPEN,
                    "rank": rank,
                    "archived_at": None,
                    "updated_at": utcnow(),
                    "version": task.version + 1,
                }
            )
            for task, rank in zip(tasks, ranks, strict=True)
        ]
        landed = await storage.update_tasks_if_current(
            org,
            [
                (changed, task.version, (make_row(org, changed, "updated"),))
                for changed, task in zip(reopened, tasks, strict=True)
            ],
        )
        assert landed == (True, True, True)
        stored = await storage.read_tasks(org, [task.id for task in tasks])
        assert [stored[task.id] for task in tasks] == reopened
        assert stored[tasks[0].id].due_on == due and stored[tasks[0].id].archived_at is None
        assert stored[tasks[0].id].rank == ranks[0]

    async def test_updates_if_current_under_another_tenant_land_nothing_of_theirs(
        self, storage: TasksStorageInterface
    ) -> None:
        """The tenant fence of the bulk write: another tenant's task, at the
        version named, is left alone, and the tenant's own task beside it
        still lands."""
        org_a, org_b = new_id(), new_id()
        mine_task, theirs = make_task("mine"), make_task("theirs")
        await seed(storage, org_b, mine_task)
        await seed(storage, org_a, theirs)

        def done(task: Task) -> Task:
            return task.model_copy(update={"status": TaskStatus.DONE, "version": task.version + 1})

        landed = await storage.update_tasks_if_current(
            org_b,
            [
                (done(theirs), theirs.version, (make_row(org_b, theirs, "updated"),)),
                (done(mine_task), mine_task.version, (make_row(org_b, mine_task, "updated"),)),
            ],
        )
        assert landed == (False, True)
        assert await storage.read_task(org_a, theirs.id) == theirs
        assert await storage.read_task(org_b, mine_task.id) == done(mine_task)

    async def test_update_is_a_compare_and_set_on_the_version(
        self, storage: TasksStorageInterface
    ) -> None:
        # Two writers read the task at version 1. The first lands at version 2;
        # the second still names version 1, so the row has moved under it and
        # the write is refused with nothing changed, not merged over the first.
        org = new_id()
        task = make_task("as read")
        await seed(storage, org, task)
        first = await bump(storage, org, task, title="first writer")
        with pytest.raises(PreconditionFailed):
            await bump(storage, org, task, title="second writer")
        assert await storage.read_task(org, task.id) == first
        assert first.version == 2
        # The winner's snapshot is current, so the next write from it lands.
        second = await bump(storage, org, first, title="first writer again")
        assert (await storage.read_task(org, task.id)) == second and second.version == 3

    async def test_update_never_inserts_a_missing_row(self, storage: TasksStorageInterface) -> None:
        # A row that is gone (purged between the read and the write) has moved
        # too: the update reports it and leaves nothing behind.
        org = new_id()
        task = make_task("never stored")
        with pytest.raises(PreconditionFailed):
            await bump(storage, org, task, title="conjured")
        assert await storage.read_task(org, task.id) is None

    async def test_open_list_sorts_by_rank_hides_deleted_and_clamps(
        self, storage: TasksStorageInterface
    ) -> None:
        org = new_id()
        tasks = [make_task(f"t{i}", rank=p) for i, p in enumerate([3, -1, 2])]
        for task in tasks:
            await seed(storage, org, task)
        listed = await storage.read_open_tasks(org, team(), None, limit=10)
        assert [t.title for t in listed] == ["t1", "t2", "t0"]
        assert len(await storage.read_open_tasks(org, team(), None, limit=2)) == 2
        assert await storage.read_open_places(org, exclude=None, after=None, limit=10) == [
            (-1.0, tasks[1].id),
            (2.0, tasks[2].id),
            (3.0, tasks[0].id),
        ]
        assert await storage.read_open_places(org, exclude=tasks[1].id, after=None, limit=10) == [
            (2.0, tasks[2].id),
            (3.0, tasks[0].id),
        ]
        gone = await bump(storage, org, tasks[1], deleted_at=utcnow(), deleted_by=new_id())
        assert [t.title for t in await storage.read_open_tasks(org, team(), None, limit=10)] == [
            "t2",
            "t0",
        ]
        assert await storage.read_task(org, gone.id) == gone

    async def test_the_recent_open_list_is_newest_first_and_hides_the_rest(
        self, storage: TasksStorageInterface
    ) -> None:
        org = new_id()
        tasks = [make_task(f"t{i}", rank=-i) for i in range(4)]
        for task in tasks:
            await seed(storage, org, task)
        await bump(storage, org, tasks[3], status=TaskStatus.DONE, updated_at=utcnow())
        await bump(storage, org, tasks[2], deleted_at=utcnow(), deleted_by=new_id())
        recent = await storage.read_recent_open_tasks(org, team(), limit=10)
        assert [t.title for t in recent] == ["t1", "t0"]
        assert [t.title for t in await storage.read_recent_open_tasks(org, team(), limit=1)] == [
            "t1"
        ]

    async def test_open_places_are_bounded_and_follow_the_pair(
        self, storage: TasksStorageInterface
    ) -> None:
        # The statement compares the pair, as the open list orders it; this
        # case holds both impls to it. Two tasks share a rank, so the id
        # decides which follows the anchor, and which precedes it.
        org = new_id()
        tasks = [make_task(f"p{i}", rank=p) for i, p in enumerate([1, 2, 2, 3, 4])]
        for task in tasks:
            await seed(storage, org, task)
        every = sorted((t.rank, t.id) for t in tasks)
        assert await storage.read_open_places(org, None, None, limit=10) == every
        assert await storage.read_open_places(org, None, None, limit=1) == every[:1]
        assert await storage.read_open_places(org, None, None, limit=3) == every[:3]
        for index, anchor in enumerate(every):
            assert await storage.read_open_places(org, None, anchor, limit=10) == every[index + 1 :]
            assert (
                await storage.read_open_places(org, None, anchor, limit=1)
                == every[index + 1 : index + 2]
            )
            nearest_first = list(reversed(every[:index]))
            assert await storage.read_open_places_before(org, anchor, limit=10) == nearest_first
            assert await storage.read_open_places_before(org, anchor, limit=1) == nearest_first[:1]
        # Excluding the place that follows the anchor reads the one after it.
        first, second, third = every[0], every[1], every[2]
        assert await storage.read_open_places(org, second[1], first, limit=1) == [third]
        # An anchor that is no task's place still cuts by the pair.
        assert await storage.read_open_places(org, None, (Decimal("2.5"), new_id()), limit=1) == [
            every[3]
        ]
        assert await storage.read_open_places_before(org, (Decimal("2.5"), new_id()), 1) == [
            every[2]
        ]

    async def test_places_read_ranks_exactly(self, storage: TasksStorageInterface) -> None:
        # Two ranks a float cannot tell apart: the list, the places, and the
        # cursor keep every digit, so the order is the ranks' and no tie.
        org = new_id()
        low = make_task("low", rank="0.1000000000000000000000001")
        high = make_task("high", rank="0.1000000000000000000000002")
        assert float(low.rank) == float(high.rank), "a float ties them"
        await seed(storage, org, high)
        await seed(storage, org, low)
        assert await storage.read_open_places(org, None, None, limit=10) == [
            (low.rank, low.id),
            (high.rank, high.id),
        ]
        page = await storage.read_open_tasks(org, team(), None, limit=1)
        assert [t.title for t in page] == ["low"]
        assert page[0].rank == low.rank
        rest = await storage.read_open_tasks(org, team(), past(page[0]), limit=10)
        assert [t.title for t in rest] == ["high"]

    async def test_the_long_place_is_the_top_most_rank_past_the_bound(
        self, storage: TasksStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        long_digits = "0." + "0" * RANK_SCALE_BOUND + "5"
        at_bound = make_task("at the bound", rank="0." + "0" * (RANK_SCALE_BOUND - 1) + "5")
        lower = make_task("long, lower", rank="1" + long_digits[1:])
        upper = make_task("long, upper", rank=long_digits)
        done = make_task("long, done", rank="-" + long_digits, status=TaskStatus.DONE)
        assert not needs_respace(at_bound.rank) and needs_respace(upper.rank)
        assert await storage.read_long_place(org) is None
        for task in (at_bound, lower, upper, done):
            await seed(storage, org, task)
        await seed(storage, other, make_task("theirs", rank="-" + long_digits))
        assert await storage.read_long_place(org) == (upper.rank, upper.id)
        await bump(storage, org, upper, deleted_at=utcnow(), deleted_by=new_id())
        assert await storage.read_long_place(org) == (lower.rank, lower.id)
        assert await storage.read_long_place(new_id()) is None, "another tenant's is not read"

    async def test_the_tenants_with_a_chore_due_are_read_across_tenants_once_each(
        self, storage: TasksStorageInterface
    ) -> None:
        """A tenant with an archivable task, one with an open task whose rank
        grew long, and one with both (two archivable, one long) are each read
        once, in id order. A tenant whose done task is younger than the cut,
        archived, or deleted, and whose long rank is on a done task or a
        deleted one, has no chore due. The cut stands a century back and the
        read starts after an id minted before every tenant of the case, so
        the case owns every tenant it reads in a shared database."""
        cut = utcnow() - timedelta(days=36500)
        older = timedelta(days=36501)
        long_rank = "0." + "0" * RANK_SCALE_BOUND + "1"
        start = new_id()
        archivable, long, both, none = new_id(), new_id(), new_id(), new_id()
        await seed(storage, archivable, make_task(status=TaskStatus.DONE, updated_ago=older))
        await seed(storage, long, make_task(rank=long_rank))
        for task in (
            make_task(status=TaskStatus.DONE, updated_ago=older),
            make_task(status=TaskStatus.DONE, updated_ago=older),
            make_task(rank=long_rank),
        ):
            await seed(storage, both, task)
        gone = {"deleted_at": utcnow(), "deleted_by": new_id()}
        for task in (
            make_task(status=TaskStatus.DONE, updated_ago=timedelta(days=36499)),
            make_task(status=TaskStatus.DONE, updated_ago=older).model_copy(
                update={"archived_at": utcnow()}
            ),
            make_task(status=TaskStatus.DONE, updated_ago=older).model_copy(update=gone),
            make_task(status=TaskStatus.DONE, rank=long_rank),
            make_task(rank=long_rank).model_copy(update=gone),
        ):
            await seed(storage, none, task)
        assert await storage.read_tenants_with_chores(cut, start, 10) == [archivable, long, both]

    async def test_the_tenants_with_a_chore_due_page_after_a_tenant(
        self, storage: TasksStorageInterface
    ) -> None:
        """At most `limit` tenants a read, and the next read starts after the
        last one it gave, as the sweep's next pass does."""
        cut = utcnow() - timedelta(days=36500)
        start = new_id()
        tenants = [new_id() for _ in range(3)]
        for org_id in tenants:
            await seed(
                storage,
                org_id,
                make_task(status=TaskStatus.DONE, updated_ago=timedelta(days=36501)),
            )
        first = await storage.read_tenants_with_chores(cut, start, 2)
        assert first == tenants[:2]
        assert await storage.read_tenants_with_chores(cut, first[-1], 2) == tenants[2:]
        assert await storage.read_tenants_with_chores(cut, tenants[-1], 2) == []

    async def test_open_list_pages_by_rank_cursor(self, storage: TasksStorageInterface) -> None:
        # Two tasks share a rank (two writers that placed at once): the id
        # breaks the tie, in the list and in the cursor alike.
        org = new_id()
        tasks = [make_task(f"o{i}", rank=p) for i, p in enumerate([1, 2, 2, 3, 4])]
        for task in tasks:
            await seed(storage, org, task)
        first = await storage.read_open_tasks(org, team(), None, limit=2)
        second = await storage.read_open_tasks(org, team(), past(first[-1]), limit=2)
        third = await storage.read_open_tasks(org, team(), past(second[-1]), limit=2)
        assert [t.id for t in [*first, *second, *third]] == [
            t.id for t in sorted(tasks, key=lambda t: (t.rank, t.id))
        ]
        assert await storage.read_open_tasks(org, team(), past(third[-1]), limit=2) == []

    async def test_done_list_is_newest_first_and_pages_by_cursor(
        self, storage: TasksStorageInterface
    ) -> None:
        org = new_id()
        tasks = [
            make_task(f"d{i}", status=TaskStatus.DONE, updated_ago=timedelta(minutes=i))
            for i in range(5)
        ]
        for task in reversed(tasks):
            await seed(storage, org, task)
        first = await storage.read_done_tasks(org, team(), None, limit=2)
        assert [t.title for t in first] == ["d0", "d1"]
        second = await storage.read_done_tasks(org, team(), after(first[-1]), limit=2)
        assert [t.title for t in second] == ["d2", "d3"]
        third = await storage.read_done_tasks(org, team(), after(second[-1]), limit=2)
        assert [t.title for t in third] == ["d4"]

    async def test_mine_is_assigned_to_me_or_unassigned_and_created_by_me(
        self, storage: TasksStorageInterface
    ) -> None:
        org, me, other = new_id(), new_id(), new_id()
        mine_created = make_task("created by me", created_by=me, rank=1)
        mine_assigned = make_task("assigned to me", created_by=other, assignee_id=me, rank=2)
        given_away = make_task("created by me, assigned away", created_by=me, assignee_id=other)
        theirs = make_task("theirs", created_by=other, rank=3)
        mine_done = make_task("mine, done", created_by=me, status=TaskStatus.DONE)
        for task in (mine_created, mine_assigned, given_away, theirs, mine_done):
            await seed(storage, org, task)
        assert [t.title for t in await storage.read_open_tasks(org, mine(me), None, limit=10)] == [
            "created by me",
            "assigned to me",
        ]
        assert len(await storage.read_open_tasks(org, team(), None, limit=10)) == 4
        assert [t.title for t in await storage.read_done_tasks(org, mine(me), None, limit=10)] == [
            "mine, done"
        ]
        assert await storage.read_done_tasks(org, mine(other), None, limit=10) == []

    async def test_purge_removes_only_tasks_deleted_before_the_cut(
        self, storage: TasksStorageInterface
    ) -> None:
        """The read and the purge reach across tenants: every tenant's tasks
        deleted before the cut, the longest deleted first, and none after."""
        org, elsewhere = new_id(), new_id()
        cut = await drained(storage)
        old, recent, live = make_task("old"), make_task("recent"), make_task("live")
        await seed(
            storage,
            org,
            old.model_copy(update={"deleted_at": cut - timedelta(days=1), "deleted_by": org}),
        )
        await seed(storage, org, recent.model_copy(update={"deleted_at": cut, "deleted_by": org}))
        await seed(storage, org, live)
        other = make_task("other")
        await seed(
            storage, elsewhere, other.model_copy(update={"deleted_at": cut - timedelta(days=2)})
        )
        assert await storage.read_deleted(cut, 10) == [(elsewhere, other.id), (org, old.id)]
        assert await storage.purge_deleted(cut, [old.id, recent.id, live.id]) == 1
        assert await storage.read_task(org, old.id) is None
        assert await storage.read_task(org, recent.id) is not None
        assert await storage.read_task(org, live.id) == live
        assert await storage.read_task(elsewhere, other.id) is not None, "only the ids named"
        assert await storage.purge_deleted(cut, [old.id]) == 0, "idempotent"
        assert await storage.read_deleted(cut, 10) == [(elsewhere, other.id)]
        assert await storage.purge_deleted(cut, [other.id]) == 1, "whatever its tenant"
        assert await storage.read_deleted(cut, 10) == []

    async def test_a_backlog_past_a_batch_goes_a_batch_at_a_time(
        self, storage: TasksStorageInterface
    ) -> None:
        org = new_id()
        cut = await drained(storage)
        for i in range(5):
            gone = make_task(f"gone {i}").model_copy(
                update={"deleted_at": cut - timedelta(days=1, minutes=5 - i), "deleted_by": org}
            )
            await seed(storage, org, gone)
        first = [task_id for _, task_id in await storage.read_deleted(cut, 2)]
        assert len(first) == 2, "the oldest deletes first, a batch at most"
        assert await storage.purge_deleted(cut, first) == 2
        second = [task_id for _, task_id in await storage.read_deleted(cut, 2)]
        assert not set(first) & set(second)
        assert await storage.purge_deleted(cut, second) == 2
        last = [task_id for _, task_id in await storage.read_deleted(cut, 2)]
        assert await storage.purge_deleted(cut, last) == 1, "a short batch: drained"
        assert await storage.read_deleted(cut, 2) == []
        live = make_task("live")
        await seed(storage, org, live)
        assert await storage.purge_tenant(org, 2) == 1
        assert await storage.purge_tenant(org, 2) == 0

    async def test_purge_tenant_takes_every_task_of_the_tenant(
        self, storage: TasksStorageInterface
    ) -> None:
        org, elsewhere = new_id(), new_id()
        live = make_task("open")
        done = make_task("done", status=TaskStatus.DONE)
        gone = make_task("deleted")
        await seed(storage, org, live)
        await seed(storage, org, done)
        await seed(
            storage, org, gone.model_copy(update={"deleted_at": utcnow(), "deleted_by": org})
        )
        other = make_task("other")
        await seed(storage, elsewhere, other)
        assert await storage.purge_tenant(org, 2) == 2, "a batch at most"
        assert await storage.purge_tenant(org, 2) == 1, "whatever its state"
        assert await storage.read_task(org, live.id) is None
        assert await storage.read_task(org, done.id) is None
        assert await storage.read_task(org, gone.id) is None
        assert await storage.read_task(elsewhere, other.id) is not None, "per tenant"
        assert await storage.purge_tenant(org, 2) == 0, "idempotent"

    async def test_many_updates_land_together_or_not_at_all(
        self, storage: TasksStorageInterface
    ) -> None:
        # The respace of a run: every row against its own version, in one
        # commit. One stale version refuses the whole write, so a run is never
        # respaced halfway; from current versions every row lands.
        org = new_id()
        first, second = make_task("first", rank=0.5), make_task("second", rank=0.75)
        await seed(storage, org, first)
        await seed(storage, org, second)
        moved_second = await bump(storage, org, second, title="moved")  # now at version 2

        def placed(task: Task, rank: int) -> Task:
            return task.model_copy(update={"rank": Decimal(rank), "version": task.version + 1})

        with pytest.raises(PreconditionFailed):
            await storage.update_tasks(
                org,
                [
                    (placed(first, 0), first.version, (make_row(org, first, "updated"),)),
                    (placed(second, 1), second.version, (make_row(org, second, "updated"),)),
                ],
            )
        assert await storage.read_task(org, first.id) == first
        assert await storage.read_task(org, second.id) == moved_second
        renumbered = [placed(first, 0), placed(moved_second, 1)]
        await storage.update_tasks(
            org,
            [
                (renumbered[0], first.version, (make_row(org, first, "updated"),)),
                (renumbered[1], moved_second.version, (make_row(org, second, "updated"),)),
            ],
        )
        assert await storage.read_task(org, first.id) == renumbered[0]
        assert await storage.read_task(org, second.id) == renumbered[1]
        assert await storage.read_open_places(org, exclude=None, after=None, limit=10) == [
            (0.0, first.id),
            (1.0, second.id),
        ]

    async def test_a_due_date_is_set_moved_and_cleared(
        self, storage: TasksStorageInterface
    ) -> None:
        org = new_id()
        task = make_task().model_copy(update={"due_on": date(2030, 1, 31)})
        await storage.create_task(org, task, (make_row(org, task),))
        moved = await bump(storage, org, task, due_on=date(2030, 2, 1))
        assert (await storage.read_task(org, task.id)) == moved
        cleared = await bump(storage, org, moved, due_on=None)
        stored = await storage.read_task(org, task.id)
        assert stored == cleared and stored is not None and stored.due_on is None

    async def test_a_reminder_is_marked_once_and_only_while_it_is_due(
        self, storage: TasksStorageInterface
    ) -> None:
        """The reminder's compare-and-set: it lands while the task is open,
        living, due on the date the reminder read, and not yet reminded, and
        never for another tenant; a second run of it lands nothing."""
        org, other = new_id(), new_id()
        due = date(2030, 9, 30)
        task = make_task().model_copy(update={"due_on": due})
        await storage.create_task(org, task, (make_row(org, task),))
        assert (await storage.read_task(org, task.id)) == task, "the date round-trips"
        assert await storage.mark_reminded(other, task.id, due, utcnow(), ()) is None
        moved = due + timedelta(days=1)
        assert await storage.mark_reminded(org, task.id, moved, utcnow(), ()) is None
        now = utcnow()
        marked = await storage.mark_reminded(
            org, task.id, due, now, (make_row(org, task, "reminded"),)
        )
        assert marked is not None
        assert marked.reminded_at == now and marked.version == task.version + 1
        assert marked.due_on == due
        assert await storage.read_task(org, task.id) == marked
        assert await storage.mark_reminded(org, task.id, due, utcnow(), ()) is None
        done = make_task(status=TaskStatus.DONE).model_copy(update={"due_on": due})
        await storage.create_task(org, done, (make_row(org, done),))
        assert await storage.mark_reminded(org, done.id, due, utcnow(), ()) is None
        undated = make_task()
        await storage.create_task(org, undated, (make_row(org, undated),))
        assert await storage.mark_reminded(org, undated.id, due, utcnow(), ()) is None
