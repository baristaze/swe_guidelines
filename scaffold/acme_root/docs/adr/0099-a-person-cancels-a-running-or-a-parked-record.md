# ADR 0099: A person cancels a running or a parked record

**Status**: accepted (2026-10-12)

## Context

A parked record waits for the event that clears its reason, the time
its park named, a sweep, or a person's resume
([ADR 0039](0039-long-running-work-is-a-record-a-guard-parks-and-a-bound-fails.md)).
Each of those runs it again. A person who no longer wants the work has
no way to end it: the record stays parked until something wakes it, and
then it runs work no one asked for any more. The guideline's
"Long-Running Orchestrations" gives a parked record a way out that is
no wake, and says who may take it. This records how Acme holds it.

## Decision

**A cancel is the manager's.** `cancel(ctx, record_id, reason)` ends a
running or a parked record. It takes `Permission.WRITE`, the permission
`resume` takes, so whoever may run a record again may end it. A record
already cancelled is answered as it is. One that succeeded or failed is
refused (`ValidationFailed`), since nothing is left to end.

**It settles `cancelled`.** `OrchestrationStatus.CANCELLED` joins
`SUCCEEDED` and `FAILED` in `SETTLED`. It is no failure with a reason,
since only a bound fails, and an operator reads the two apart. The
record keeps its cursor, its total, and what its steps applied and
skipped, as a failure does. Its park reason goes, and `finished_at` and
`updated_by` say when and who. The reason a person gives is logged with
the record, on one line and bounded as a failure's detail. The record
does not store it.

**The write is the record's compare-and-set.** The cancel names the
version it read, as every write of a record does. A step already under
way read an earlier version, so its write lands nothing and rolls back
its effect with it. The next step's work item finds the record no
longer running and completes with nothing to do.

**Nothing wakes it.** A wake reads only parked records, and `resume`
refuses a settled one. A request the record waits on in a lease's line
no longer waits: the waiter's `still_waits` reads the record settled,
and the next grant passes the request as `waiter_gone`
([ADR 0086](0086-a-scarce-resource-is-leased-under-a-fencing-token.md)).

**A client learns of it through the record's own push.** The cancel
lands `orchestrations.orchestration.updated` with the version it wrote
([ADR 0061](0061-a-push-names-the-version-it-wrote.md)), and no work
row. A client that reads the record at that version reads `cancelled`.

**The retention takes it.** The purge of settled records reads
`SETTLED`, so a cancelled record goes thirty days after its cancel.
The status column holds any status the code writes, so nothing
migrates.

**A kind a person starts says what a second start does.** While one of
its records is open, a second start answers the open record, is
refused, or starts beside it. `noop` starts beside it. A kind kept per
period answers the open record through its unique key.

## Consequences

A product's route for a person's cancel calls `cancel`, and maps the
refusal of a finished record to its error. Where the record may wait in
a lease's line, the route then makes it leave every line
(`LeasesManagerInterface.leave`), since the leases depend on the
orchestrations and not the other way. Without that call, the request
stays in its line until a grant passes it or its wait ends, and the
line's estimates count it until then.

Every transition still counts under `orchestrations`, and a cancel
counts as `<kind>_cancelled`.
