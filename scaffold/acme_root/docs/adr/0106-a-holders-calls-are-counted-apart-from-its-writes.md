# ADR 0106: A holder's calls are counted apart from its writes

**Status**: accepted (2026-10-22)

## Context

A credential has one budget of writes, in a fixed window that starts at
its first write: 1,200 a minute by default
([ADR 0059](0059-authenticated-routes-have-limits.md)). A process admits
200 writes in flight at once. A holder that streams its output spends
both on writes whose loss costs little. One push every quarter second
per item it streams is 240 writes a minute, so five such items spend
the budget. From then until the window ends, every renewal it sends is
refused. A holder that cannot wait out the refusal inside its lease
loses the lease, and the work it holds is revoked mid-run.

When the database slows, the write lane fills, and admission sheds a
renewal like any other write. The slowdown becomes a wave of lost
leases, and every lost lease is work to start again.

The guideline counts a holder's calls apart: in a lane of their own at
admission and a budget of their own per credential (NET-37).

## Decision

**A route marks itself a holder's call.** It carries `HOLDER_CALL` in
its dependencies (`gateway/admission.py`). The mark does nothing when
the route runs. The app reads it where it is built: `HolderCalls.of`
takes every route that carries it, with the methods and the path
pattern FastAPI routes it by. The lease's renewal and its release are
the scaffold's holder's calls (`routers/leases.py`). A copy marks its
own routes the same way, a heartbeat or a result, and edits no
middleware.

**Admission has a third lane.** Routing has not run where admission
counts, so it matches a request against the marked routes by method
and path, with each route's own pattern. A match is counted in the
holds lane, and anything else in the read or the write lane by its
method. The holds lane refuses past its own bound with the same 503
and `Retry-After`, and its message names it: `the process is at its
bound of holds in flight`. The default is 50
(`ACME_ADMISSION_LIMIT_HOLDS`), a quarter of the writes, since a
renewal is one short write and holders are few beside writers. A
deployed environment sets it to four per connection of the pool, as it
sets the other two lanes from the pool.

**The credential spends the lane's budget.** Admission puts the name of
the lane it counted the request in on the scope, and the credential's
budget is the one of that name: reads, writes, or holds. One decision
picks both. The holds budget is 600 a minute per credential
(`ACME_CREDENTIAL_RATE_LIMIT_HOLDS`), over the same window as the
others. A holder renews at half of what is left on its lease, and at
most once a second, so ten leases renewed at that pace spend it all,
and a hundred on the default term of a minute spend a third of it.

## Consequences

A holder whose writes are spent still renews, and a renewal past its
own budget answers 429 and spends no write. A full write lane still
admits a renewal and a release.

A client that renews too often is refused by its own budget, and a
flood of renewals by the holds lane's bound. Neither takes anything
from the writes.

A route marked by mistake gets a cheaper lane than it should. The mark
is on the route, where a review reads it, and NET-37 names a bulk write
marked a holder's call as a breach.

A route added after the app is built is not read for the mark. Every
router a copy mounts goes through `create_app`, which reads the routes
once they are mounted.
