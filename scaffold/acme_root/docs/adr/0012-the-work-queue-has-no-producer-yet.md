# ADR 0012: A write that also starts work is built and tested; no write in Acme starts work yet

**Status**: closed (2026-09-22). Reminders and Slack posts are the first
producers; see Closed below. The seat count of a Max subscription rides
the same path ([ADR 0031](0031-plans-are-levers-and-the-processor-is-mirrored.md)):
adding or removing a member lands `work.SYNC_SEATS` beside the change.
The record stays for the interval it covers.

## Context

The Work Queue and Database Roles describe a pattern at length: a work
item that follows a core write is not enqueued by the manager that made
the write. The queue is a database role of its own and no statement
reaches both rows, so the item rides a second outbox row of that write,
of kind `work.<kind>`, landed by the same statement; the relay
dispatches on the row's kind and enqueues it, taking `(org_id, row)`
and no context and reading the actor off the row. The relayed enqueue
presents the outbox row's id as the item's `idempotency_key`, which is
the same on every run of the relay, so a relay that runs twice meets
the row already there. Lenses `STO-20` and `ASY-25` rate it.

Acme now implements all of it: the storage bases and the entity
storages take `outbox_rows: tuple[OutboxRow, ...]` and land them in the
one statement, `OutboxRelayImpl._deliver` branches on `work.<kind>` to
`WorkManagerInterface.enqueue_relayed(org_id, row)`, and the direct
create under a context and the relayed enqueue share one insert under
one key. An end-to-end test over the real root lands two rows in one
statement, relays them, and relays the work row a second time to show
one item and one wake.

What is missing is a producer. Acme is a to-do list. Its core writes
are create, update, move, delete and a purge; the only heavy one is the
renumbering of the open list, which cannot be deferred, because a move
has no position until the list is renumbered and the request cannot
answer before it. There are no webhooks, exports, imports, digests or
notifications in this product, and the only `WorkKind` is `NOOP`, the
placeholder the maintenance worker keeps. A create that enqueued a
`NOOP` would be junk in the queue, and reminders or assignee
notifications would be a feature invented to exercise a pattern.

## Decision

The path stays built, wired and tested with no production producer.
The first domain work kind rides it rather than getting a path of its
own: it declares its `WorkKind`, its manager lands the second outbox
row of kind `work.<kind>` in the same tuple as the entity's row, and
the relay it already has does the rest. The deviation ends there.

A work item whose `idempotency_key` is already held by **another**
tenant stays a `Conflict`. The reported duplicate the guideline
describes is the same tenant's retry meeting its own row; the unique
index on the key is global, and no retry explains a collision across
tenants, so `UniqueKeyTaken` names it rather than a report that would
hand back a row the caller may not read.

## Consequences

A review that looks for a caller of `enqueue_relayed` outside the tests
finds none and cites this record; the lenses that rate the pattern read
against the code that implements it, which is there. What Acme gives
up is the demonstration that a real feature uses the path, and the risk
that carries is a path exercised only by its own test: the end-to-end
test is therefore written over the real root and the real relay, not
over doubles, and the second relay run is part of it, so the property
that matters is held by something other than a caller's good manners.
The first feature that needs asynchronous follow-up work is the trigger
to delete this record, not to add a second enqueue path.

## Closed

The first feature with follow-up work rode the path, as decided, and
added none of its own. A task's due time schedules a `TASK_REMINDER`
item, and a task created or completed in an org with a Slack channel
asks for a `SLACK_POST` item, each as a second outbox row of the task's
own write, relayed by the relay that was already there. The reminder
itself lands a third kind of row the same way. A scheduled kind waits
in the queue until its time: its payload names `not_before`, and the
relayed enqueue makes the item available then. A work-item key held by
another tenant stays a conflict.
