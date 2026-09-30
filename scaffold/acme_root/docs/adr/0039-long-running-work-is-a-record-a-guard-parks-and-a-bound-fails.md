# ADR 0039: Long-running work is a record; a guard parks it, a bound fails it

**Status**: accepted (2026-09-28)

## Context

Some work takes minutes, not a request. The guideline's "Long-Running
Orchestrations" says what such work is: "a durable record advanced by
stateless workers", "a row with a status and a cursor", claimed through
a work item that is "a separate row from the record it advances". It
names three outcomes, "It succeeds, it fails, or it parks", and the
rule between the last two: "A guard parks, a bound fails. A safety
check leaves the work resumable; only a real limit terminates it."
"Maintenance Without a Scheduler" adds that the sweep "open[s] the
next period of a record kept per period".

## Decision

**One mechanism, a namespace of its own.**
`om/src/acme/om/orchestrations` holds the record (`Orchestration`: a
kind, its input, a period, a status, a cursor, a total, what the steps
applied and skipped, the first twenty
skipped rows with a reason, a park reason, a fail reason, and a
version), its storage in Postgres and in memory under the tenant fence,
and its manager: `start`, `get`, `get_recent`, `resume`, `wake`,
`fail`, and the purges. The one kind is `noop`, the mechanism's own: it
steps through its input's count and changes nothing else. A product
replaces it with its kinds. Each declares an input shape in
`ORCHESTRATION_INPUTS` and a step in the namespace whose rows it
changes.

**A step is one commit.** A step's effect, the record's next cursor,
and the work row that asks for the next step land in one transaction.
The record rides it as a companion statement (`Step`), the way an
outbox row rides a core write: the namespace's storage runs the
record's compare-and-set on its version in its own transaction, and
rolls back the effect with it when the record moved. So a worker that
dies leaves the record at its last committed step. A stale worker's
step, one that held an item past its lease, lands nothing. The count of
what was applied grows in that commit by the rows the effect wrote.

**A step runs twice and does once.** What a step makes takes an id
derived from the record and the row (`base.derived_id`), so a second
run meets its rows already there.

**A guard parks, a bound fails.** A limit that can clear parks the
record at its cursor, in the step's own commit, and keeps what it made.
The one park reason is `provider_unavailable`: a provider a step calls
did not answer. The work item that clears a reason is `WAKE_PARKED`,
which resumes the org's records parked for it, two seconds apart. A
person may resume a parked record too (`resume`). A woken record is not
trusted: its next step asks its guard again. A bound of the input fails
the record with its reason, which a product adds beside `defect`. The
park is the record's status, not a
deferred work item, so only the record says why it waits.

**Everything else transient is the work queue's retry.** A database or
a store that does not answer makes the step raise, and the queue
retries the item with its growing delay, from the cursor the last
commit left. On the item's last attempt the handler fails the record as
`defect` instead of raising, so no record waits on work that will never
come. The storage layer's statement deadline bounds every batch.

**A record kept per period has a unique key.** The org, the kind, and
the period are unique (`uq_orchestrations_org_id_kind_period`), so the
first start in a period opens its record and every later start answers
it as stored. A sweep can open the period's record on every pass with
no scheduler, no leader, and no lock.

**Progress is pushed.** Every write of a record lands
`orchestrations.orchestration.<created|updated>`, with the record's id
and the version the write left
([ADR 0061](0061-a-push-names-the-version-it-wrote.md)). A client
follows a record on the realtime channel and reads it on each push it
does not already hold. Nothing polls.

## Consequences

The next long job is a kind, its input shape, a step in the namespace
that owns its rows, and, where it has a guard, a park reason and the
event that clears it.

One org's large job takes one step at a time, a work item each, so it
shares the worker's slots with every other org's work. A lane of its
own for a heavy kind is the guideline's answer when bulk work starves
its neighbours.

Every transition counts under the subsystem `orchestrations`, labelled
by kind and outcome, and a `defect` is an `ERROR` line naming the
record and its org.

A settled record is purged thirty days later; a parked or running one
is kept. A record whose last attempt could not write its failure stays
running until its org is purged, and the work queue's dead letter
names it.
