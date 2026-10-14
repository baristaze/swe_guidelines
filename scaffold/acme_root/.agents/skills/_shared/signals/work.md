# Signals: work

The durable queue of background work: one item per thing to do, which
a worker claims, retries, and fails for good when its attempts run out
or its handler refuses it. An item's kind is the namespace's that asks
for it, and that namespace's file says what the kind does.

## Outcomes

- `work`: `dead_letter`, an item that failed for good: its attempts
  ran out (`worker`/`failed` beside it), or its handler was refused, a
  failure no retry changes (`worker`/`refused`, a provider refusing the
  call itself); `requeued`, one an operator sent back.

## Gauges

- `acme_work_oldest_ready_seconds`: how long the item ready longest
  has waited for a worker, on any lane.
- `acme_work_failed_recently`: the items that failed in the last
  fifteen minutes and are still failed.

## Alarms

- `acme-<env>-work-backlog`: `max(acme_work_oldest_ready_seconds)`
  above 600. The item ready longest waited ten minutes: no worker
  claims. With the maintenance service at its desired count, it is a
  worker that claims nothing: read its log for `claim failed`.
- `acme-<env>-work-dead-letter`: `max(acme_work_failed_recently)` at 1
  or more. An item failed for good in the last fifteen minutes.

## Log lines

- `work item <id> (<kind>) in org <org> failed for good: <reason>` at
  `ERROR` in `/acme/<env>/maintenance`, one per item failed for good.
  It is a finding with its id, its kind, its org, and its reason. The
  reason says whether it is the provider's or a person's to fix.
- `claim failed` in `/acme/<env>/maintenance`: the worker could not
  claim, so the backlog grows.

## Search

`claim failed`

## Change events

- `work.item.failed`: a work item that failed for good, in the org the
  item belongs to.

## Commands

- Once the cause of a failed item is fixed, a person sends it back with
  `uv run acme-ops work requeue --env <env> --org <org> <item>`. It
  signs them in with the second factor and mints a `write` token for
  that one call.

## Report

- Work: oldest ready item <age>, failed in the last fifteen minutes <n>, requeued <n>
- Failed work items: <item id, kind, org id, reason; or none>
