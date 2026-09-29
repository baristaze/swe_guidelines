"""Reminders: a due date schedules one item that waits in the queue until the
first morning of that date anywhere; when it runs, it waits the rest for the
morning of the person the task is for, in their time zone, and then the task
is marked reminded and the org hears of it. An edit that moves or clears the
date, and a finished or deleted task, leave the scheduled item stale, and a
stale item sends nothing."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from slack_support import build, claim, connect, make_task, member_of, owner_of, queued
from worker_support import fast_options

from acme.infra.cache import CacheScope
from acme.om.base import utcnow
from acme.om.opcontext import OpContext
from acme.om.tasks.rules import earliest_reminder_time
from acme.om.tasks.types.task import TaskStatus
from acme.om.work.types.handler import WorkHandlerInterface, WorkParked
from acme.om.work.types.work_item import WorkItem, WorkKind, WorkStatus
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.loop import WorkerLoop
from acme.workers.maintenance.reminders import TaskReminderHandlerImpl


def today() -> date:
    return utcnow().date()


def yesterday() -> date:
    """A date whose morning has passed in every zone: nine there, at UTC-12,
    is 21:00 UTC of that day, before today began in UTC."""
    return today() - timedelta(days=1)


async def reminded_kinds(container: WorkerContainer, ctx: OpContext) -> list[str]:
    events = await container.managers.events.get_events(ctx, 0, 100)
    return [event.kind for event in events if event.kind == "tasks.task.reminded"]


async def run_due(container: WorkerContainer) -> list[WorkItem]:
    """Claims every reminder that is due and runs it, as the loop would."""
    handler = TaskReminderHandlerImpl(container.managers.tasks)
    ran: list[WorkItem] = []
    while (claimed := await claim(container, WorkKind.TASK_REMINDER)) is not None:
        ctx, item = claimed
        await handler.handle(ctx, item)
        await container.managers.work.complete(ctx, item)
        ran.append(item)
    return ran


async def test_a_due_date_schedules_one_reminder_for_the_first_morning_of_it(
    tmp_path: Path,
) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    due = today() + timedelta(days=3)
    task = await container.managers.tasks.create_task(ann, make_task(ann, due_on=due))
    [item] = await queued(container, ann, WorkKind.TASK_REMINDER, task.id)
    first_morning = datetime(due.year, due.month, due.day, 9, tzinfo=UTC) - timedelta(hours=14)
    assert item.available_at == first_morning == earliest_reminder_time(due)
    assert datetime.fromisoformat(item.payload["not_before"]) == first_morning
    assert item.created_by == ann.user_id, "the person who set the due date asked for it"
    assert await claim(container, WorkKind.TASK_REMINDER) is None, "not before its time"
    plain = await container.managers.tasks.create_task(ann, make_task(ann, "no due date"))
    assert await queued(container, ann, WorkKind.TASK_REMINDER, plain.id) == []


@pytest.mark.parametrize(
    ("zone", "utc_hour", "day_shift"),
    [
        ("Pacific/Kiritimati", 19, -1),  # UTC+14: nine there is 19:00 UTC the day before
        ("Asia/Tokyo", 0, 0),  # UTC+9
        (None, 9, 0),  # no zone sent: the morning of UTC
        ("America/Los_Angeles", 16, 0),  # UTC-7 in the summer
        ("Pacific/Pago_Pago", 20, 0),  # UTC-11
    ],
)
async def test_the_reminder_goes_out_at_nine_in_the_persons_time_zone(
    tmp_path: Path, zone: str | None, utc_hour: int, day_shift: int
) -> None:
    """A zone ahead of UTC meets its morning before UTC does, and one behind
    it after; the date is a summer one, so the offsets are the summer's."""
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    if zone is not None:
        await container.managers.tenancy.set_time_zone(ann, zone)
    due = date(today().year + 1, 7, 15)
    task = await container.managers.tasks.create_task(ann, make_task(ann, due_on=due))
    reminder = await container.managers.tasks.get_due_reminder(ann, task.id)
    assert reminder is not None and reminder.due_on == due
    day = datetime(due.year, due.month, due.day, utc_hour, tzinfo=UTC)
    assert reminder.at == day + timedelta(days=day_shift)
    assert reminder.at >= earliest_reminder_time(due), "never before the item is available"


async def test_the_assignees_zone_decides_and_the_creators_when_unassigned(
    tmp_path: Path,
) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    bob = await member_of(container, "ajax", "bob@ajax.test")
    tenancy, tasks = container.managers.tenancy, container.managers.tasks
    await tenancy.set_time_zone(ann, "Asia/Tokyo")
    await tenancy.set_time_zone(bob, "America/New_York")
    due = date(today().year + 1, 1, 20)  # winter: New York is UTC-5
    mine = await tasks.create_task(ann, make_task(ann, due_on=due))
    his = await tasks.create_task(ann, make_task(ann, due_on=due, assignee_id=bob.user_id))
    mine_at = await tasks.get_due_reminder(ann, mine.id)
    his_at = await tasks.get_due_reminder(ann, his.id)
    assert mine_at is not None and mine_at.at == datetime(due.year, 1, 20, 0, tzinfo=UTC)
    assert his_at is not None and his_at.at == datetime(due.year, 1, 20, 14, tzinfo=UTC)


async def test_before_the_persons_morning_the_item_parks_until_then(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    due = today() + timedelta(days=2)
    task = await container.managers.tasks.create_task(ann, make_task(ann, due_on=due))
    [item] = await queued(container, ann, WorkKind.TASK_REMINDER, task.id)
    with pytest.raises(WorkParked) as parked:
        await TaskReminderHandlerImpl(container.managers.tasks).handle(ann, item)
    left = datetime(due.year, due.month, due.day, 9, tzinfo=UTC) - utcnow()
    assert abs(parked.value.resume_after - left) < timedelta(seconds=5)
    assert await reminded_kinds(container, ann) == []


async def test_the_reminder_goes_out_once_and_is_announced(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    task = await container.managers.tasks.create_task(ann, make_task(ann, due_on=yesterday()))
    [item] = await run_due(container)
    stored = await container.managers.tasks.get_task(ann, task.id)
    assert stored.reminded_at is not None and stored.version == task.version + 1
    assert await reminded_kinds(container, ann) == ["tasks.task.reminded"]
    assert await container.managers.tasks.get_due_reminder(ann, task.id) is None
    # A second run of the same item, the at-least-once case, sends nothing.
    fired = await container.managers.tasks.fire_reminder(ann, task.id, yesterday())
    assert fired is None
    assert await reminded_kinds(container, ann) == ["tasks.task.reminded"]
    assert item.target_id == task.id


async def test_moving_the_due_date_leaves_the_first_reminder_stale(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    tasks = container.managers.tasks
    first = yesterday() - timedelta(days=1)
    task = await tasks.create_task(ann, make_task(ann, due_on=first))
    await tasks.update_task(ann, task.model_copy(update={"due_on": yesterday()}), task.version)
    assert len(await queued(container, ann, WorkKind.TASK_REMINDER, task.id)) == 2
    assert len(await run_due(container)) == 2
    assert await reminded_kinds(container, ann) == ["tasks.task.reminded"], "the moved one alone"
    # The one write names the date it read, so the first date's cannot land.
    assert await tasks.fire_reminder(ann, task.id, first) is None


async def test_a_new_due_date_after_a_reminder_is_reminded_again(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    tasks = container.managers.tasks
    task = await tasks.create_task(ann, make_task(ann, due_on=yesterday() - timedelta(days=1)))
    await run_due(container)
    reminded = await tasks.get_task(ann, task.id)
    later = reminded.model_copy(update={"due_on": yesterday()})
    again = await tasks.update_task(ann, later, reminded.version)
    assert again.reminded_at is None, "a new due date has not been reminded of"
    await run_due(container)
    assert len(await reminded_kinds(container, ann)) == 2


async def test_clearing_the_due_date_cancels_the_reminder(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    tasks = container.managers.tasks
    task = await tasks.create_task(ann, make_task(ann, due_on=yesterday()))
    cleared = await tasks.update_task(ann, task.model_copy(update={"due_on": None}), 1)
    assert cleared.due_on is None
    assert len(await run_due(container)) == 1
    assert await reminded_kinds(container, ann) == []
    assert (await tasks.get_task(ann, task.id)).reminded_at is None


async def test_a_reassigned_task_asks_again_for_the_new_persons_morning(tmp_path: Path) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    bob = await member_of(container, "ajax", "bob@ajax.test")
    tasks = container.managers.tasks
    task = await tasks.create_task(ann, make_task(ann, due_on=yesterday()))
    handed = task.model_copy(update={"assignee_id": bob.user_id})
    await tasks.update_task(ann, handed, task.version)
    assert len(await queued(container, ann, WorkKind.TASK_REMINDER, task.id)) == 2
    await run_due(container)
    assert await reminded_kinds(container, ann) == ["tasks.task.reminded"], "one of the two lands"


@pytest.mark.parametrize("ending", ["done", "deleted"])
async def test_a_finished_or_deleted_task_is_not_reminded(tmp_path: Path, ending: str) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    tasks = container.managers.tasks
    task = await tasks.create_task(ann, make_task(ann, due_on=yesterday()))
    if ending == "done":
        await tasks.update_task(ann, task.model_copy(update={"status": TaskStatus.DONE}), 1)
    else:
        await tasks.delete_task(ann, task.id, 1)
    await run_due(container)
    assert await reminded_kinds(container, ann) == []


async def test_a_reminder_asks_for_the_slack_post_when_a_channel_is_bound(
    tmp_path: Path,
) -> None:
    container, twin = build(tmp_path)
    ann = await owner_of(container, "ajax")
    await connect(container, twin, ann)
    task = await container.managers.tasks.create_task(ann, make_task(ann, due_on=yesterday()))
    await run_due(container)
    posts = await queued(container, ann, WorkKind.SLACK_POST, task.id)
    assert [item.payload["event"] for item in posts] == ["created", "reminded"]


class Parking(WorkHandlerInterface):
    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        raise WorkParked("slack said wait", timedelta(minutes=3))


async def test_the_loop_hands_a_parked_item_back_without_spending_an_attempt(
    tmp_path: Path,
) -> None:
    container, _ = build(tmp_path)
    ann = await owner_of(container, "ajax")
    task = await container.managers.tasks.create_task(ann, make_task(ann, due_on=yesterday()))
    loop = WorkerLoop(
        work=container.managers.work,
        outbox=container.managers.outbox,
        purges={},
        handlers={WorkKind.TASK_REMINDER: Parking()},
        topics=container.infra.get_topics(),
        liveness=container.infra.get_cache(CacheScope.WORKER_LIVENESS),
        options=fast_options(),
    )
    running = asyncio.create_task(loop.run())
    [item] = await queued(container, ann, WorkKind.TASK_REMINDER, task.id)
    storage = container.storage.get_work_storage()
    stored: WorkItem | None = None
    for _ in range(200):
        stored = await storage.read_item(ann.org_id, item.id)
        if stored is not None and stored.last_error:
            break
        await asyncio.sleep(0.01)
    loop.stop()
    await running
    assert stored is not None
    assert stored.status is WorkStatus.QUEUED and stored.attempts == 0
    assert stored.last_error == "parked: slack said wait"
    assert stored.available_at > utcnow() + timedelta(minutes=2)
