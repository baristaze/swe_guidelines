# Signals: outbox

The rows a write lands beside itself, which the relay turns into a
change event and a push, or into a work item, and retries until it
fails for good.

## Outcomes

- `outbox`: `relayed`; `relay_failed`, an attempt that failed, which
  the sweep tries again; `publish_failed`, a change event the bus
  dropped, which stays pending; `dead_letter`, a row failed for good;
  `purged`, relayed rows the sweep deleted past their retention.

## Gauges

- `acme_outbox_oldest_pending_seconds`: how long ago the oldest row
  that is neither relayed nor failed landed.
- `acme_outbox_failed_recently`: the rows that failed for good in the
  last fifteen minutes.

## Alarms

- `acme-<env>-outbox-lag`: `max(acme_outbox_oldest_pending_seconds)`
  above 300. The oldest row not yet relayed landed five minutes ago: a
  relay that keeps failing.
- `acme-<env>-outbox-dead-letter`: `max(acme_outbox_failed_recently)`
  at 1 or more. A row failed for good in the last fifteen minutes.

## Log lines

- `outbox relay of <row id> (<kind>) failed on attempt <n>: <error>`
  at `WARNING` in `/acme/<env>/maintenance`: one attempt of a relay
  that keeps failing, the cause of an outbox lag.
- `outbox row <row id> (<kind>) failed for good after <n> attempts:
  <error>` at `ERROR` in `/acme/<env>/maintenance`: the last of those
  attempts, an outbox dead letter. The org's stream holds an
  `outbox.row.failed` change event that names the row's kind and
  target.

## Search

`outbox relay of`

## Change events

- `outbox.row.failed`: an outbox row that failed for good, naming the
  row's kind and target.

## Report

- Outbox: oldest pending row <age>, failed for good in the last fifteen minutes <n>, dead letters <row id, kind, org id; or none>
