"""Pure: how a task is shown, how a change is told, which tasks are mine,
how a short id names a task, and which org a slug names. Values in, values
out; no client, no clock, no terminal, so every rule is unit tested without
either."""

import re
from collections.abc import Callable, Sequence
from datetime import date
from uuid import UUID

from acme.client.types import FileView, MembershipChoiceView, OrgKind, TaskStatus, TaskView

SHORT_ID = 8
NameOf = Callable[[UUID | None], str]


def short_id(task_id: UUID) -> str:
    """The tail of the id: ids are time-ordered, so their heads are alike for
    everything made in the same minute and their tails are the random part."""
    return str(task_id)[-SHORT_ID:]


def is_mine(task: TaskView | None, me: UUID) -> bool:
    """The API's `mine` scope: assigned to me, or unassigned and created by me."""
    if task is None:
        return False
    if task.assignee_id is not None:
        return task.assignee_id == me
    return task.created_by == me


def resolve(reference: str, tasks: Sequence[TaskView]) -> TaskView:
    """A full id or a unique tail of one, over the tasks the caller can see."""
    matches = [t for t in tasks if str(t.id).endswith(reference.lower())]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"no task matches {reference!r}")
    listed = ", ".join(short_id(t.id) for t in matches[:5])
    raise LookupError(f"{reference!r} matches more than one task ({listed}); give more of the id")


def human_size(size_bytes: int) -> str:
    """Bytes as a person reads them: B under a kilobyte, then KB and MB with
    one decimal, powers of 1024."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def attachment_table(files: Sequence[FileView]) -> str:
    header = f"{'ID':<{SHORT_ID}}  {'SIZE':>9}  {'TYPE':<24}  NAME"
    lines = [
        f"{short_id(f.id)}  {human_size(f.size_bytes):>9}  {f.content_type[:24]:<24}  {f.name}"
        for f in files
    ]
    return "\n".join([header, *lines])


def resolve_file(reference: str, files: Sequence[FileView]) -> FileView:
    """A full id or a unique tail of one, over a task's attachments."""
    matches = [f for f in files if str(f.id).endswith(reference.lower())]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"no attachment matches {reference!r}")
    raise LookupError(f"{reference!r} matches more than one attachment; give more of the id")


def task_line(task: TaskView, name_of: NameOf) -> str:
    assignee = name_of(task.assignee_id) if task.assignee_id else "-"
    return f"{short_id(task.id)}  {task.status.value:<6}  {assignee:<12}  {task.title}"


def task_table(tasks: Sequence[TaskView], name_of: NameOf) -> str:
    header = f"{'ID':<{SHORT_ID}}  {'STATUS':<6}  {'ASSIGNEE':<12}  TITLE"
    return "\n".join([header, *(task_line(t, name_of) for t in tasks)])


def describe(
    action: str, before: TaskView | None, after: TaskView | None, actor: str, name_of: NameOf
) -> str:
    """One line that says what someone did to a task. An update names the
    first difference that matters, in this order: status, title, assignee,
    notes, order."""
    known = after or before
    title = known.title if known else "a task"
    if action == "created":
        return f"{actor} created a task: {title}"
    if action == "deleted":
        return f"{actor} deleted a task: {title}"
    if action == "reminded":
        return f"reminder: {title}"
    if before is None or after is None:
        return f"{actor} updated a task: {title}"
    if before.status != after.status:
        verb = "completed" if after.status == TaskStatus.done else "reopened"
        return f"{actor} {verb} a task: {title}"
    if before.title != after.title:
        return f'{actor} renamed a task "{before.title}" to "{after.title}"'
    if before.assignee_id != after.assignee_id:
        if after.assignee_id is None:
            return f"{actor} unassigned a task: {title}"
        return f"{actor} assigned a task to {name_of(after.assignee_id)}: {title}"
    if before.notes != after.notes:
        return f"{actor} edited the notes of a task: {title}"
    if before.rank != after.rank:
        return f"{actor} moved a task: {title}"
    return f"{actor} updated a task: {title}"


def choose_org(
    memberships: Sequence[MembershipChoiceView], slug: str | None
) -> MembershipChoiceView:
    """The org a sign-in or a switch enters: the one `slug` names, or, when no
    slug is given, the only one, else the person's personal org, the place
    every person has. Anything else is a LookupError whose message is the
    slugs to choose from."""
    choices = [m for m in memberships if slug is None or m.org.slug == slug]
    if len(choices) == 1:
        return choices[0]
    personal = [m for m in choices if slug is None and m.org.kind is OrgKind.personal]
    if len(personal) == 1:
        return personal[0]
    raise LookupError(", ".join(sorted(m.org.slug for m in memberships)) or "none")


def org_lines(memberships: Sequence[MembershipChoiceView], current: str | None) -> str:
    """One line per org, by name, the current one marked with `*` and the
    person's personal org said as such."""
    ordered = sorted(memberships, key=lambda m: (m.org.name.lower(), m.org.slug))
    width = max((len(m.org.slug) for m in ordered), default=0)
    return "\n".join(
        f"{'*' if m.org.slug == current else ' '} {m.org.slug:<{width}}"
        f"  {m.org.name} ({m.role.value}{', personal' if m.org.kind is OrgKind.personal else ''})"
        for m in ordered
    )


DUE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_due(text: str) -> date:
    """A due date as a person types it: `2026-10-01`, a date and never a
    time. A ValueError says what was wrong."""
    text = text.strip()
    try:
        if not DUE_DATE.match(text):
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a due date; give one as 2026-10-01") from None
