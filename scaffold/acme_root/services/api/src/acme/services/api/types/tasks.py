from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator

from acme.om.orchestrations.types.orchestration import (
    FailReason,
    OrchestrationStatus,
    ParkReason,
)
from acme.om.tasks.rules import BULK_MAX_IDS
from acme.om.tasks.types.bulk import BulkAction, SkipReason
from acme.om.tasks.types.task import TaskScope, TaskStatus
from acme.services.api.types.common import PlanLimitDetail, RequestBody, View


class TaskView(View):
    id: UUID
    title: str
    notes: str
    status: TaskStatus
    assignee_id: UUID | None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: datetime | None
    version: int
    rank: str = Field(
        description=(
            "Where an open task sits in the open list, which is ascending by"
            " rank, then by id. An exact decimal number, written out in full:"
            " compare two as numbers, never as floats and never as text."
        ),
    )
    # Later fields default, so a client reads a response from a build that
    # predates them.
    due_on: date | None = None
    reminded_at: datetime | None = None
    # Set when the daily cleanup archived the task; null otherwise.
    archived_at: datetime | None = None
    # Read from the task's rank: the view sends the rank's float, and the
    # task has no position of its own.
    position: float | None = Field(
        default=None,
        validation_alias="rank",
        description=(
            "The rank as a float, for a client of the release before, which"
            " requires it. Order by `rank`: no client reads this, and it"
            " leaves the wire in the release after this one."
        ),
        json_schema_extra={"deprecated": True},
    )

    @field_validator("rank", mode="before")
    @classmethod
    def _written_out(cls, value: object) -> object:
        """A rank is sent in full, never in exponent form: `0.00001`, not
        `1E-5`, so a client compares digits it can read."""
        return format(value, "f") if isinstance(value, Decimal) else value


class TaskPageView(View):
    """One page of a task list. `next_cursor` fetches the next page of the same
    list, open or done, and is null on the last page. The page size is
    clamped, and a list the clamp cut still says a page follows."""

    items: list[TaskView]
    next_cursor: str | None


class AddTaskRequest(RequestBody):
    """`due_on` is the day the task is due, `YYYY-MM-DD`, never a time. It
    schedules one reminder at nine in the morning of that day, in the time
    zone of the person the task is for (the assignee, or the creator), pushed
    to every open screen of the org and posted to its Slack channel when one
    is connected."""

    title: str = Field(max_length=500)
    notes: str = ""
    assignee_id: UUID | None = None
    due_on: date | None = None


class UpdateTaskRequest(RequestBody):
    """A partial update: absent fields are kept. An explicit null
    `assignee_id` unassigns the task. The version the update compares with is
    the task's as the caller read it, in the `If-Match` header: the update
    lands only when the task is still at it, and is refused with 412
    `precondition_failed` when another write landed since, so the caller reads
    again and decides over the current task. An update that names no version
    is refused with 422 `validation_failed`, since it would overwrite blind.
    An explicit null `due_on` clears the due date; a new one reschedules the
    reminder, and the one scheduled before it never goes out."""

    title: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    status: TaskStatus | None = None
    assignee_id: UUID | None = None
    due_on: date | None = None


class MoveTaskRequest(RequestBody):
    """Places an open task right after `after_id`; null puts it at the top.
    `expected_version` is the moved task's as the caller read it: 412
    `precondition_failed` when the task changed since, and 422
    `validation_failed` when the request names no version."""

    after_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)


class RestoreTaskRequest(RequestBody):
    """Takes an archived task back to the done list. `expected_version` is the
    task's as the caller read it: 412 `precondition_failed` when it changed
    since, and 422 `validation_failed` when the request names none or the
    task is not archived."""

    expected_version: int | None = Field(default=None, ge=1)


class TaskCountView(View):
    """How many tasks one list shows: the open list, or the done list without
    the archived tasks, in the scope asked for."""

    status: TaskStatus
    scope: TaskScope
    count: int


class BulkListRequest(RequestBody):
    """A whole list, by the scope and the status it shows."""

    scope: TaskScope
    status: TaskStatus


class BulkTasksRequest(RequestBody):
    """A change to many tasks in one call: `complete` or `reopen`, over the
    tasks named in `ids` (at most 1000) or over every task of one list in
    `all`, never both. `all` reads the list on the server, not the page a
    client loaded: `complete` goes with the open list and `reopen` with the
    done list. Each task is an edit of its own, under the rules a single edit
    applies and fenced on the version the call read: a task that is not the
    org's, already in the status asked for, or changed between the read and
    the write is skipped and named, never a refusal of the rest. A reopen
    puts each task on top of the open list, the last one named on top, up to
    the plan's bound on active tasks. The call runs under an
    `Idempotency-Key`, and a retry of it answers what the first one did."""

    action: BulkAction
    ids: list[UUID] | None = Field(default=None, max_length=BULK_MAX_IDS)
    all: BulkListRequest | None = None


class SkippedTaskView(View):
    """A task the bulk change left alone, and why: `not_found`,
    `already_done`, `already_open`, `changed`, or `plan_limit`."""

    id: UUID
    reason: SkipReason


class BulkTasksView(View):
    """What a bulk change did. `changed` lists the tasks it wrote, in the order
    it wrote them, and `skipped` the ones it left alone; each lists at most
    1000, and the counts beside them are whole. To undo a change, send the
    other action with `changed` as `ids`. `plan_limit` is set when a reopen
    met the plan's bound on active tasks: the tasks past it are skipped as
    `plan_limit`, and it carries what a `plan_limit_reached` refusal does, so
    a client offers the plan that lifts it. Every changed task is announced
    on the realtime channel as `tasks.task.updated`, as a single edit is."""

    action: BulkAction
    changed: list[UUID]
    changed_count: int
    skipped: list[SkippedTaskView]
    skipped_count: int
    plan_limit: PlanLimitDetail | None = None


class StartImportRequest(RequestBody):
    """The import of a CSV file uploaded under the `task_import` purpose
    (`POST /v1/tasks/imports/files`, then the media routes) and confirmed.
    Its columns are `title` (needed), `notes`, `due_on` (`YYYY-MM-DD`), and
    `assignee_email` (a member of the org), by header name."""

    file_id: UUID


class RowErrorView(View):
    """A row the import skipped: its number, the first data row being 1, and why."""

    row: int
    reason: str


class ImportView(View):
    """An import of tasks from a CSV file, read as it runs. `status` is
    `running`, `parked`, `succeeded`, or `failed`. `total` is the file's data
    rows, null until the first step counted them; `cursor` is how many were
    read; `created` the tasks the import made, `skipped` the rows it passed
    over, and `row_errors` the first twenty of those with the reason. A
    `parked` import names its `park_reason`: `plan_limit` is the plan's bound
    on active tasks, lifted by a higher plan (which resumes it) or by tasks
    finished and `POST /v1/tasks/imports/{id}/resume`. A `failed` one names
    its `fail_reason`: `file_too_large`, `too_many_rows`, `not_csv`,
    `no_title_column`, `file_gone`, or `defect`. Every change is pushed as
    `orchestrations.orchestration.updated` on the realtime channel."""

    id: UUID
    file_id: UUID
    status: OrchestrationStatus
    total: int | None
    cursor: int
    created: int
    skipped: int
    row_errors: list[RowErrorView]
    park_reason: ParkReason | None
    fail_reason: FailReason | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    created_by: UUID


class ImportPageView(View):
    """The org's newest imports, newest first."""

    items: list[ImportView]
