# Async

Group id: `async`. Covers Infrastructure, The Network Layer
(Idempotency on the Consumer Side, Long-Running Orchestrations), and
Worker Roles of `architecture.md`.

This group judges everything that happens off the request path: the
infrastructure capabilities managers lean on, how work is handed off
and picked up, how a worker behaves over its life, and what a
long-running record does when it cannot continue. It leaves the
`org_id` and `EMPTY_UUID` keying rules and the provenance of a worker's
context to `context`, database roles and the storage side of the work
queue to `storage`, and the realtime edge with its wait-versus-notify
patterns to `network`.

## ASY-01 Infrastructure is never reached through ambient state

**Principle.** Every infra capability is fronted by an interface with
swappable impls, and a handle to one arrives only through a
constructor at boot, never through a global, a thread local, a
module-level client, or `OpContext`.

**Source.** Infrastructure, Infrastructure Principles.

**Look for.** Any module-level cache, bucket, topic, queue, or secret
client; any attribute on the context that hands out an infra handle;
any impl constructed inside a manager or handler body.

**Violation.** A manager imports a concrete cache or bucket client and
builds it itself; a global `topics` object is reached from inside a
handler; an infra handle is read off `ctx`.

**Severity.** high

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

## ASY-03 Every impl describes itself and boot logs the choice

**Principle.** Every infra impl can `describe()` itself in one line, and
the container logs the chosen backends once at start, so a boot log
names exactly what the process is talking to.

**Source.** Infrastructure, Infrastructure Principles.

**Look for.** A `describe()` on each impl; the one-line inventory the
container logs after `start()`.

**Violation.** An impl with no description; a boot log that never names
the backends; an operator who has to read settings to know whether a
process is on the local or the cloud backend.

**Severity.** low

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
manager raises on a cache miss; a counter, a lock, or a record whose
only copy is a cache entry.

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

## ASY-10 Durable work never rides a topic

**Principle.** A topic is best effort: a published event reaches the
processes subscribed at the time, at most once, and a bus hiccup may
lose it. It carries wake-ups and live updates; durable work is a row
in the work queue, and a missed notification degrades to polling
latency, never to lost work.
When the bus is backed by the database, it connects to the queue role,
because the processes that enqueue work and the workers they wake must
share it.

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
and the store it was looked up in, never a value. A process that names
itself staging or production and finds the file backend configured
refuses to start.

**Source.** Infrastructure, Secrets.

**Look for.** Entity fields that hold credentials; where `get(name)` is
called and how long the value lives; log and audit calls near secret
resolution; the text of the not-found error; subprocess environment
construction; the boot-time backend check.

**Violation.** An entity with a token or password field; a secret
resolved at boot and kept on an object; a value in a log line, an
error string, or an audit payload; a not-found error that omits the
secret name or the store; a subprocess inheriting the parent's full
environment; a production deployment on the file backend that starts
anyway.

**Severity.** high

## ASY-14 Every handler is idempotent on a producer-generated key

**Principle.** Delivery is at-least-once on every queue. Every message
carries a producer-generated idempotency key (or the outside system's
delivery id), the handler dedupes before doing work through a unique
index or an upsert keyed on it, and every pipeline stage forwards the
key. The key lives on the row the effect produces, or marker and
effect are one named atomic write (the idempotent consumer).

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
recurring tasks. Every such need is an explicit worker role with its
own container and deployment: an always-on container with the same
shape as a web service minus a public network surface, placed on
bigger compute when it needs more without changing shape. The one
thing that is not a job is a topic subscriber that only forwards
events to sockets its own process holds; anything that writes,
retries, or outlives a connection is a worker.

**Source.** Worker Roles, Workers, Not Web-Service Side Jobs;
Implementation Options.

**Look for.** Background tasks created inside a service process;
timers and schedulers in service code; in-process subscribers and what
they do; how each worker role is deployed.

**Violation.** A request handler that starts a task which outlives the
request; a service that runs a periodic sweep; an in-process subscriber
that writes to storage or retries deliveries; a worker deployed as a
scheduled one-shot or a serverless function rather than an always-on
container.

**Severity.** high

## ASY-16 Durable work is a row with the queue's shape

**Principle.** A work item names its kind and target, carries a unique
idempotency key, a `lane` routing string, a status, an `available_at`,
its claim (`claimed_by`, `lease_expires_at`), and its attempts; payload
shapes are fixed per kind by `WORK_PAYLOADS`. Enqueue writes the row
and then publishes the wake-up. Claim takes the oldest available row
in the named lane and stamps claim and lease together. Completion
marks done, requeues with a growing delay, or fails when attempts run
out, and a failed item is a dead letter named by an audit entry and
counted by a metric; handing an item back costs no attempt.

**Source.** Worker Roles, The Work Queue.

**Look for.** The work item type; the enqueue, claim, complete, defer,
and requeue methods; the order of write and publish in enqueue.

**Violation.** A publish before the row exists; a row with no lease or
no attempt count; a payload with no shape fixed for its kind; a failed
attempt requeued immediately with no delay; an item that fails its
last attempt with no audit entry and no metric; a hand-back that
spends an attempt; a second table or topic invented for routing when
the `lane` string would do.

**Severity.** high

## ASY-17 A worker claims within capacity, renews, and fences itself

**Principle.** A worker runs several items at once up to a capacity it
advertises, each as its own task. Each running item renews its lease on
a timer, and a lease that could not be renewed for half its length
cancels its own task before the lease expires: the first fence. The
second is that completion and every write to the record the item
advances are conditional on the claim (`claimed_by` and
`lease_expires_at` on the queue row, a compare-and-set on `version` on
the record), so a stale worker's write is refused with `Conflict` and
the item is handed back without spending an attempt (a fencing token).
Liveness is a key with a TTL under the system scope, written and read
back on every beat; repeated failures stop claiming but let held work
finish.

**Source.** Worker Roles, Shape of a Worker.

**Look for.** The claim loop and its capacity check; the per-item lease
renewal task; what happens when renewal fails; what the completion
statement and the record writes compare against; the heartbeat key
and the failure path.

**Violation.** A worker that claims without bound; an item with no
renewal so long work loses its lease; a completion or record write
that is not conditional on the claim, so a worker whose lease passed
lands a write; a stale write that spends an attempt; a heartbeat that
is only written and never read back; a heartbeat failure that either
crashes the worker or lets it keep claiming.

**Severity.** high

## ASY-18 Shutdown drains first and goes offline last

**Principle.** On a stop signal the worker cancels in-flight tasks, each
returns its record to the queue with a note, then the heartbeat stops,
then the worker marks itself offline. A rollout never runs more workers
than desired at once, because a worker holds leases.

**Source.** Worker Roles, Shutdown.

**Look for.** The signal handler and its order of operations; the
deployment's rollout limits for worker roles.

**Violation.** A worker that goes offline before its work is back in
the queue; a cancelled task that leaves its record claimed until the
lease expires; a rollout configured to run extra workers during a
deploy.

**Severity.** high

## ASY-19 Maintenance is an idempotent sweep every worker runs

**Principle.** Recurring housekeeping is a sweep every worker runs on
its own timer, idempotent and serialized by the database, with no
leader, no lock, and no scheduler component: requeue expired leases,
resume parked records, relay the outbox rows a crash left behind,
purge soft-deleted rows past their retention period. Resumes are
staggered so a recovered dependency is not met by every parked record
at once.

**Source.** Worker Roles, Maintenance Without a Scheduler.

**Look for.** Where housekeeping runs; any leader election, cron
component, or scheduled task; how parked records are resumed; whether
the sweep relays the outbox and purges.

**Violation.** A dedicated scheduler process or cron job for
housekeeping; a sweep that is not safe to run twice concurrently; a
sweep only one elected instance runs; a sweep with no outbox relay,
so a crash between the core write and its handoff is never repaired;
every parked record resumed in the same instant.

**Severity.** medium

## ASY-20 A long-running record is advanced by stateless workers

**Principle.** Work that takes minutes or hours is a durable record with
a status and a cursor, claimed and advanced by stateless workers, so
another worker picks up at the persisted position when one dies. The
claim is a separate row from the record it advances. A synchronous
chain across services gives its first step an expiry and carries its
id forward; a chain that must survive a crash between steps is such a
record, the irreversible step last and a compensating step for each
one before it (a saga).

**Source.** The Network Layer, Long-Running Orchestrations; Direction
of Calls.

**Look for.** How long operations are modelled; what is persisted
between steps; whether the claim lives on the record or on a work item;
the expiry and the compensation of each step in a cross-service chain.

**Violation.** Progress held only in a process's memory; a record with
no cursor so a restart begins from the start; a claim stamped onto the
record itself so it can carry only one kind of work; a synchronous
chain across services whose first step has no expiry or compensation;
an irreversible step followed by one that can fail.

**Severity.** high

## ASY-21 A guard parks, a bound fails

**Principle.** A record succeeds, fails, or parks. A park stops with a
reason and, where known, a time to resume, and keeps everything
achieved. A dependency that is down, a limit an operator can raise, or
an input a person must supply parks the record; only a real limit
terminates it. A parked record is woken by the event that clears its
reason, by the sweep at its resume time, or by a person.

**Source.** The Network Layer, Long-Running Orchestrations.

**Look for.** The status values of long-running records; what the code
does on a transient outage, a raised limit, or a missing input; the
fields that carry a park reason and a resume time; the wake paths.

**Violation.** A transient outage recorded as a failure that discards
progress; a limit breach that ends the record instead of parking it; a
parked record with no reason or no way to be woken; a safety check that
marks failure rather than parking.

**Severity.** high
