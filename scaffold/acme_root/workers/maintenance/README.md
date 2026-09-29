# The maintenance worker

The background process of Acme. It runs the work queue's claim loop,
the consumer of what Slack sends, and, on a timer, the sweep that keeps
the platform tidy. Every replica is the same process on one lane; a
second lane is a second replica told its lane.

## What it does

- **Claim, handle, complete.** The loop wakes on `work_available`,
  with a short poll as the fallback for a missed wake-up, and claims
  the item on its lane ready longest while it has a free slot. Each
  item runs as a task of its own, under the context the claim produced,
  naming the request that caused the work and linking its trace to
  that request. When the handler returns, the item is completed; when
  it raises, the item is failed, which requeues it with a growing delay
  or, once its attempts are spent, makes it a dead letter. When it
  parks, the item is handed back for the time it named and spends no
  attempt. When it is refused, a failure no retry changes, the item is
  a dead letter at once. An operator sends a failed item back with
  `acme-ops work requeue`.
- **The kinds.** A reminder waits for nine in the morning of its task's
  due date, in the time zone of the person the task is for, read when it
  runs; before then it parks until then. Then it marks its task reminded
  and announces it, if the task is still open and still due on that
  date; otherwise it sends nothing. A Slack post writes one line to
  the org's channel (a task created, completed, or reminded of),
  records it under the item's key so a rerun posts nothing, parks on a
  rate limit for the time Slack named, and marks the installation
  broken when Slack refuses the channel for good. `SYNC_SEATS` reads an org's
  active members when it runs and holds a Max subscription's quantity to
  them, with no proration and under a key made of the item and the
  count, so a retried run is one change. A processor out of reach, or
  refusing the process's own key, parks it for a minute, spending no
  attempt; a processor that refuses the request itself fails it at
  once. An orchestration step reads its
  record and, while it runs, does one batch of it: an import makes the
  tasks of the next hundred rows, a cleanup archives the next five
  hundred old done tasks. The batch, the record's next cursor, and the
  next step's item land in one commit. A step that raises is retried by
  the queue from the cursor the last commit left; on its last attempt the
  record fails as `defect`. A wake-up resumes the org's records parked
  for the reason a plan that rose cleared. A deleted account's item runs
  in the person's personal org: it deletes the person at WorkOS, cancels
  the org's subscription at once and deletes its customer at Stripe,
  removes its Slack app, and then deletes the org, which the next sweep
  purges whole. A provider that does not answer, or that refuses the
  process's own key, parks it for a minute, spending no attempt; a
  provider that refuses the call itself fails it at once. Each step finds
  its own work done on a rerun. The person's open tasks in each team org
  they left go unassigned by an item of their own, run under their name.
  A team org its owner or an operator deleted has an item of its own,
  run in the org: it deletes the org's organization at WorkOS, cancels
  the subscription and deletes the customer at Stripe, removes the Slack
  app, and then deletes the org, which the sweep purges after the
  retention. It waits and fails as the account's item does.
  The last kind does nothing and keeps the loop honest. The worker holds
  the WorkOS key for the account's and the org's items, as the API does.
- **What Slack sends.** Slack calls the API, which checks each call's
  signature, acknowledges it within Slack's three seconds, and queues
  it on `slack`. The worker reads that queue: `/acme` commands,
  mentions, the App Home opening, the app's uninstall, and the bot
  joining a channel. When someone invites the bot back to the channel
  Acme posts to, an installation that channel broke is well again and
  posts resume. The org is
  the one that installed the app in the call's workspace. The person
  is the org's member whose sign-in proved the email their Slack
  profile holds; someone Acme cannot match is told how to join.
  `/acme` alone answers with your ten newest open tasks, `/acme team`
  with the org's, each with a count of the rest and a link to the
  portal at `ACME_PORTAL_URL`. `/acme add <title>` creates a task
  made by the person who typed it. `/acme connect`, from an owner or
  an admin, makes the channel it was typed in the one Acme posts to.
  `/acme help`, and anything else, answers with the usage, and so
  does a mention, in its thread. A task past the org's plan is not
  added, and the reply names the plan and where to upgrade. A call
  handled twice changes nothing: the task takes an id derived from the
  call's key, and a mention's reply is recorded under it. A call that
  fails stays on the queue and comes back.
- **Apply the payment processor's deliveries.** Beside the claim loop,
  the worker long-polls the inbound queue the webhook route fills. For
  each delivery it finds the org the delivery names, mints that org's
  service context, and applies the delivery: the subscription is read
  from the processor again, and the delivery's mark lands in the
  account's commit, so a copy changes nothing. A message is deleted once
  it is applied, or once it can never be (no org named, or the org is
  gone); any other failure leaves it to come back after its visibility,
  and the queue dead-letters it past its receives.
- **Renew the lease and fence itself.** While an item runs, the worker
  renews its lease. A renewal refused because the lease was lost
  cancels the running task at once, since another worker holds the
  item now. A renewal that fails for any other reason is retried, and
  the task is cancelled at half the lease if none succeeds, so no
  worker keeps working an item it may no longer settle.
- **The sweep**, every thirty seconds by default. The requeue, the
  relay, every purge of rows past their retention, and the read of the
  orgs with a chore due reach every org at once; only the work shaped
  by one org runs under that org's service context:
  - **Requeue** items whose lease has expired, or fail them when their
    attempts are spent. This runs first, once for every org together,
    in the system scope: a batch of a hundred at a time (`requeue_batch`),
    chosen with `FOR UPDATE SKIP LOCKED` and again while a batch comes
    back full, so a crashed worker's item waits one sweep at most. An
    org's items come back staggered, five seconds apart. An item whose
    attempts are spent is a dead letter, named by an event in its org's
    diary; an org that is gone has no diary, so it is logged and counted.
  - **Relay** the outbox rows the request path left behind, one
    attempt each with a growing delay, and fail the ones whose
    attempts are spent. This runs next, for every org together, a
    hundred rows at a time (`outbox_batch`) and again while a batch
    comes back whole, so a backlog after an outage of the bus drains at
    the pace of the budget. A batch with a row that failed ends it, so
    a bus that is still down is not asked again until the next sweep.
  - **The chores**, in the orgs that have one due and in no other. One
    read across every org names them: the orgs with a done task past the
    day's archive cut, and the orgs with an open task whose rank grew
    long, each found on a partial index that holds only such tasks. The
    read takes a page of a thousand orgs a sweep, in id order, and the
    next sweep reads on from the last org this one ran, so a backlog of
    them is a page a sweep. An org with no chore due costs a sweep
    nothing (ADR 0070). The chores run before the purges per org, and
    past the budget they still take one org, so no backlog elsewhere
    skips them. Each org they take runs both, under its service context:
    - **Open the day's cleanup** of the org when it has a done task
      nobody changed for the archive age
      (`ACME_TASKS_ARCHIVE_AFTER_DAYS`, ninety by default) when the day
      began, in UTC. The org, the kind, and the day are the record's
      unique key, so the first sweep of the day opens it and every later
      one opens nothing. Once the record has run, the org has nothing to
      archive until the next day begins, and the read stops naming it.
      No scheduler is involved.
    - **Respace a long rank** of the org when its open list has one: a
      rank past 24 digits after the point, which only many moves into
      one and the same gap make. The run of tasks around it takes short
      ranks, in the order it had, in one write, each task announced like
      an edit.
  - **Per org**, the system scope first and deleted orgs included, the
    **purge of an org deleted longer ago than the retention.** Every row
    goes, its event stream included, and the org row stays as the
    record. Each namespace asks tenancy the one question, whether the
    org has expired, and the sweep answers it from the org rows it read
    to list the orgs. So an org that lives costs these purges nothing.
    Once a sweep finds nothing left of an expired org, it marks the org
    purged and leaves it out from then on.
  - **Purge** each namespace's rows past its retention, once for every
    org together, in the system scope: deleted tasks (their attachments
    first, under the task's org, so a detach that failed when the task
    was deleted is tried again, and the task waits for the next sweep
    while its files will not go), removed files and uploads never
    confirmed (the object in the store first, then the row, a hundred
    at a time), removed members with their ended memberships, revoked
    keys, expired sessions and tickets, closed invitations, finished
    idempotency records, the payment processor's delivery marks, Slack's
    old install states and posts, settled work items, and orchestrations
    that succeeded or failed thirty days ago. Events older than
    `ACME_EVENT_RETENTION_DAYS` (90 by default) go too, and each org's
    floor moves with its own in the same statement; 0 keeps every event
    (ADR 0040). Each purge reads an index that leads with the column its
    retention is counted on (ADR 0045).
  - **Purge** the outbox rows done or failed past eight days, which
    outlives the database backup retention.
  - **Count the platform's size**, once every five minutes and on the
    worker's first sweep, whatever the budget: the live orgs and users,
    and the tasks created and the events produced in the day before the
    count. The count is kept as one row in the `admin` role, which the
    operator plane's size reads, so no request counts across tenants
    (ADR 0074). A count older than the row's does not replace it, and a
    count that fails is tried again on the next sweep. At 5,000 orgs it
    takes about 20 ms.

  Every purge statement deletes a batch at most, a thousand rows by
  default, chosen with `FOR UPDATE SKIP LOCKED`, so no statement grows
  with a backlog past the database's statement deadline and two workers
  split a backlog between them. A purge whose batch comes back full runs
  again while the sweep's budget lasts, twenty seconds by default, and so
  do the requeue and the relay. Past the budget the sweep takes no new
  org, but always one, and the next sweep starts at the org it stopped
  at, so every org is reached in turn.

  Each pass ends with four reads across every tenant, one statement
  each, whatever the budget: how long the work item ready longest has
  waited, on any lane; how many items failed in the last fifteen
  minutes and are still failed; how long ago the oldest outbox row
  neither relayed nor failed landed; and how many outbox rows failed for
  good in the last fifteen minutes, which the lag no longer sees. It
  then logs one line with its duration and the four, as the fields
  `sweep.duration_ms`, `sweep.work_oldest_ready_seconds`,
  `sweep.work_failed_recently`, `sweep.outbox_oldest_pending_seconds`,
  and `sweep.outbox_failed_recently`, which the cloud's alarms read.
  The four are gauges of the same names on `/metrics` too, which
  Grafana draws. A read that fails leaves its field off the line and its
  gauge as it was, so an alarm sees no data rather than a zero.

  The knobs, all in `.env.example`: `ACME_WORKER_PURGE_BATCH`,
  `ACME_WORKER_SWEEP_BUDGET_SECONDS`, `ACME_WORKER_TALLY_SECONDS`
  (how often the size is counted), and one retention per kind of
  row, `ACME_<KIND>_RETENTION_DAYS` or `_HOURS`. Each defaults to what
  it has always been but the socket tickets', a day: a ticket lives a
  minute.
- **Liveness.** The worker beats every ten seconds by default, in
  memory, and publishes each beat to the cache as best effort, bounded
  by its interval, so other replicas can see it. Its `/healthz`, served
  on the metrics port, answers 200 while the last beat is within three
  intervals; a cache outage neither fails the probe nor stops claiming,
  because the queue and its leases live in Postgres. The container
  probe and the `health` subcommand ask that URL. `/metrics` on the
  same port is what the collector scrapes.
- **Drain first.** On stop, the loop claims nothing new, cancels the
  items it holds and waits for each to hand its item back to the queue
  for another worker, then marks itself offline. A cloud rollout replaces one worker at a time, because a
  worker holds leases.

## The subcommands

| Subcommand | Does |
|------------|------|
| `serve` | Runs the loop and the `slack` queue's consumer; `--lane` overrides the lane from settings. |
| `health` | Asks the serving process's `/healthz` by hand; exits 0 on 200. |

## What it never does

It never runs a job under a person's credential: a claimed item runs
under the service role for the org, with the person who asked kept as
the attribution. It never hard-deletes anything before its retention.
And it never settles an item it no longer holds: every write after the
claim is conditional on the claim token.
