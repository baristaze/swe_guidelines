"""The value objects a task list is asked with. They travel through the
manager and the storage interfaces unchanged, so the relational impl
spells the criterion in SQL and the memory impl in Python from one shape."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from acme.om.base import Platform
from acme.om.tasks.types.task import TaskScope


class TaskFilter(Platform):
    """Which tasks a list shows: every task of the org, or the caller's own.
    `user_id` is the caller, the person `mine` is about."""

    scope: TaskScope
    user_id: UUID


class TaskCursor(Platform):
    """Where the previous page of the done list ended: its last task's
    (updated_at, id). The next page is strictly before it."""

    updated_at: datetime
    id: UUID


class OpenTaskCursor(Platform):
    """Where the previous page of the open list ended: its last task's
    (rank, id). The next page is strictly after it."""

    rank: Decimal
    id: UUID
