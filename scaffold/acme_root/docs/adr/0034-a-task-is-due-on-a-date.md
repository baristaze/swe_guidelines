# ADR 0034: A task is due on a date, reminded at nine in its person's morning

**Status**: accepted (2026-09-23). The contract half is superseded
(2026-09-25) by
[ADR 0038](0038-a-dead-column-leaves-the-mapping-before-the-table.md):
`remind_at` is off the wire and out of the mapping, nothing writes it,
and the release after drops the column. The drop is done (2026-09-25):
migration 202609290000 takes it out of the table.

## Context

A task carried a due time, `remind_at`, a timestamp with an offset. People
think of a to-do as due on a day, not at a minute, and asking for a time
made the quick add a form of two fields. A task now carries `due_on`, a
date and never a time, and the quick add is one text box: a title and
Enter. The date is set by editing the task.

A date has no hour, and the reminder needs one. The worker fires one
reminder per due date, in one conditional write, as before. Two questions
follow: at what moment does a date's reminder go out, and how does a
column that every task already has become another without breaking the
release that still reads it?

## Decision

**Nine in the morning, in the time zone of the person the task is for.**
That person is the assignee, or the creator when the task is unassigned
(`tasks.rules.reminder_person`). Nine is early enough to plan the day
around, and late enough not to arrive in the night. The rule is one pure
function, `tasks.rules.reminder_time(due_on, zone)`. A zone that is
unknown, or missing, means UTC.

**The time zone is the person's, recorded from the portal.** An
identity carries `time_zone`, an IANA name (`Europe/Istanbul`). The
portal reads the browser's own
(`Intl.DateTimeFormat().resolvedOptions().timeZone`) once a person is
signed in, and sends it with `PATCH /v1/me/identity` when it differs
from the stored one. So a person who travels has their zone moved the
next time they open the portal. The zone lives on the identity, not the
user, because a person has one morning in every org they are in.
`tenancy.rules.check_time_zone` refuses anything that is not a name the
process knows, an offset like `+03:00` among them: an offset has no
daylight saving, and the reminder would drift by an hour for half the
year.

**The zone is read when the reminder runs, not when the date is set.**
The write that sets a date lands the work row as before, available from
`earliest_reminder_time`: nine in the morning at UTC+14, the first
morning of that date anywhere. When the item runs, the handler asks
`get_due_reminder` for the task's date and the moment in its person's
zone, and parks until then (`WorkParked`, which spends no attempt). A
task handed to someone in another zone, or a person whose zone moved,
is still met on their morning. A reassignment lands a second row, so a
reminder already parked for the old person's later morning does not
hold back the new person's earlier one. Two rows for one date are
harmless: `fire_reminder` is one write conditioned on the task being
open, living, still due on the date read, and not yet reminded, so one
of them lands and the other writes nothing.

A simpler rule was on the table: nine in the morning UTC for everyone.
It needs no zone at all. It also reminds a person in Tokyo at six in the
evening and one in Honolulu at eleven at night, the day before. A
reminder that comes at the wrong end of the day is not a reminder, so
the zone is worth its one column.

**The column moves by expand and contract.** This release adds
`tasks.due_on` and `identities.time_zone`, and backfills `due_on` with
the UTC date of every task's `remind_at`, under the fence and counted,
as a data migration runs. UTC, because no person's zone is known before
this release records it. From this release on, no read names
`remind_at`: the table maps it deferred. The Postgres storage still
writes it on every create and update, nine in the morning UTC of the
due date, because the fast rollback runs the release before against
this schema, and that release reads only `remind_at`. The backfill's
downgrade gives every task whose `remind_at` falls on another day the
same value, so a downgrade leaves the release before a due time on each
due date.

**The wire keeps `remind_at` for one release.** `TaskView` gains `due_on`
and carries `remind_at` as `null`, marked deprecated. `AddTaskRequest`
and `UpdateTaskRequest` take `due_on`, and still take `remind_at`, marked
deprecated: its date, in its own offset, is the due date; `due_on` wins
when both are sent, and an explicit `null` of either clears it. So a
portal tab or a CLI of the release before keeps working while this one
rolls out. The CLI takes `--due 2026-10-01` and `--no-due`, and refuses
a time.

## Consequences

The release after this one drops `tasks.remind_at` and its mapping, the
storage's write of it, and `remind_at` from the three wire types. That
is the contract half, and it is a release of its own.

During a rollout, a task the release before writes gets a `remind_at`
and no `due_on`, and this release does not see it as due. The window is
the minutes both releases serve. After a fast rollback to the release
before, a due date set in this release shows as a due time at nine UTC,
and its reminder item, available from the earlier moment, finds the
time it carries is not the task's and completes without a word. Saving
the task's due time again in the release before schedules it again.

Every reminder item is claimed at least once before its person's
morning and parked. That is one extra claim per reminder, which a
reminder a week out already paid in the queue's wait.

A person who never opens the portal has no zone, and is reminded at
nine UTC. The CLI does not send one.
