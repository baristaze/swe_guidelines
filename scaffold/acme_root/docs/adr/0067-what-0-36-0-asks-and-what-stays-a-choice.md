# ADR 0067: What guideline 0.36.0 asks, and what stays a choice

**Status**: accepted (2026-09-26). Its first decision is no longer a
deviation: since v0.37.0, STO-20 marks a work row done once its item is
queued, as Acme does ([ADR 0079](0079-what-0-37-0-asks.md)). The other
two stay choices.

## Context

Acme moves its pin from guideline v0.35.0 to v0.36.0. The release makes
four audits built-in skills, keeps an outbox row pending until its side
effect happened, and puts a pull behind a trust decision pushed to a
socket. Here is where Acme stands on each rule the release changes:

| Rule | What v0.36.0 asks | Here |
|------|-------------------|------|
| OPS-11, Operational Skills | Thirteen built-in skills, the nine operational skills and four audits, and two optional audits. An audit is read-only and proposes tickets. An audit of calls removes, folds, defers, and caches a call before it runs calls in parallel. | All fifteen, under `.claude/skills/`, with their tools under `ops/audit/`. `audit-database-calls` ranks a cache before parallel reads. |
| OPS-10 | `local` for every skill but the administrator's two and the deploy audit. | `audit-deploy-time` takes `--env staging\|production`. The other audits run on the local stack. |
| ASY-09, Topics | `publish` returns no id: `None`, or a boolean that says the bus took the event. | `publish` answers a boolean ([ADR 0062](0062-a-dropped-publish-leaves-its-outbox-row-pending.md)). Its exception leaves `pyproject.toml`. |
| STO-20, Database Roles | The relay marks a row done only when its side effect happened. A dropped publish leaves the row pending. | An entity change, yes (ADR 0062). A work row, no: see below. |
| NET-30, Realtime at the Edge | A push may carry the entity's `version`. | A task's push does ([ADR 0061](0061-a-task-push-names-the-version-it-wrote.md)). |
| CTX-27, Stages | Every socket rechecks its session and membership on an interval, without moving `last_seen_at`. Closing or pausing a connection never ends the session. | The recheck runs every 300 seconds and records no use ([ADR 0058](0058-a-socket-asks-again-and-its-pong-answers-from-the-bus.md)). A close ends nothing but the socket. |
| Realtime: One Channel per App | The provider may pause the socket while the app is hidden, and resume with a fresh ticket and a replay. | The portal's channel does ([ADR 0064](0064-a-hidden-tab-pauses-its-socket.md)). |

`make check` runs `arch-check` at v0.36.0, and it passes: 106 rules over
351 files, 5 findings accepted by an exception.

Three things keep a shape of their own.

## Decision

**A work row is done once its item is in the queue.** This is a
deviation from STO-20. The Storage Layer, Database Roles:

> The relay marks a row done only when its side effect happened: the
> destination row written and the publish taken by the bus, which
> `publish()` answers. A publish the bus dropped leaves the row
> pending, and the sweep relays it again. One rule holds for every kind
> of row that publishes, an entity change and a request for work alike.

Acme's relay marks a `work.<kind>` row done once `enqueue_relayed` has
landed the item, whatever became of its `work_available` wake. The item
is the effect. The wake is a hint: a worker polls every 5 seconds
(`worker_poll_seconds`) and claims the item without it. A row kept
pending would come back at the sweep's next pass, 10 to 40 seconds
later (its grace, then its 30-second pass), after the poll has found
the item. And the enqueue it runs again finds the item by its key and
publishes nothing, so keeping the row brings no wake back. The
guideline's worker scaffold has the same shape: `enqueue_relayed`
publishes inside the work manager and answers the item, not whether
the bus took the wake.

This holds until the guideline gives `enqueue_relayed` the wake's
answer, or until a worker's poll is slower than the sweep's retry.
Then the relay keeps a work row whose wake was dropped, as it keeps an
entity change.

**The two optional audits count as well as read.** Operations,
Operational Skills: "Both read the code and the settings, and nothing
else." `audit-credential-lifetimes` and `audit-provider-calls` read
them. They also count what a check or a call costs, through the
provider twins, on a database the run makes and drops, as
`audit-database-calls` does. Each still holds no credential, reads no
environment, calls no real provider, and writes to no shared database.
A count is a fact where a reading is an estimate, and each row of the
report says which it is.

**The recheck's interval is a realtime setting.** OpContext, Stages
names it `session_recheck_interval`. Here it is
`realtime_recheck_seconds` (`ACME_REALTIME_RECHECK_SECONDS`), beside
the realtime service's other bounds, 300 seconds by default. The
interval, its default, and the bound it states are the guideline's.
Only the name differs.

## Consequences

- ADR 0061 and ADR 0062 follow the guideline for what they decide about
  an entity change. ADR 0062's decision on a work row is the deviation
  above.
- A review that reads STO-20 on a work row, the two optional audits, or
  the recheck's setting cites this record. The relay cites it where it
  marks a work row done.
- A dropped wake costs a work item up to one poll, 5 seconds by
  default.
