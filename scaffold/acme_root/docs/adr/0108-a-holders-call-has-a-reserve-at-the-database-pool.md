# ADR 0108: A holder's call has a reserve at the database pool

**Status**: accepted (2026-10-22)

## Context

A holder's call has a lane of its own at admission and a budget of its
own per credential
([ADR 0106](0106-a-holders-calls-are-counted-apart-from-its-writes.md)).
Admitted, it still needs a database connection, and every request
draws on the same pools: 12 connections each, under a checkout bound
of 5 seconds, past which the call answers 503.

When the database slows, the connections stay checked out longer, and
up to 200 writes and 400 reads wait on those 12. A renewal joins the
back of that wait. When the wait does not drain inside the bound, the
renewal answers 503, and the holder loses its lease and the work it
holds, just as a refusal at admission would cost it.

## Decision

**Each pool has a reserve.** Beside each pool, the storage root opens a
second engine of `ACME_DATABASE_POOL_RESERVE` connections, 2 by
default, under the pool's own checkout bound and statement deadline
(`impl/postgres.py`). A role overrides it the way it overrides the
pool's size (`ACME_DATABASE_POOL_RESERVE_CORE`), and zero is no
reserve. The reserve is a pool of its own, so no checkout made for
another call can reach it.

**A holder's call takes the pool first, and the reserve only when the
pool is full.** Before a holder's call checks out, the funnel reads its
pool. A pool with a connection to give, idle or not yet opened, serves
the call as it serves any other. A pool with none sends the call to the
reserve (`pg_base.Reserve`). The read and the checkout run with no
await between them, so nothing takes the connection the read saw. Every
other call waits on the pool alone, under its bound.

**The mark rides the call to the funnel.** Admission runs a request it
counts in the holds lane inside `as_holder_call()`
(`acme.om.storage.reserve`), and the funnel reads `in_holder_call()`
for each statement. So one mark on the route picks the lane, the
budget, and the reserve. The mark reaches every statement the call
runs: its authentication, which reads under the system login, its
reads and its write, under the runtime login, and a copy's heartbeat
or result on whatever role it writes.

The mark is a context variable, the one beside the request id's
(CTX-07). A deadline rides the context as a field, which a manager
hands to each call that takes one
([ADR 0069](0069-a-request-has-a-deadline-its-provider-calls-share.md)),
because it decides: a call with no time left does not start. The mark
decides nothing a call returns. It only picks which connections serve
it, and every storage method would carry it as an argument, so a copy's
route would get the reserve only where each of its storage calls passed
it on. Admission alone sets it, and resets it when the call ends; the
funnel alone reads it. A test holds both.

**Only the API opens a reserve.** A reserve opens a connection only
when a holder's call needs one, and only admission marks one. The
connection budget counts the reserve for the API's replicas
(`deployment/cloud/README.md`): 1 at XS and 2 from S up.

## Consequences

A slow database that fills the pool still serves a renewal, from the
reserve, under the same checkout bound. A reserve the holder's calls
fill themselves answers the next one 503 past the bound, as a full
pool does, and the holds lane's bound caps how many wait on it.

A burst of writes never reaches the reserve, however long it waits.

A call that runs outside a request, a worker's own renewal among them,
carries no mark and is served from its process's pool.

The readiness probe reads the pools and not their reserves: it is no
holder's call.

A relay that runs after a holder's call is answered still carries the
mark, so its writes may take the reserve when the pool is full.
