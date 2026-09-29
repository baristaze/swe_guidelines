"""Pure rules of the tasks namespace: which tasks a filter shows, where a
cursor cuts, the ranks of the open list's manual order, when a due
date's reminder goes out, what an import file and its rows may be, which
done tasks the cleanup archives, and which tasks a bulk change leaves alone.
Values in,
values out; no clock, no storage, no settings. The manager and both storage
impls call these; the relational impl spells the visibility, cursor, and
follows rules in SQL where one statement must decide, and names the rule it
mirrors."""

import csv
import io
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, ROUND_CEILING, ROUND_FLOOR, Context, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from acme.om.base import Platform
from acme.om.exceptions import ValidationFailed
from acme.om.tasks.types.bulk import BulkAction, SkipReason
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.task import Task, TaskScope, TaskStatus
from acme.om.tenancy.rules import fold_email

Place = tuple[Decimal, UUID]
"""Where an open task sits: its rank and its id, the pair the open list is
ordered by. A rank is not unique: two writers that read the same list place
two tasks at the same one. So the list, the cursor, and every read of places
compare the pair, and the id decides between two tasks that share a rank."""

RANK_SCALE_BOUND = 24
"""The longest rank a move leaves as it is, in digits after the point. A move
always writes its one row; a rank past this is respaced later, by the sweep
(`respace_run`). Seventy-odd moves into one and the same gap make a rank this
long."""

RANK_SCALE_SHORT = 12
"""A rank at most this long bounds a respaced run: the run is every task
between two such ranks, and it takes ranks between them that are short again."""

RESPACE_REACH = 100
"""How far a respace looks from the long rank, each way, for the ranks that
bound its run; the most tasks one respace writes is twice this, and one."""


_EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
"""Rank arithmetic never rounds: the context holds every digit a rank has."""


def rank_scale(rank: Decimal) -> int:
    """How many digits a rank has after the point, trailing zeros aside."""
    exponent = rank.normalize(_EXACT).as_tuple().exponent
    assert isinstance(exponent, int), "a rank is a finite number"
    return max(0, -exponent)


def _plain(units: int, scale: int) -> Decimal:
    """`units` times ten to the minus `scale`, written without trailing zeros
    and never in exponent form, so a rank reads the same everywhere."""
    while scale > 0 and units % 10 == 0:
        units //= 10
        scale -= 1
    return Decimal(units).scaleb(-scale, _EXACT)


def spread(low: Decimal | None, high: Decimal | None, count: int) -> list[Decimal]:
    """`count` ranks strictly between `low` and `high`, ascending and evenly
    spaced, each as short as it can be. None is the open end: above the top
    they are whole numbers below it, below the bottom whole numbers after it,
    and in an empty list they start at zero. Between two ranks they take the
    fewest digits after the point that leave room for all of them, which is
    at most one or two more than the longer of the two. `low` must be below
    `high`."""
    if count <= 0:
        return []
    if low is None and high is None:
        return [Decimal(index) for index in range(count)]
    if low is None:
        assert high is not None
        top = int(high.to_integral_value(ROUND_FLOOR, _EXACT))
        return [Decimal(top - count + index) for index in range(count)]
    if high is None:
        bottom = int(low.to_integral_value(ROUND_FLOOR, _EXACT))
        return [Decimal(bottom + 1 + index) for index in range(count)]
    if not low < high:
        raise ValueError(f"no rank lies between {low} and {high}")
    scale = 0
    while True:
        first = int(low.scaleb(scale, _EXACT).to_integral_value(ROUND_FLOOR, _EXACT)) + 1
        last = int(high.scaleb(scale, _EXACT).to_integral_value(ROUND_CEILING, _EXACT)) - 1
        room = last - first + 1
        if room >= count:
            return [
                _plain(first + (index + 1) * (room + 1) // (count + 1) - 1, scale)
                for index in range(count)
            ]
        scale += 1


def rank_between(low: Decimal | None, high: Decimal | None) -> Decimal:
    """The one rank strictly between `low` and `high` (`spread` of one): the
    shortest there is, near the middle."""
    return spread(low, high, 1)[0]


def top_rank(places: Sequence[Place]) -> Decimal:
    """The rank above every open task, given their places ascending: the
    whole number below the smallest rank, or zero when the list is empty.
    Only the first place is read, so the top place alone is enough."""
    return rank_between(None, places[0][0] if places else None)


def rank_after(anchor: Decimal, following: Decimal | None) -> Decimal:
    """The rank right after an anchor whose rank is `anchor`, given the
    smallest rank past it (`following`, None when the anchor is last): a rank
    between the two, so the move writes the moved task and no other. The
    following rank is strictly past the anchor's; a task that shares the
    anchor's rank is not between them, so a task placed after one of two
    tasks that share a rank goes after both."""
    return rank_between(anchor, following)


def needs_respace(rank: Decimal) -> bool:
    """Whether a rank has grown past `RANK_SCALE_BOUND`, and its run is the
    sweep's to respace."""
    return rank_scale(rank) > RANK_SCALE_BOUND


def is_short(rank: Decimal) -> bool:
    """Whether a rank may bound a respaced run (`RANK_SCALE_SHORT`)."""
    return rank_scale(rank) <= RANK_SCALE_SHORT


class Run(Platform):
    """The tasks a respace gives new ranks, top first, and the ranks around
    them that stay: `low` above the first, `high` below the last, None at an
    end of the list."""

    places: tuple[Place, ...]
    low: Decimal | None
    high: Decimal | None


def respace_run(long: Place, above: Sequence[Place], below: Sequence[Place], reach: int) -> Run:
    """The run around a rank that grew too long. `above` is the places before
    it, nearest first, and `below` the places after it, nearest first, each
    read `reach` deep. The run is every place from the nearest short rank
    above to the nearest short rank below (`is_short`), those two left out
    and kept. A side read to its end without one is open (None) when it is
    shorter than `reach`, since the list ends there; when it is `reach` long,
    its farthest place bounds the run instead, so one respace writes at most
    twice `reach` tasks and one."""
    before: list[Place] = []
    low: Decimal | None = None
    for index, place in enumerate(above):
        if is_short(place[0]) or index == reach - 1:
            low = place[0]
            break
        before.append(place)
    after: list[Place] = []
    high: Decimal | None = None
    for index, place in enumerate(below):
        if is_short(place[0]) or index == reach - 1:
            high = place[0]
            break
        after.append(place)
    return Run(places=(*reversed(before), long, *after), low=low, high=high)


def respaced(run: Run) -> list[Decimal]:
    """The new ranks of a run's places, in their order: evenly spread between
    the ranks around it, short again."""
    return spread(run.low, run.high, len(run.places))


def is_visible(task: Task, criterion: TaskFilter) -> bool:
    """`team` shows every task; `mine` shows a task assigned to the person, or
    unassigned and created by them."""
    if criterion.scope is TaskScope.TEAM:
        return True
    if task.assignee_id is not None:
        return task.assignee_id == criterion.user_id
    return task.created_by == criterion.user_id


def is_before(task: Task, cursor: TaskCursor) -> bool:
    """The done list is newest first; a task is on the next page when its
    (updated_at, id) sorts strictly before the cursor's."""
    return (task.updated_at, task.id) < (cursor.updated_at, cursor.id)


def is_after(task: Task, cursor: OpenTaskCursor) -> bool:
    """The open list is by rank, top first; a task is on the next page when
    its (rank, id) sorts strictly after the cursor's."""
    return (task.rank, task.id) > (cursor.rank, cursor.id)


REMINDER_HOUR = time(9)
"""A due date has no hour, so its reminder takes one: nine in the morning of
the due date, in the time zone of the person the task is for."""

AHEAD_OF_UTC_AT_MOST = timedelta(hours=14)
"""The furthest any time zone runs ahead of UTC (Pacific/Kiritimati): no
person's morning of a date comes before nine there."""


def reminder_zone(name: str | None) -> ZoneInfo:
    """The zone a reminder is timed in: the person's, by its IANA name, or UTC
    when they have none or the name is not one this process knows."""
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            pass
    return ZoneInfo("UTC")


def reminder_person(task: Task) -> UUID:
    """Whose morning the reminder keeps: the assignee's, or the creator's when
    the task is unassigned."""
    return task.assignee_id if task.assignee_id is not None else task.created_by


def reminder_time(due_on: date, time_zone: str | None) -> datetime:
    """When the reminder of `due_on` goes out: nine in the morning of that
    day in the zone named, in UTC. A day that skips or repeats nine (a
    daylight-saving change at that hour) takes the first reading."""
    local = datetime.combine(due_on, REMINDER_HOUR, tzinfo=reminder_zone(time_zone))
    return local.astimezone(UTC)


def earliest_reminder_time(due_on: date) -> datetime:
    """The first moment any person's reminder of `due_on` can go out: nine
    in the morning in the zone furthest ahead of UTC. The reminder's work
    item waits in the queue until then, and its handler waits the rest from
    the person's zone as it reads when it runs, so an assignee changed or a
    zone moved after the date was set is still met on their morning."""
    return datetime.combine(due_on, REMINDER_HOUR, tzinfo=UTC) - AHEAD_OF_UTC_AT_MOST


# The import of tasks from a CSV file, and the cleanup of old done tasks. The
# numbers are illustrative, like the plans': the shape is what holds.

IMPORT_COLUMNS = ("title", "notes", "due_on", "assignee_email")
"""The columns an import reads, by header name, in any order; `title` is the
one it needs. A column it does not know is ignored."""

IMPORT_MAX_ROWS = 5000
"""The most data rows one import reads. A file with more fails at once and
creates nothing: a bound, not a guard."""

IMPORT_BATCH = 100
"""The rows one step reads: one commit creates their tasks and moves the cursor."""

CLEANUP_BATCH = 500
"""The tasks one cleanup step archives, in one conditional write."""

BULK_BATCH = 100
"""The tasks one commit of a bulk change writes: a section of thousands is
many short transactions, never one long one."""

BULK_MAX_IDS = 1000
"""The most tasks a bulk change names by id. It is also the report's cap
(`BULK_REPORT_CAP`), so every change a report names in full can be undone by
naming its tasks back."""

BULK_REPORT_CAP = BULK_MAX_IDS
"""The most ids a bulk change's answer lists, of the tasks it changed and of
the ones it skipped; the counts beside the lists are whole."""


def bulk_ranks(top: Decimal | None, count: int) -> list[Decimal]:
    """The ranks of `count` tasks a bulk reopen puts on top of the open list,
    given the rank of the current top (None when the list is empty): each
    one above the one before, as if they were reopened one at a time in the
    order given, so the last one given is the top."""
    return list(reversed(spread(None, top, count)))


def bulk_skip(task: Task | None, action: BulkAction) -> SkipReason | None:
    """Why a bulk change leaves this task alone, or None when it changes it.
    The rules a single edit applies: a task that is gone is not found, and
    each action changes only a task in the status it starts from. A task
    in the other status was changed by someone first, and the change it asked
    for is already true."""
    if task is None or task.deleted_at is not None:
        return SkipReason.NOT_FOUND
    if action is BulkAction.COMPLETE and task.status is not TaskStatus.OPEN:
        return SkipReason.ALREADY_DONE
    if action is BulkAction.REOPEN and task.status is not TaskStatus.DONE:
        return SkipReason.ALREADY_OPEN
    return None


MAX_TITLE_LENGTH = 500


class ImportFileRefused(ValidationFailed):
    """The file is past a bound of the import; `reason` names which one,
    as `orchestrations.types.FailReason` spells it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ImportRow(Platform):
    """One data row, as the file holds it; every field a string."""

    number: int  # the first data row is 1
    title: str = ""
    notes: str = ""
    due_on: str = ""
    assignee_email: str = ""


class ImportedTask(Platform):
    """A row that makes a task: its fields checked and typed."""

    number: int
    title: str
    notes: str
    due_on: date | None
    assignee_id: UUID | None


def parse_import(data: bytes, max_bytes: int, max_rows: int = IMPORT_MAX_ROWS) -> list[ImportRow]:
    """The file's data rows, or `ImportFileRefused` when the file is past a
    bound: larger than `max_bytes` (`file_too_large`), not text a CSV reader
    reads (`not_csv`), without a `title` header (`no_title_column`), or with
    more than `max_rows` rows (`too_many_rows`). A row that is empty in every
    column is not a row. The whole file is read every step: it is bounded,
    and a stateless worker holds nothing between two."""
    if len(data) > max_bytes:
        raise ImportFileRefused("file_too_large")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileRefused("not_csv") from None
    if "\x00" in text:
        raise ImportFileRefused("not_csv")
    try:
        lines = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise ImportFileRefused("not_csv") from None
    if not lines:
        raise ImportFileRefused("no_title_column")
    header = [name.strip().lower() for name in lines[0]]
    if "title" not in header:
        raise ImportFileRefused("no_title_column")
    columns = {name: index for index, name in enumerate(header) if name in IMPORT_COLUMNS}
    rows: list[ImportRow] = []
    for cells in lines[1:]:
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) == max_rows:
            raise ImportFileRefused("too_many_rows")
        fields = {
            name: cells[index].strip() if index < len(cells) else ""
            for name, index in columns.items()
        }
        rows.append(ImportRow(number=len(rows) + 1, **fields))
    return rows


def import_refusal(row: ImportRow, members: Mapping[str, UUID]) -> str | None:
    """Why a row makes no task, or None when it makes one: a title it lacks
    or one too long, a due date that is not `YYYY-MM-DD`, an assignee who is
    not a member of the org. `members` maps a member's address, folded
    (`tenancy.rules.fold_email`), to the member."""
    if not row.title:
        return "no title"
    if len(row.title) > MAX_TITLE_LENGTH:
        return f"a title is at most {MAX_TITLE_LENGTH} characters"
    if row.due_on and _date(row.due_on) is None:
        return f"due_on {row.due_on!r} is not a date (YYYY-MM-DD)"
    if row.assignee_email and fold_email(row.assignee_email) not in members:
        return f"{row.assignee_email} is not a member of this org"
    return None


def imported(row: ImportRow, members: Mapping[str, UUID]) -> ImportedTask:
    """The task a row makes, once `import_refusal` found nothing wrong."""
    return ImportedTask(
        number=row.number,
        title=row.title,
        notes=row.notes,
        due_on=_date(row.due_on) if row.due_on else None,
        assignee_id=members[fold_email(row.assignee_email)] if row.assignee_email else None,
    )


def _date(value: str) -> date | None:
    if len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def room_for(bound: int | None, active: int) -> int | None:
    """How many more tasks the plan lets the org open: None when it has no
    bound, and never below zero (an org over its bound after a downgrade has
    no room, and keeps what it has)."""
    return None if bound is None else max(0, bound - active)


def cleanup_period(now: datetime) -> str:
    """The day a cleanup record is for: the UTC date, which with the org and
    the kind is the record's unique key, and from which its id is derived."""
    return now.astimezone(UTC).date().isoformat()


def archive_cutoff(now: datetime, archive_after: timedelta) -> datetime:
    """The day's cleanup archives the done tasks unchanged since before this:
    the start of the UTC day `cleanup_period` names, less the archive age.
    The cut holds all day. So once the day's record has run, no task of the
    org is archivable again before the next day begins, and the sweep's read
    of the tenants with a chore due stops finding the org."""
    day = datetime.combine(now.astimezone(UTC).date(), time(), tzinfo=UTC)
    return day - archive_after


def import_row_part(number: int) -> str:
    """What an imported row's task id is derived from beside the import's id
    (`base.derived_id`): the same row stepped twice presents the same id."""
    return f"row:{number}"


def cleanup_part(period: str) -> str:
    """What the day's cleanup record id is derived from beside the org's id."""
    return f"task_cleanup:{period}"


def is_archivable(task: Task, before: datetime) -> bool:
    """A task the cleanup archives: done, not deleted, not archived yet, and
    unchanged since before `before`. A task reopened, edited, or deleted
    meanwhile no longer is, and the conditional write leaves it alone."""
    return (
        task.status is TaskStatus.DONE
        and task.deleted_at is None
        and task.archived_at is None
        and task.updated_at < before
    )
