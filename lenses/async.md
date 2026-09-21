# Async

Group id: `async`. Covers Infrastructure, The Network Layer
(Idempotency on the Consumer Side, Long-Running Orchestrations), and
Worker Roles of `architecture.md`.

This group judges everything that happens off the request path: the
infrastructure capabilities managers lean on, how work is handed off
and picked up, how a worker behaves over its life, and what a
long-running record does when it cannot continue. It owns the sweep's
duties (requeue expired leases, resume parked records, relay what a
crash left in the outbox, purge done outbox rows and soft-deleted rows
past retention) and the whole work queue, its table and statements
included. It leaves the `org_id` and `EMPTY_UUID` keying rules and the
provenance of a worker's context to `context`, database roles, the
retention periods, and the life of an outbox row to `storage`, and the
realtime edge with its wait-versus-notify patterns to `network`.

## ASY-01 Infrastructure is never reached through ambient state

**Principle.** Every infra capability is fronted by an interface with
swappable impls, and a handle to one arrives only through a
constructor at boot, never through a global, a thread local, a
module-level client, or a context, whichever stage or scope it is.

**Source.** Infrastructure, Infrastructure Principles.

**Look for.** Any module-level cache, bucket, topic, queue, or secret
client; any attribute on the context that hands out an infra handle;
any impl constructed inside a manager or handler body.

**Violation.** A manager imports a concrete cache or bucket client and
builds it itself; a global `topics` object is reached from inside a
handler; an infra handle is read off `ctx`.

**Severity.** medium

**Check.** `arch-check` decides module-level clients and infra impls
built in a manager; the rest is judged.

## ASY-02 One infra root with a lifecycle

**Principle.** Infrastructure is fronted by a single root with one
getter per capability and `start()` / `close()`. The app container
chooses the impl behind each getter from settings, opens the root at
boot, and closes it at shutdown.

**Source.** Infrastructure, InfraInterface Root.

**Look for.** The infra root class and its getters; the container's
start and close order; where each impl is selected.

**Violation.** A capability that can only be obtained by constructing
its impl directly; a topic listener or queue client opened lazily on
first use rather than in `start()`; a process that exits without
closing the root; backend selection scattered outside the settings
object.

**Severity.** medium

**Check.** `arch-check` decides the root's getters, `start`, `close`,
and which modules import an impl; the rest is judged.

## ASY-03 Every impl describes itself

**Principle.** Every infra impl can `describe()` itself in one line
naming the backend it talks to, which is what the boot inventory line
(DEL-06) is built from.

**Source.** Infrastructure, Infrastructure Principles.

**Look for.** A `describe()` on each impl and what it names; whether
the inventory line the container logs reads it.

**Violation.** An impl with no description, or one that names the
interface instead of the backend behind it, so the inventory line
cannot say whether the process is on the local or the cloud impl.

**Severity.** low

**Check.** `arch-check` decides that every infra impl defines or
inherits `describe()`; the rest is judged.

## ASY-04 Caches are scoped and injected already scoped

**Principle.** A cache is scoped by a fixed `CacheScope` enum so
unrelated consumers do not step on each other's keys, and a manager
receives its cache already scoped at wire-up time.

**Source.** Infrastructure, Cache.

**Look for.** The `CacheScope` enum; manager constructors taking
`CacheInterface`; the wire-up site that calls `get_cache(scope)`.

**Violation.** A manager calls `get_cache` with a scope it picks at
call time; two consumers share one scope and collide on key names; a
scope added as a free string instead of an enum member.

**Severity.** medium

**Check.** `arch-check` decides the `CacheScope` enum, a free string
passed to `get_cache`, and a manager calling it; the rest is judged.

## ASY-05 A read cache is a projection with a generation

**Principle.** A cached read carries the tenant's generation number in
its key, and a write bumps that number with one atomic `increment`, so
every older entry is orphaned at once. The TTL is a backstop, never the
primary invalidation.

**Source.** Infrastructure, Cache.

**Look for.** How a manager builds cache keys; what a write path does
to the cache; use of `increment` versus `invalidate` on a write.

**Violation.** A write that tries to enumerate and delete a tenant's
keys; a write that leaves the cache to expire by TTL alone; a key with
no generation on a value that any write can change; `increment` used
for anything other than rate limits and generations.

**Severity.** medium

## ASY-06 A cache fails open and holds nothing that must be correct

**Principle.** A miss is always an acceptable answer, and a backend
that cannot be reached is a miss, not an error. Nothing that must be
correct is kept only in a cache.

**Source.** Infrastructure, Cache.

**Look for.** Error handling in the cache impls and at cache call
sites; any value that exists only in the cache.

**Violation.** A request fails because the cache backend is down; a
manager raises on a cache miss; a lock or a record whose only copy is
a cache entry, where the rate-limit counter, the generation, and the
liveness beat are cache-only by design.

**Severity.** high

## ASY-07 Caching is a manager decision

**Principle.** The storage layer does not wrap reads in a cache; a
storage impl talks to its database and nothing else. Caching decisions
live in managers, where the cost of a stale read is understood.

**Source.** Infrastructure, Cache.

**Look for.** Storage impl constructors and read methods; where cache
calls appear.

**Violation.** A storage impl takes a `CacheInterface`; a storage read
consults a cache before the database; a decorator around a storage
impl adds caching.

**Severity.** medium

**Check.** `arch-check` decides cache imports, names, and parameters in
the storage layer; the rest is judged.

## ASY-08 Buckets carry blobs, keyed by a fixed enum, moved by presigned URLs

**Principle.** Large blobs live in buckets named by an enum, laid out
under keys the manager chooses, with presigned URLs for direct upload
and download so the service never proxies a large transfer through its
own memory. A local filesystem impl with the same layout serves
development and tests.

**Source.** Infrastructure, Buckets.

**Look for.** The `Buckets` enum; the bucket interface; how uploads
and downloads reach clients; the local impl.

**Violation.** A large blob (a document, an upload, an export) stored
in a column or on a service's disk; a bucket name passed as a free
string; a route that streams a large upload through the process
instead of handing out a presigned URL; a local setup that needs the
cloud object store to run tests.

**Severity.** medium

**Check.** `arch-check` decides the `Buckets` enum, the bucket
parameters, and the local impl; the rest is judged.

## ASY-09 Topics are a fixed enum with a typed payload map

**Principle.** Topic names are fixed by enum, payload types are fixed
by a payload map, and every payload extends `TopicPayload`, a frozen
base infra declares that ignores unknown fields, with a producer-set
`idempotency_key` and `produced_at`. `publish` returns `None`;
`subscribe` returns an unsubscribe callable.

**Source.** Infrastructure, Topics.

**Look for.** The `Topics` enum, `TOPIC_PAYLOADS`, the payload base
class, the signatures of `publish` and `subscribe`.

**Violation.** A topic published by string name; a payload class that
does not extend the base or lacks the key; a payload base that extends
the OM root or forbids unknown fields, so an old consumer rejects a new
producer's payload mid-rollout; a `publish` that returns a
broker-assigned id; a subscription with no way to unsubscribe; a
consumer name missing from `subscribe`.

**Severity.** medium

**Check.** `arch-check` decides the enum, the payload map, the payload
base, and the `publish` and `subscribe` signatures; the rest is judged.

## ASY-10 Durable work never rides a topic

**Principle.** A topic is best effort: a published event reaches the
processes subscribed at the time, at most once, and a bus hiccup may
lose it. Durable work is a row in the work queue, and a missed
notification degrades to polling latency, never to lost work. A bus
backed by the database connects to the queue role.

**Source.** Infrastructure, Topics; The Storage Layer, Database Roles.

**Look for.** What each topic handler does with a message; whether any
handler is the only path by which some work gets done; which database
URL the database-backed topic impl opens.

**Violation.** A handler that performs the work itself with no backing
row; a producer that publishes a task and writes nothing durable; a
consumer that assumes it will see every message ever published; a
database-backed bus pointed at a role other than the queue role.

**Severity.** high

## ASY-11 A capped bus trims the payload and the consumer re-reads

**Principle.** When a bus caps payload size, the impl trims a payload
that would not fit, marks it `truncated`, and the consumer re-reads the
record from storage. Consumers written that way work unchanged on a
bus with no cap.

**Source.** Infrastructure, Topics.

**Look for.** The database-backed topic impl; consumers of topics that
carry large payloads.

**Violation.** A publish that raises or silently drops when the
payload exceeds the cap; a consumer that trusts an inline body it
could have re-read; a consumer that breaks when `truncated` is set.

**Severity.** low

## ASY-12 Queues absorb outside producers; the consumer owns exactly-once

**Principle.** Work whose producer is outside the platform and cannot
wait goes on a queue with the shape of a hosted queue service. The
queue delivers at least once and does not deduplicate; the consumer
dedupes. Dead letters are visible: an audit entry names them and a
metric counts them.

**Source.** Infrastructure, Queues.

**Look for.** The queue interface and its impls; the consumer's dedupe
step; what happens after the last failed attempt.

**Violation.** A consumer that relies on the queue to deduplicate; a
message that fails its last attempt and disappears without an audit
entry or a metric; an in-process impl that lacks visibility timeouts or
a dead-letter path; an outside producer wired directly into a request
handler that does the whole job inline.

**Severity.** high

## ASY-13 Secrets are references in the model, values at the point of use

**Principle.** A secret store holds values; the object model holds only
references. A value is resolved for exactly one operation and
discarded. It never enters an entity, a log line, an audit payload, an
error message, or a subprocess environment. An error names the secret
and the store it was looked up in, never a value.

**Source.** Infrastructure, Secrets.

**Look for.** Entity fields that hold credentials; where `get(name)` is
called and how long the value lives; log and audit calls near secret
resolution; the text of the not-found error; subprocess environment
construction.

**Violation.** An entity with a token or password field; a secret
resolved at boot and kept on an object; a value in a log line, an
error string, or an audit payload; a not-found error that omits the
secret name or the store; a subprocess inheriting the parent's full
environment.

**Severity.** medium

## ASY-14 Every handler is idempotent on a producer-generated key

**Principle.** Delivery is at-least-once on every queue. Every message
carries a producer-generated idempotency key (or the outside system's
delivery id), the handler dedupes on it through a unique index or an
upsert before doing work, and every pipeline stage forwards it. The
key lives on the row the effect produces, or marker and effect are one
named atomic write.

**Source.** The Network Layer, Idempotency on the Consumer Side.

**Look for.** The dedupe step at the top of each handler; the unique
index or upsert on the key; whether the marker and the effect commit
together; the key on every message the handler forwards.

**Violation.** A handler that inserts without a unique key and creates
a duplicate on redelivery; a dedupe marker committed separately from
the effect it guards, so a crash between them suppresses the work for
good; a key minted by the consumer instead of the producer; a
downstream message that drops the incoming key and mints a new one; a
webhook handler that ignores the provider's delivery id.

**Severity.** high

## ASY-15 Web services do not spawn background jobs

**Principle.** Web services do not spawn background jobs or schedule
recurring tasks; every such need is a worker role with its own
container, deployment, and catalog entry. The one thing that is not a
job is a topic subscriber that only forwards events to sockets its own
process holds; anything that writes, retries, or outlives a connection
is a worker.

**Source.** Worker Roles, Workers, Not Web-Service Side Jobs.

**Look for.** Background tasks created inside a service process;
timers and schedulers in service code; in-process subscribers and what
they do.

**Violation.** A request handler that starts a task which outlives the
request; a service that runs a periodic sweep; an in-process subscriber
that writes to storage or retries deliveries.

**Severity.** high

**Check.** `arch-check` decides tasks, threads, timers, and schedulers
in service code off the socket edge; the rest is judged.

## ASY-16 Durable work is a row with the queue's shape

**Principle.** A work item names its kind and target, carries a unique
idempotency key, a `lane` routing string, a status, an `available_at`,
its claim (`claimed_by` for an operator, `claim_token` the fence,
`lease_expires_at`), and its attempts; payload shapes are fixed per
kind by `WORK_PAYLOADS`. The lane is the routing:
one table serves a shared pool and any dedicated lane.

**Source.** Worker Roles, The Work Queue.

**Look for.** The work item type and its fields; `WORK_PAYLOADS`; the
table and the index on `idempotency_key`; how routing is expressed.

**Violation.** A row with no lease, no claim token, or no attempt
count; a payload with no shape fixed for its kind; no unique index on
the idempotency key; a second table or topic invented for routing when
the `lane` string would do.

**Severity.** medium

**Check.** `arch-check` decides the work item's fields, `WORK_PAYLOADS`,
and the unique key; the rest is judged.

## ASY-17 A worker claims within capacity and renews its leases

**Principle.** A worker runs several items at once up to a capacity it
advertises, each as its own task, and each running item renews its
lease on a timer, so no task outlives the lease behind it, the first
fence. A work handler is bounded by its lease; nothing runs unbounded.
(When a failed renewal cancels is ASY-23.)

**Source.** Worker Roles, Shape of a Worker; The Network Layer, Clients
Live in One Place.

**Look for.** The claim loop and its capacity check; the per-item lease
renewal task; what happens when renewal fails; whether a handler can
outlive its lease.

**Violation.** A worker that claims without bound; an item with no
renewal task at all, so long work loses its lease; a handler with no
bound but the process's life.

**Severity.** high

## ASY-18 Shutdown drains first and goes offline last

**Principle.** On a stop signal the worker cancels in-flight tasks,
each returns its work item to the queue with a note, then the
heartbeat stops, then the worker marks itself offline. A rollout never
runs more workers than desired at once, because a worker holds leases.

**Source.** Worker Roles, Shutdown.

**Look for.** The signal handler and its order of operations; the
deployment's rollout limits for worker roles.

**Violation.** A worker that goes offline before its work is back in
the queue; a cancelled task that leaves its item claimed until the
lease expires; a rollout configured to run extra workers during a
deploy.

**Severity.** medium

## ASY-19 Maintenance is an idempotent sweep every worker runs

**Principle.** Recurring housekeeping is a sweep every worker runs on
its own timer, idempotent and serialized by the database, with no
leader, no lock, and no scheduler: requeue items whose lease expired,
expire leases, resume parked records, roll periods, relay what a crash
left in the outbox, purge done outbox and soft-deleted rows past
retention. Resumes are staggered.

**Source.** Worker Roles, Maintenance Without a Scheduler; The Storage
Layer, Database Roles.

**Look for.** Where housekeeping runs; any leader election, cron
component, or scheduled task; how parked records are resumed; whether
the sweep relays the outbox and purges.

**Violation.** A dedicated scheduler process or cron job for
housekeeping; a sweep that is not safe to run twice concurrently; a
sweep only one elected instance runs; a sweep with no outbox relay,
so a crash between the core write and its handoff is never repaired,
or with no purge, so done rows outlive their retention; every parked
record resumed in the same instant.

**Severity.** medium

**Check.** `arch-check` decides scheduler libraries and scheduled
Terraform resources; the rest is judged.

## ASY-20 A long-running record is advanced by stateless workers

**Principle.** Work that takes minutes or hours is a durable record with
a status and a cursor, claimed and advanced by stateless workers, so
another worker picks up at the persisted position when one dies. The
claim is a separate row from the record it advances, so one record can
carry several kinds of work over its life.

**Source.** The Network Layer, Long-Running Orchestrations.

**Look for.** How long operations are modelled; what is persisted
between steps; whether the claim lives on the record or on a work
item.

**Violation.** Progress held only in a process's memory; a record with
no cursor so a restart begins from the start; a claim stamped onto the
record itself so it can carry only one kind of work.

**Severity.** high

## ASY-21 A guard parks, a bound fails

**Principle.** A record succeeds, fails, or parks. A park stops with a
reason and, where known, a time to resume, and keeps everything
achieved. A dependency that is down, a limit an operator can raise, or
an input a person must supply parks the record; only a real limit
terminates it.

**Source.** The Network Layer, Long-Running Orchestrations.

**Look for.** The status values of long-running records; what the code
does on a transient outage, a raised limit, or a missing input; the
fields that carry a park reason and a resume time; the wake paths.

**Violation.** A transient outage recorded as a failure that discards
progress; a limit breach that ends the record instead of parking it; a
parked record with no reason or no way to be woken; a safety check that
marks failure rather than parking.

**Severity.** high

## ASY-22 A worker role runs as an always-on container

**Principle.** Worker roles run as always-on containers: the same
shape as a web service minus a public network surface, so deployment,
observability, pooling, and local development are the same for both.
A worker that needs more compute is placed on a bigger box; its shape
does not change.

**Source.** Worker Roles, Implementation Options.

**Look for.** How each worker role is deployed and the compute it is
placed on; the deployment folder and the service catalog entry of
each worker.

**Violation.** A worker deployed as a scheduled one-shot or a
serverless function rather than an always-on container; a worker
whose shape differs from a web service's for reasons of compute
alone.

**Severity.** low

## ASY-23 A renewal refused with Conflict cancels at once

**Principle.** A renewal refused with `Conflict`, because another
worker holds the item now, cancels the task at once; that answer is
definitive. A renewal that fails for any other reason, a timeout, an
engine out of reach, is retried, and only half a lease without a
successful renewal cancels the task.

**Source.** Worker Roles, Shape of a Worker.

**Look for.** The renewal task's error handling: which exception ends
the task and which is retried; the clock the half-lease rule reads.

**Violation.** A `Conflict` on renewal retried until the half-lease
deadline, so a worker keeps running an item another worker holds; a
timeout or a connection error treated as definitive, so a blip cancels
sound work; a renewal failure of any kind that leaves the task running
past the lease.

**Severity.** high

## ASY-24 The fences guarantee one completion, not the record

**Principle.** The two fences guarantee one completion per item and
nothing about the record, which lives in another role: a handler is
idempotent on the item's key, a contended record carries a `version`
written by compare-and-set, and an external side effect is keyed by
the item or reconciled, never assumed exclusive. Liveness is read
back on every beat.

**Source.** Worker Roles, Shape of a Worker.

**Look for.** What the record writes compare against; the handler's
dedupe on the item's key; how an external call is keyed; the
heartbeat key under the `WORKER_LIVENESS` scope and the failure path.

**Violation.** A record write that treats the lease alone as
exclusive, with no `version` and no idempotent handler behind it; an
external side effect performed as if the lease made it exclusive; a
heartbeat that is only written and never read back; a heartbeat
failure that crashes the worker, or repeated failures that leave it
claiming.

**Severity.** high

## ASY-25 Enqueue is a create; completion spends or keeps an attempt

**Principle.** Enqueue is a create: the insert that reports an existing
id, so a retried enqueue never resets a claim, and a duplicate
`idempotency_key` is reported, never a driver error; the manager's
copy stamps actor, status, and attempts, clears every claim field, and
leaves the timestamps as constructed. Enqueue then publishes the
wake-up; claim stamps claim and lease together.

**Source.** Worker Roles, The Work Queue.

**Look for.** The enqueue path: the insert primitive it uses, what the
manager's copy overwrites, and the order of write and publish; its two
callers, the outbox relay for a work item that follows a core write,
which presents the row's id as the item's `idempotency_key`, and a
holder of a context for one that follows none; the claim,
complete, defer, requeue, and fail methods and what each does to
`attempts`.

**Violation.** An enqueue that upserts, so a retry resets a claim or
announces twice; a caller-supplied status, attempt count, or claim
field written as sent, or a timestamp the copy resets; a publish
before the row exists; a manager that enqueues in a second statement
after its own core write instead of riding the second outbox row of
that write (STO-20); a relayed enqueue whose key changes between runs,
so a second relay lands a second item; a failed attempt requeued with
no delay; an item that fails its last attempt with no audit entry and
no metric (the hand-back that spends no attempt is ASY-26).

**Severity.** high

## ASY-26 The claim token fences every write to the queue row

**Principle.** Every claim mints a claim token the claim returns, and
completion, release, deferral, and renewal carry it and condition on it
in the statement itself, the token and never the worker's name, since
one worker can hold one item twice across a requeue; a stale holder is
refused with `Conflict` and hands the item back without spending an
attempt.

**Source.** Worker Roles, Shape of a Worker.

**Look for.** The `claim_token` the claim returns beside the item and
stamps on the row, `claimed_by` staying the worker's name for an
operator; the `WHERE` of
completion, release, deferral, and renewal in both storage impls; what
the loop does with a `Conflict` from any of them.

**Violation.** A completion, release, deferral, or renewal that matches
on `claimed_by` alone or on the key alone, so a stale holder writes; a
claim that returns no token, so the fence has nothing to compare; a
refused write that spends an attempt, or an item a worker finds is not
its to run failed or handed back with an attempt spent.

**Severity.** high

## ASY-27 A cross-service chain expires its first step or is a saga

**Principle.** A synchronous chain across services carries its
idempotency key forward, so a retry reruns a step instead of repeating
it, and a step that reserves something bounds it with an expiry. A
chain that must survive a crash between steps is a durable record
advanced by workers: the irreversible step last, a compensating step
for each one before it.

**Source.** The Network Layer, Long-Running Orchestrations; Direction
of Calls.

**Look for.** Every service impl that sequences calls across services;
the key each step is called under and whether a retry reruns it; the
expiry on anything a step reserves; the compensation of each step
before the irreversible one.

**Violation.** A step that reserves something with no expiry, so a
failed later step leaks it; a step called under no key, so a retry
repeats it; an irreversible step followed by one that can fail; a
chain that must outlive a crash held only in the service's memory.

**Severity.** high

## ASY-28 The local secrets impl reads the environment and an owner-only file

**Principle.** The local secrets impl reads environment variables and an
owner-only file; the cloud impl talks to the managed secret manager.

**Source.** Infrastructure, Secrets.

**Look for.** The local impl's file read and the permission check it
makes before reading; where the file's path comes from; which impl
the configured root selects per environment.

**Violation.** A local impl that accepts a secrets file readable by
group or others, or reads values from a world-readable path with no
check; a local impl that reaches a network store; the cloud impl
reading a file.

**Severity.** low

**Check.** `arch-check` decides the owner-only mode check before every
secrets file read; the rest is judged.

## ASY-29 The work item carries the request that caused it

**Principle.** A work item carries the request that caused it and that
request's trace context, a `traceparent` and not a `trace_id`: the
relay takes both off the outbox row of the write, a direct create off
its caller's context. The span the run raises links to that trace
context rather than becoming its child.

**Source.** Worker Roles, The Work Queue; Telemetry, Correlation
Across a Handoff.

**Look for.** The work item type and the columns behind the two
fields; both enqueue paths, the relay's build from `(org_id, row)` and
the direct create, and where each reads them; what the span a handler
raises is linked to, and what it does when the item's traceparent is
empty.

**Violation.** A work item with no request id or no traceparent, so
the trail ends at the queue or the link has nothing to point at; a
relayed enqueue that mints either afresh instead of taking the row's;
a run whose span starts an unlinked trace, or one that runs as a child
of the causing span, stretching one trace across the queue.

**Severity.** medium

**Check.** `arch-check` decides the two fields on the work item and the
outbox row; the rest is judged.

## ASY-30 A degraded answer is declared at the read, never silent

**Principle.** Where a read may answer from a degraded source, the
degradation is chosen at that read and visible to its caller: a cache
that fails open, a limit that fails open, a channel that degrades to
polling. Nothing silently substitutes a stale answer for a fresh one.

**Source.** Infrastructure, Cache.

**Look for.** Every read that can answer without its source: what a
manager does when the cache backend is unreachable and what its caller
learns; any fallback path inside a storage impl or a service impl.

**Violation.** A read that falls back to a stale or partial source and
returns it as if it were fresh; a fallback buried in an impl, where
the manager that owns the cost of a stale read cannot see it; a caller
with no way to tell a degraded answer from a fresh one.

**Severity.** medium
