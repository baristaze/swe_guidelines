"""The reminder handler: a task's due date came, so the team hears of it.

A due date has no hour. Its reminder goes out at nine in the morning of the
date, in the time zone of the person the task is for: the assignee, or the
creator when the task is unassigned (`tasks.rules.reminder_time`). The item
was scheduled by the write that set the date, for the first moment any zone's
morning of it comes. When it runs, the handler reads the task and the person
as they are now; before their morning, it parks until then.

Whether the reminder still means anything is the task's to say, in one
conditional write: open, living, still due on the date read, and not yet
reminded. An edit that moved or cleared the date, a task finished or deleted
meanwhile, and a second item or a second run for a reminder that went out all
make that write land nothing, and the item completes without a word."""

import logging
from typing import ClassVar

from acme.om.base import utcnow
from acme.om.opcontext import OpContext, Permission
from acme.om.tasks import TasksManagerInterface
from acme.om.work.types.handler import WorkHandlerInterface, WorkParked
from acme.om.work.types.work_item import WorkItem

log = logging.getLogger(__name__)


class TaskReminderHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.READ, Permission.WRITE)
    """`get_due_reminder` reads; `fire_reminder` writes."""

    def __init__(self, tasks: TasksManagerInterface) -> None:
        self._tasks = tasks

    async def handle(self, ctx: OpContext, item: WorkItem) -> None:
        due = await self._tasks.get_due_reminder(ctx, item.target_id)
        if due is None:
            log.info("reminder %s for task %s is stale; nothing sent", item.id, item.target_id)
            return
        early = due.at - utcnow()
        if early.total_seconds() > 0:
            # Before the person's morning: the item waits in the queue for it.
            raise WorkParked("the reminder is not due yet", early)
        fired = await self._tasks.fire_reminder(ctx, item.target_id, due.due_on)
        if fired is None:
            log.info("reminder %s for task %s is stale; nothing sent", item.id, item.target_id)
            return
        log.info("reminder %s for task %s went out", item.id, item.target_id)
