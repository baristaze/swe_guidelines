# Orchestrations

Work that takes minutes, kept as a record and done a step at a time.
This is one of the kinds of thing [Acme is made of](../../../../README.md).

## The nouns

- **Orchestration**: one long job of an org, kept as a record. It has a
  kind, what it works on (its input), a status, and a cursor: where the
  next step starts. It counts what its steps did, the rows they skipped,
  and names the first twenty skipped rows with the reason.
- **Kind**: what the job is. There are two, and both are the
  [tasks](../tasks/README.md) namespace's: an **import** of tasks from a
  CSV file, and the daily **cleanup** that archives old done tasks.
- **Status**: running, parked, succeeded, or failed.
- **Park reason**: why a parked record waits, and so what wakes it.
  There is one: `plan_limit`, the plan's bound on active tasks.
- **Fail reason**: why a record ended without finishing. A bound of the
  input (a file too large, too many rows, not a CSV file, no `title`
  column, a file removed), or `defect`: a step still failed on its last
  attempt.
- **Period**: the day a record kept per day is for. The org, the kind,
  and the day are its unique key.

## What can happen

- **Start.** The record is written running at its first cursor, and
  the work item of its first step rides the same commit.
- **Step.** A worker claims the step's work item and does one batch.
  The batch's effect (the tasks it created, the tasks it archived), the
  record's next cursor, and the work item of the next step land in one
  commit, so a worker that dies leaves the record at its last committed
  step.
- **Park.** A guard stops the record at its cursor and keeps what it
  achieved. It is written in the step's own commit.
- **Wake.** A parked record runs again from its cursor, when the event
  that clears its reason happens (a plan that rose clears `plan_limit`),
  or when a person presses Resume. The records one event wakes start a
  couple of seconds apart.
- **Succeed** when the last step is done, or **fail** on a bound.
- **Sweep.** A record that succeeded or failed is erased thirty days
  later. A parked or a running one is kept.

## The rules

- **A guard parks, a bound fails.** A limit a person can lift (the
  plan) stops the record and keeps what it made; a limit of the input
  itself ends it. Nothing a record made is ever undone by its ending.
- **An error that is neither is the queue's.** A database or a store
  that did not answer makes the step raise, and the
  [work queue](../work/README.md) retries the item with a growing delay.
  The retry starts at the cursor the last commit left. On the item's last
  attempt the record fails as `defect` instead, so no record waits on
  work that will not come.
- **Every write names the version it read.** A step, a park, a wake, and
  a failure land only while the record is at the version they read. A
  worker that held an item past its lease and writes after the next
  holder is refused, and its batch lands nothing.
- **A step runs twice and does once.** A step may run twice (at least
  once delivery). The ids of what it makes are derived from the record
  and the row, so a second run meets them already there, and the count
  goes up only by what a commit wrote.
- **A step asks its guard again.** A woken record is not trusted to have
  room: its next step reads the plan and the count again, and parks
  again if the room is still not there.
- **A record belongs to its org**, like everything else, and carries the
  same fence.
