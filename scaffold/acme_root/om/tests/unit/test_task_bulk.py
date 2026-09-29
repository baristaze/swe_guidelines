"""The change of many tasks at once: by the ids named and by a whole list, a
batch a commit, each task fenced on its own version, the plan's bound on a
reopen, and the answer that names what changed and what was left alone."""

from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from contracts.doubles import Members, context, media_of, no_slack, orchestrations_of
from contracts.factories import make_org
from contracts.plans import ON_TEAM, FixedPlan

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics
from acme.om.base import new_id, utcnow
from acme.om.billing.manager import EntitlementsInterface
from acme.om.billing.types.plan import Plan
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import NotAuthorized, ValidationFailed
from acme.om.opcontext import OpContext, Role
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.tasks.impl import manager as manager_module
from acme.om.tasks.impl.manager import TasksManagerImpl, TasksOptions
from acme.om.tasks.rules import BULK_BATCH, BULK_MAX_IDS
from acme.om.tasks.storage.impl.memory import TasksStorageMemoryImpl
from acme.om.tasks.types.bulk import BulkAction, PlanBound, SkippedTask, SkipReason
from acme.om.tasks.types.filter import TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus


class CountingStorage(TasksStorageMemoryImpl):
    """Counts the commits of the bulk write, and runs a hook before the first
    one: another writer landing between the change's read and its write."""

    commits = 0
    before_first_commit: Callable[[], Coroutine[Any, Any, None]] | None = None

    async def update_tasks_if_current(
        self, org_id: UUID, updates: Sequence[tuple[Task, int, tuple[OutboxRow, ...]]]
    ) -> tuple[bool, ...]:
        hook, self.before_first_commit = self.before_first_commit, None
        if hook is not None:
            await hook()
        self.commits += 1
        return await super().update_tasks_if_current(org_id, updates)


class Stack:
    def __init__(self, tmp_path: Path, plan: EntitlementsInterface = ON_TEAM) -> None:
        infra = InfraLocalImpl(tmp_path)
        self.outbox = OutboxStorageMemoryImpl()
        self.storage = CountingStorage(self.outbox)
        self.members = Members()  # pyright: ignore[reportAbstractUsage] (a partial double)
        relay = OutboxRelayImpl(self.outbox, EventStorageMemoryImpl(), infra.get_topics())
        self.manager = TasksManagerImpl(
            self.storage,
            self.members,
            media_of(self.outbox, self.members, relay, infra),
            relay,
            no_slack(),
            TasksOptions(),
            entitlements=plan,
            orchestrations=orchestrations_of(
                OrchestrationsStorageMemoryImpl(), self.members, relay
            ),
        )
        self.pushes: list[EntityChangedPayload] = []

        async def record(payload: TopicPayload) -> None:
            if isinstance(payload, EntityChangedPayload):
                self.pushes.append(payload)

        infra.get_topics().subscribe(Topics.ENTITY_CHANGED, "test", record)

    async def add(self, ctx: OpContext, title: str, assignee_id: UUID | None = None) -> Task:
        now = utcnow()
        task = Task(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            title=title,
            assignee_id=assignee_id,
        )
        return await self.manager.create_task(ctx, task)

    async def finish(self, ctx: OpContext, task: Task) -> Task:
        current = await self.manager.get_task(ctx, task.id)
        return await self.manager.update_task(
            ctx, current.model_copy(update={"status": TaskStatus.DONE}), current.version
        )

    async def status_of(self, ctx: OpContext, task: Task) -> TaskStatus:
        return (await self.manager.get_task(ctx, task.id)).status

    async def open_titles(self, ctx: OpContext) -> list[str]:
        page = await self.manager.get_open_tasks(ctx, team(ctx), None, 200)
        return [t.title for t in page.items]


def team(ctx: OpContext) -> TaskFilter:
    return TaskFilter(scope=TaskScope.TEAM, user_id=ctx.user_id)


def mine(ctx: OpContext) -> TaskFilter:
    return TaskFilter(scope=TaskScope.MINE, user_id=ctx.user_id)


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


async def test_complete_by_ids_changes_the_open_ones_and_names_the_rest(stack: Stack) -> None:
    org = make_org()
    ann = context(Role.MEMBER, org)
    first, second, finished = [await stack.add(ann, t) for t in ("first", "second", "finished")]
    await stack.finish(ann, finished)
    gone = await stack.add(ann, "gone")
    await stack.manager.delete_task(ann, gone.id, gone.version)
    elsewhere = context(Role.MEMBER)
    theirs = await stack.add(elsewhere, "theirs")
    nothing = new_id()
    stack.pushes.clear()

    outcome = await stack.manager.change_tasks(
        ann,
        BulkAction.COMPLETE,
        [first.id, finished.id, second.id, gone.id, theirs.id, nothing, first.id],
    )

    assert outcome.action is BulkAction.COMPLETE
    assert outcome.changed == (first.id, second.id) and outcome.changed_count == 2
    assert outcome.skipped == (
        SkippedTask(id=finished.id, reason=SkipReason.ALREADY_DONE),
        SkippedTask(id=gone.id, reason=SkipReason.NOT_FOUND),
        SkippedTask(id=theirs.id, reason=SkipReason.NOT_FOUND),
        SkippedTask(id=nothing, reason=SkipReason.NOT_FOUND),
    )
    assert outcome.skipped_count == 4 and outcome.plan_bound is None
    for task in (first, second):
        stored = await stack.manager.get_task(ann, task.id)
        assert stored.status is TaskStatus.DONE and stored.version == task.version + 1
        assert stored.updated_by == ann.user_id
    assert await stack.status_of(elsewhere, theirs) is TaskStatus.OPEN, "another tenant's"
    # One push per changed task, as a single edit makes: the realtime hint and
    # the record of the change.
    assert [(p.kind, p.target_id) for p in stack.pushes] == [
        ("tasks.task.updated", first.id),
        ("tasks.task.updated", second.id),
    ]


async def test_reopen_by_ids_puts_the_last_named_on_top(stack: Stack) -> None:
    ann = context(Role.MEMBER)
    kept = await stack.add(ann, "kept open")
    one, two, three = [await stack.add(ann, t) for t in ("one", "two", "three")]
    for task in (one, two, three):
        await stack.finish(ann, task)

    outcome = await stack.manager.change_tasks(
        ann, BulkAction.REOPEN, [three.id, two.id, one.id, kept.id]
    )

    assert outcome.changed == (three.id, two.id, one.id)
    assert outcome.skipped == (SkippedTask(id=kept.id, reason=SkipReason.ALREADY_OPEN),)
    # As if reopened one at a time in the order named: the last one on top.
    # A client that wants the list to read as it did sends them bottom first.
    assert await stack.open_titles(ann) == ["one", "two", "three", "kept open"]


async def test_a_reopen_takes_a_task_off_the_archive(stack: Stack) -> None:
    ann = context(Role.MEMBER)
    task = await stack.finish(ann, await stack.add(ann, "old"))
    archived = task.model_copy(update={"archived_at": utcnow(), "version": task.version + 1})
    await stack.storage.update_task(ann.org_id, archived, task.version, ())

    outcome = await stack.manager.change_tasks(ann, BulkAction.REOPEN, [task.id])

    assert outcome.changed == (task.id,)
    reopened = await stack.manager.get_task(ann, task.id)
    assert reopened.status is TaskStatus.OPEN and reopened.archived_at is None


async def test_a_bulk_change_names_at_most_the_bound(stack: Stack) -> None:
    ann = context(Role.MEMBER)
    with pytest.raises(ValidationFailed):
        await stack.manager.change_tasks(
            ann, BulkAction.COMPLETE, [new_id() for _ in range(BULK_MAX_IDS + 1)]
        )
    outcome = await stack.manager.change_tasks(ann, BulkAction.COMPLETE, [])
    assert outcome.changed == () and outcome.skipped == () and stack.storage.commits == 0


async def test_a_viewer_changes_nothing(stack: Stack) -> None:
    org = make_org()
    ann, viewer = context(Role.MEMBER, org), context(Role.VIEWER, org)
    task = await stack.add(ann, "mine")
    with pytest.raises(NotAuthorized):
        await stack.manager.change_tasks(viewer, BulkAction.COMPLETE, [task.id])
    with pytest.raises(NotAuthorized):
        await stack.manager.change_list(viewer, BulkAction.COMPLETE, team(viewer), TaskStatus.OPEN)
    assert await stack.status_of(ann, task) is TaskStatus.OPEN


async def test_a_task_that_moved_between_the_read_and_the_write_is_skipped(
    stack: Stack,
) -> None:
    """The version fence, one task at a time: Bob's edit lands after the
    change read the batch, so that one task is left alone as `changed` and
    the rest of the batch still lands."""
    org = make_org()
    ann, bob = context(Role.MEMBER, org), context(Role.MEMBER, org)
    first, second = await stack.add(ann, "first"), await stack.add(ann, "second")

    async def bob_edits() -> None:
        await stack.manager.update_task(
            bob, second.model_copy(update={"title": "renamed"}), second.version
        )

    stack.storage.before_first_commit = bob_edits
    outcome = await stack.manager.change_tasks(ann, BulkAction.COMPLETE, [first.id, second.id])

    assert outcome.changed == (first.id,)
    assert outcome.skipped == (SkippedTask(id=second.id, reason=SkipReason.CHANGED),)
    kept = await stack.manager.get_task(ann, second.id)
    assert kept.status is TaskStatus.OPEN and kept.title == "renamed"


async def test_a_whole_list_goes_a_batch_a_commit(stack: Stack) -> None:
    ann = context(Role.MEMBER)
    total = 2 * BULK_BATCH + 5
    for index in range(total):
        await stack.add(ann, f"task {index}")
    top_first = [
        t.id for t in (await stack.manager.get_open_tasks(ann, team(ann), None, 200)).items
    ]

    outcome = await stack.manager.change_list(ann, BulkAction.COMPLETE, team(ann), TaskStatus.OPEN)

    assert outcome.changed_count == total and outcome.skipped_count == 0
    assert stack.storage.commits == 3
    # The order it wrote them in is the list's, top first.
    assert list(outcome.changed[: len(top_first)]) == top_first
    assert await stack.manager.count_tasks(ann, team(ann), TaskStatus.OPEN) == 0
    assert await stack.manager.count_tasks(ann, team(ann), TaskStatus.DONE) == total


async def test_a_whole_list_is_the_scope_asked_for(stack: Stack) -> None:
    org = make_org()
    ann = context(Role.MEMBER, org, stack.members)
    bob = context(Role.MEMBER, org, stack.members)
    mine_task = await stack.add(ann, "ann's")
    theirs = await stack.add(bob, "bob's")
    for_ann = await stack.add(bob, "for ann", assignee_id=ann.user_id)

    outcome = await stack.manager.change_list(ann, BulkAction.COMPLETE, mine(ann), TaskStatus.OPEN)

    assert set(outcome.changed) == {mine_task.id, for_ann.id}
    assert await stack.status_of(bob, theirs) is TaskStatus.OPEN


async def test_reopen_all_reads_the_done_list_and_skips_the_archive(stack: Stack) -> None:
    ann = context(Role.MEMBER)
    done = [await stack.finish(ann, await stack.add(ann, f"done {i}")) for i in range(3)]
    shelved = done[0].model_copy(update={"archived_at": utcnow(), "version": done[0].version + 1})
    await stack.storage.update_task(ann.org_id, shelved, done[0].version, ())

    outcome = await stack.manager.change_list(ann, BulkAction.REOPEN, team(ann), TaskStatus.DONE)

    assert set(outcome.changed) == {done[1].id, done[2].id}
    assert await stack.status_of(ann, done[0]) is TaskStatus.DONE, "the archive is not the list"


async def test_an_action_goes_with_its_own_list(stack: Stack) -> None:
    ann = context(Role.MEMBER)
    with pytest.raises(ValidationFailed):
        await stack.manager.change_list(ann, BulkAction.COMPLETE, team(ann), TaskStatus.DONE)
    with pytest.raises(ValidationFailed):
        await stack.manager.change_list(ann, BulkAction.REOPEN, team(ann), TaskStatus.OPEN)
    bob = context(Role.MEMBER, make_org())
    with pytest.raises(ValidationFailed):
        # A list is the caller's: `mine` is about nobody else.
        await stack.manager.change_list(ann, BulkAction.COMPLETE, mine(bob), TaskStatus.OPEN)


async def test_reopening_past_the_plan_opens_up_to_the_bound_and_names_it(
    tmp_path: Path,
) -> None:
    """Free allows ten active tasks. With eight open, a reopen of five opens
    two, in the order named, and skips three for the plan, naming the bound
    and the plan that lifts it, the way an import parks at it."""
    stack = Stack(tmp_path, FixedPlan(Plan.FREE))
    ann = context(Role.OWNER)
    finished = [await stack.add(ann, f"done {i}") for i in range(5)]
    for task in finished:
        await stack.finish(ann, task)
    for index in range(8):
        await stack.add(ann, f"open {index}")

    outcome = await stack.manager.change_tasks(
        ann, BulkAction.REOPEN, [task.id for task in finished]
    )

    assert outcome.changed == (finished[0].id, finished[1].id)
    assert outcome.skipped == tuple(
        SkippedTask(id=task.id, reason=SkipReason.PLAN_LIMIT) for task in finished[2:]
    )
    assert outcome.plan_bound == PlanBound(
        lever="active_tasks", plan="free", limit=10, suggested_plan="pro"
    )
    assert await stack.manager.count_active_tasks(ann) == 10
    # Completing is never bounded: the plan counts open tasks.
    completed = await stack.manager.change_list(
        ann, BulkAction.COMPLETE, team(ann), TaskStatus.OPEN
    )
    assert completed.changed_count == 10 and completed.plan_bound is None


async def test_the_answer_lists_up_to_the_cap_and_counts_the_rest(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager_module, "BULK_REPORT_CAP", 3)
    ann = context(Role.MEMBER)
    tasks = [await stack.add(ann, f"task {i}") for i in range(5)]
    outcome = await stack.manager.change_tasks(
        ann, BulkAction.COMPLETE, [*(t.id for t in tasks), *(new_id() for _ in range(4))]
    )
    assert outcome.changed == tuple(t.id for t in tasks[:3]) and outcome.changed_count == 5
    assert len(outcome.skipped) == 3 and outcome.skipped_count == 4


async def test_undo_is_the_other_action_over_what_changed(stack: Stack) -> None:
    """What a client's Undo sends: the other action over exactly the tasks
    the change wrote, bottom first, and the open list reads as it did. A
    task that was done before the change stays done."""
    ann = context(Role.MEMBER)
    before = await stack.finish(ann, await stack.add(ann, "done before"))
    for title in ("c", "b", "a"):
        await stack.add(ann, title)
    assert await stack.open_titles(ann) == ["a", "b", "c"]

    marked = await stack.manager.change_list(ann, BulkAction.COMPLETE, team(ann), TaskStatus.OPEN)
    undone = await stack.manager.change_tasks(
        ann, BulkAction.REOPEN, list(reversed(marked.changed))
    )

    assert undone.changed_count == 3
    assert await stack.open_titles(ann) == ["a", "b", "c"]
    assert await stack.status_of(ann, before) is TaskStatus.DONE
