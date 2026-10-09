# Work items

Durable background jobs and the queue they wait in. This is one of the
kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Work item**: the kind, the record it is for, a producer key unique
  within its org, a lane, and the request that caused it. It carries its
  status (queued, claimed, done, failed), when it becomes available,
  who claimed it and until when, its attempts, and its last error.
- **Kind**: the job's shape, with a fixed payload and the permission a
  person needs to ask for it. The core has five: `NOOP`, a step of an
  `ORCHESTRATION`, `WAKE_PARKED` for the records a cleared reason
  frees, and `DELETE_ACCOUNT` and `DELETE_ORG` for the identity
  provider's side of a deletion. A kind whose payload names a time
  waits until then.
- **Lane**: a routing name. A worker serves one lane. A kind can have a
  lane of its own, such as a long-held kind kept apart from short ones;
  any other kind runs on the default lane.
- **Handler**: the code that does one kind. It is idempotent, because
  an item may run twice.

## What can happen

- **Enqueue**, by a person's request or by the outbox relay when a
  write asked for work. The item starts queued, with no attempts and no
  claim. The relay puts it on its kind's lane.
- **Claim.** A worker takes the item on its lane ready longest, in one
  statement, with a claim token and the context the job runs under: the
  org, the service role, and the person who asked. An item of a deleted
  org fails in the same call. On a lane with a cap, the claim passes
  over an org that already holds that many items claimed and takes the
  next org's; the passed-over items wait where they are, untouched.
- **Complete, fail, defer, release, or extend the lease.** A failure is
  retried with a growing delay until the attempts are spent.
- **Park.** A handler that must wait (a provider out of reach) hands the
  item back for a time, with no attempt spent.
- **Fail for good.** A handler whose failure no retry changes (a
  provider refused the call itself) fails the item at once.
- **Requeue by an operator.** An operator with `write` sends one failed
  item back with every attempt it had. The org's stream records who did.
- **Sweep.** Expired leases go back to the queue, or fail when their
  attempts are spent. Done and failed items go after thirty days.
- **Watched.** Each sweep reads the wait of the item ready longest and
  the count failed in the last fifteen minutes, and an alarm fires on a
  wait past ten minutes and on any failure.

## The rules

- **The lease.** A claim holds an item for a lease, and the worker
  renews it while the job runs. Every transition is conditional on the
  claim token, so a worker that lost its item changes nothing.
- **One org cannot hold every worker.** A lane that orgs share can cap
  how many items one org holds claimed on it. An org at its cap spends
  no attempt waiting, and its next item runs as soon as one of its
  running items ends.
- **Enqueueing twice leaves one item.** The same id or the same producer
  key returns the item as stored.
- **At least once.** Every handler changes nothing the second time.
- **A dead letter is named.** An item that fails for good is counted
  and recorded as `work.item.failed` in its org's stream.
- **The person authorized the work once.** The job runs while the org is
  live, even after the person has left it, and whoever may ask for a
  kind may do everything its handler does.

## How another namespace composes it

A write that starts work lands a `work.<kind>` outbox row beside its
own, and the relay enqueues the item under the row's id; a namespace
never enqueues across a role itself. A new kind adds its name to
`WorkKind`, its payload to `WORK_PAYLOADS`, its permission to
`WORK_ENQUEUE_PERMISSIONS`, and its handler to the worker. A kind that
runs on a lane of its own adds the lane to `WORK_LANES`, which the relay
reads through `relayed_lane`, and a worker serves that lane. A handler
raises `WorkParked` to wait and `WorkRefused` to fail for good.
