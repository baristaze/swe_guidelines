# ADR 0039: Long-running work is a record; a guard parks it, a bound fails it

**Status**: accepted (2026-09-25)

## Context

Two features take minutes, not a request: importing tasks from a CSV
file, and archiving the done tasks nobody changed for ninety days. The
guideline's "Long-Running Orchestrations" says what such work is: "a
durable record advanced by stateless workers", "a row with a status and
a cursor", claimed through a work item that is "a separate row from the
record it advances". It names three outcomes: "It succeeds, it fails, or
it parks", and the rule between the last two: "A guard parks, a bound
fails. A safety check leaves the work resumable; only a real limit
terminates it." "Maintenance Without a Scheduler" adds that the sweep
"open[s] the next period of a record kept per period".

Acme had the work queue and its fences, and no such record.

## Decision

**One mechanism, a namespace of its own.** `om/orchestrations` holds the
record (`Orchestration`: a kind, its input, a status, a cursor, a total,
what the steps applied and skipped, the first twenty skipped rows with a
reason, a park reason, a fail reason, and a version), its storage in
Postgres and in memory, its migration under the tenant fence, and its
manager: start, get, the org's newest, a person's resume, the event's
wake, a failure, and the purge. Both features are kinds of it,
`task_import` and `task_cleanup`. What one step of a kind does belongs
to the namespace whose rows it changes: the tasks manager steps both.

**A step is one commit.** A step's effect (the tasks an import creates,
the tasks a cleanup archives), the record's next cursor, and the work row
that asks for the next step land in one transaction. The record rides
it as a companion statement (`Step`), the way an outbox row rides a core
write: the tasks storage runs the record's compare-and-set on its
version in its own transaction, and rolls back the effect with it when
the record moved. So a worker that dies leaves the record at its last
committed step, and a stale worker's step (one that held an item past
its lease, which the guideline's fence table leaves to the record) lands
nothing. The record's count of what was applied grows in that commit by
the rows the effect wrote, not by the rows it was given.

**A step runs twice and does once.** An imported row's task takes an id
derived from the import and the row number (`base.derived_id`, as ADR
0027 derives a Slack delivery's), and the insert skips an id already
written. The cleanup's write is conditional: it archives only a task
still done, not deleted, not archived, and unchanged since the cutoff,
so a task reopened or edited between the read and the write is left
alone, and a second run archives nothing more.

**A guard parks: the plan's active tasks.** When the next row of an
import would take the org past its plan's bound, the step creates the
rows before it, and parks the record at that row with the reason
`plan_limit`, in the same commit. Everything it made stays. The bound is
a lever an owner lifts, so it is the guideline's "a quota an operator
can raise", never a failure. Two things wake it. A plan that rises: every
billing account write that lifts the org's plan (a payment's delivery,
the seed's grant, an operator's grant) lands a `WAKE_PARKED` work row in
the account's own commit, and its handler resumes the org's records
parked for `plan_limit`, a couple of seconds apart. Or a person, with
Resume (`POST /v1/tasks/imports/{id}/resume`), after finishing some
tasks. A woken record is not trusted to have room: its next step reads
the plan and the count again, and parks again at the same row when the
room is still not there. The park is the record's status, not a deferred
work item: nothing should come back on a timer, and only the record can
say to a person why it waits.

**A bound fails.** The import's input has real limits: a file over one
megabyte, over five thousand rows, not text a CSV reader reads, or with
no `title` column, and a file removed before it was read. Each fails the
record at once with its reason, and nothing more is created. A row that
is bad on its own (no title, a date that is not a date, an assignee who
is not a member) is not a bound of the file: it is skipped and named.

**Everything else transient is the work queue's retry.** A database or
an object store that does not answer makes the step raise. The worker
loop already answers that: the item is failed, which requeues it with
its growing delay until its attempts are spent. The retry starts at the
cursor the last commit left. On the item's last attempt the handler
fails the record as `defect` instead of raising, so no record waits on
work that will never come. There is no second park, no busy check, and
no timeout of this feature's own: the storage layer's statement
deadline and checkout bound already hold every batch, and a batch is
small (a hundred rows, five hundred tasks), each one conditional write.
The cleanup has no guard of its own, so it never parks: it succeeds, or
a defect fails it.

**The day's cleanup is a unique key, not a clock.** Every sweep (every
thirty seconds on every worker) asks each org whether it has a done task
unchanged for the archive age and, if so, opens its cleanup record for
the UTC date. The org, the kind, and the date are the record's unique
key, and its id is derived from them, so the first sweep of the day
opens it and every later insert is a no-op. No scheduler, no leader, no
lock. The cutoff is fixed when the record opens, so every step of one
day asks the same question; a task that crosses the age during the day
waits for tomorrow.

**Archived is a column, not a status.** A task keeps `status` done and
gains `archived_at`. It leaves the done list and every list and count
shown by default; it never was an active task, so the plan's count does
not move. It is read by its id, listed at `GET /v1/tasks/archived`, and
restored with `POST /v1/tasks/{id}/restore`, naming the version read:
restoring is one compare-and-set, so it is in the API. Reopening an
archived task restores it too. Its attachments stay, rows and objects;
archiving deletes nothing. The clock is the task's last change: a done
task someone edits keeps its place for another ninety days, and so does
a restored one. The age is the worker's setting
(`ACME_TASKS_ARCHIVE_AFTER_DAYS`, 90), illustrative like the plans'
numbers.

**Progress is pushed.** Every write of a record lands
`orchestrations.orchestration.<created|updated>`, ids only, so the
portal and the CLI follow an import on the realtime channel and read it
on each push. Nothing polls. Each imported task lands its own
`tasks.task.created`, as a created task does; an imported task posts
nothing to Slack, since a file of a thousand rows is not a thousand
messages.

## Consequences

The mechanism is the next long job's: a kind, its input shape, a step in
the namespace that owns its rows, and, where it has a guard, a park
reason and the event that clears it.

One org's large import takes one step at a time, a work item each, so it
shares the worker's slots with every other org's work. If bulk work ever
starves its neighbours, the guideline's answer is the lane on the work
queue: a lane of its own for the orchestration kind, served by workers
of its own. It is not built now.

Operators read the records by their outcomes: every transition counts
under the subsystem `orchestrations`, labelled by its kind and state, and
a defect is an `ERROR` line naming the record and its org
(`ops-investigate`).

A record that settled is purged thirty days later; a parked or running
one is kept. A record whose last attempt could not even write its
failure (the database down for the whole of its retries) stays running
until its org is purged; the work queue's dead letter names it.

The numbers are illustrative: the file's megabyte and five thousand
rows, the batches of a hundred and five hundred, the twenty rows named,
the ninety days.
