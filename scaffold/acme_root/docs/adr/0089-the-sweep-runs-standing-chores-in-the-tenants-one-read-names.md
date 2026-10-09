# ADR 0089: The sweep runs standing chores in the tenants one read names

**Status**: accepted (2026-10-08)

## Context

"Maintenance Without a Scheduler" gives the sweep a duty beside its
purges: open the next period of a record kept per period. Such a record
is an orchestration started with its `period`, and the org, the kind,
and the period are its key
([ADR 0039](0039-long-running-work-is-a-record-a-guard-parks-and-a-bound-fails.md)).
Opening one is a write in one tenant, under that tenant's context. It is
not a statement across tenants, as a purge past its retention is
([ADR 0045](0045-retention-purges-run-once-a-pass-across-tenants.md)).

A duty per tenant that asks every tenant on every pass whether it is due
costs each living tenant a transaction a pass, and almost every answer
is no. That cost grows with every tenant, as the purges per tenant did
before ADR 0045.

## Decision

**A chore is a step per tenant.** The loop takes standing chores by
name (`ChoreStep`). Each takes one tenant's service context, the one
the pass listed for it, and is idempotent. The requeue, the relay, the
purges past retention, and the leases' sweep are steps across tenants
(`AcrossStep`): one call in the system scope, called again while its
batch comes back whole. The leases' sweep finds the orgs with something
due and visits each inside its own call; a chore is run by the loop.

**One read a pass names the tenants with a chore due.** The loop takes
it beside the chores (`ChoreTenants`). It is a named read across tenants
in the system scope, under the pass's request stage, and it returns live
tenants in id order, after a cursor, at most `chore_batch` (1,000). It
is one read for every chore, never a read per chore, so a tenant it does
not name costs the pass nothing. The loop refuses chores without it.

**The chores run before the ring, a page a pass.** Each tenant the read
names runs every chore once. Past the budget the pass takes no new
tenant, but always takes one. The next pass reads on after the last
tenant this one ran, and after a page that came back short and ran
whole, from the first. So every tenant due is reached in turn, however
many are due, and one whose chore keeps failing holds no other back. A
chore that fails is logged and stops no other step. A tenant the read
names with no context in the pass, made since the list was read or
purged, is passed over until the read names it again. A pass whose list
of tenants failed runs no chore and moves no cursor. The chores come
before the ring because a period opens when it begins, and a deleted
tenant's purge loses nothing by waiting.

**The scaffold wires no chore.** It keeps no record per period: its one
kind, `noop`, is started by a person. A copy that keeps one wires two
things in `build_loop`. One is the chore, which starts the period's
record; a start in a period already open answers the record as stored,
so a chore that runs twice opens one record. The other is the read, in
the namespace whose rows say a chore is due, listed with the methods
that take no tenant. Its index holds only the rows it may find, so it
answers without a probe per tenant.

## Consequences

A copy with no chore pays nothing for them. A copy with chores pays one
read a pass, and the chores of each tenant due, once.

The pass's line counts the tenants whose chores ran (`chores`), beside
the tenants of the ring.

A burst of tenants due, as when a day begins, drains at a page a pass at
most, and at one tenant a pass when the budget is spent before the
chores start.
