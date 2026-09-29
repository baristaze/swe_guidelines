# Work items

Durable background jobs and the queue they wait in. This is one of the
seven kinds of thing [Acme is made of](../../../../README.md).

## The nouns

- **Work item**: what to do (the kind), for which record, under which
  producer key, on which lane, and the request that caused it. It
  carries its status (queued, claimed, done, failed), when it becomes
  available, who claimed it and until when, how many attempts it has
  spent of how many it has, and its last error.
- **Kind**: the job's shape. The payload of each kind is fixed. There
  are nine: a task's reminder, a post to the org's Slack channel, the
  seat count of a per-seat subscription brought in step with the org's
  members, one step of an [orchestration](../orchestrations/README.md),
  the wake of the orchestrations a plan that rose freed, what is left of
  a deleted account (the person at the identity provider, the personal
  org's subscription, customer, and Slack app, then the org itself), the
  open tasks a person who deleted their account leaves assigned in a
  team org, what is left of a team org its owner or an operator deleted (its
  organization at the identity provider, its subscription, customer,
  and Slack app, then the org itself), and one that does nothing but keep the loop honest. A kind
  whose payload names a time waits in the queue until then. Each kind
  names the permission a person needs to ask for it, and whoever holds
  that permission may do everything the job does.
- **Lane**: a routing name. A worker serves one lane.
- **Handler**: the code that does one kind of work. A handler is
  idempotent, because the same item may run twice.

## What can happen

- **Enqueue.** Directly by a person's request, or by the outbox relay
  when a change asked for work: setting a due date, a task created,
  completed, or reminded in an org with a Slack channel, a member
  added or removed in an org on Max, an orchestration started, stepped,
  or woken, a plan that rose, an account deleted, and an org deleted by
  its owner. The item starts queued with zero
  attempts and no claim, whatever the caller sent.
- **Claim.** A worker takes the item on its lane that has been ready
  longest, in one statement, and gets a claim token and the context the
  job runs under: the org, the service role, and the person who asked
  as the attribution. An item whose org is gone is failed in the same call.
- **Complete**, **fail** (requeued with a growing delay, or a dead
  letter once the attempts are spent), **defer** (hand it back for
  later), **release** (hand it back now), **extend the lease**.
- **Fail for good.** A job whose failure no retry changes (a provider
  refused the call itself, not its availability and not the process's
  own key) is failed at once, with the reason as its note: a dead
  letter, with the one attempt it spent. Only a failure that can come
  out differently is asked again.
- **Requeue by an operator.** A person on the operator plane with the
  write permission sends one failed item back to the queue, available
  now, with every attempt it had. An item that is not failed is
  refused. The org's diary names the requeue and the operator who made
  it.
- **Park.** A job that must wait (Slack asked for a pause, or a
  provider did not answer a deleted account's or org's cleanup) is handed
  back for the time it named, with the reason as its note, and spends
  no attempt: a guard parks, only a real limit fails.
- **Sweep.** Items whose lease has expired go back to the queue, or
  fail when their attempts are spent, every org's in one step at the
  start of each sweep, so a crashed worker's item waits one sweep at
  most. Done and failed items are erased after the retention, thirty
  days by default, every org's in one step.
- **Watched.** Each sweep reads how long the item ready longest has
  waited and how many items failed in the last fifteen minutes, across
  every org, and an alarm fires on a wait past ten minutes and on any
  failure. An item parked until later is not waiting yet.

## The rules

- **The lease.** A claim holds an item for a lease, one minute by
  default, and the worker renews it while the job runs. Every
  transition is conditional on the claim token, in the statement
  itself, never on the worker's name, because one worker can hold the
  same item twice across a requeue. Once the sweep has requeued an item
  whose lease passed, the worker that held it is refused, hands nothing
  back, and spends no attempt: the requeue cleared its token. Until the
  sweep runs, a late worker's transition still lands.
- **Attempts.** A claim spends one; a hand-back refunds it. An item
  has three by default. The delay before a retry doubles per attempt,
  up to a cap. An org's requeued items are staggered so a recovered
  dependency is not met by all of them at once.
- **A dead letter is named.** An item that fails for good is counted
  and recorded as an event in the org's diary, and so is its requeue.
- **Enqueueing twice leaves one item.** An enqueue that runs again
  under the same id or the same producer key returns the item as
  stored, its claim intact. A producer key is unique within its org.
- **At least once.** A job may run twice, so every handler is written
  to change nothing the second time.
- **The person authorized the work once.** The job runs as long as the
  org is live, even if the person who asked has since left.
