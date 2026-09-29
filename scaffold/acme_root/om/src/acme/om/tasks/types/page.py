"""What a task list answers with: one page, and whether another follows.
The manager asks storage for one row more than the page and keeps it out,
so `has_more` is a fact about the rows and not a guess about the count."""

from acme.om.base import Platform
from acme.om.tasks.types.task import Task


class TaskPage(Platform):
    items: tuple[Task, ...]
    has_more: bool
