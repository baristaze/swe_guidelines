# Signals: maintenance

The maintenance worker: it claims work items and runs their handlers,
sweeps on every pass, and consumes what providers deliver.

## Outcomes

- `worker`: one per item a handler settled: `done`; `failed`, an
  attempt the handler failed, retried until the item's attempts run
  out; `refused`, an item failed for good at once, since no retry
  changes the answer; `parked`, an item that waits on a provider. Then
  `claim_error`, a claim that failed, and `lease_lost`, an item whose
  lease passed to another claim before it settled, never written over.
- `deliveries`: one per delivery the consumer handled: `applied`,
  `duplicate`, `unowned` (it names no org this environment holds),
  `malformed`, `failed`, `unknown_provider`, and `receive_failed` (the
  queue could not be read). A `failed` or an `unknown_provider` stays on
  the queue and comes back.
- `queue`: `sent` by the API, `received` and `deleted` by the
  consumer.

## Queues

- `acme-<env>-webhooks`: what providers deliver, which the API checked
  at its webhook route and queued. The deliveries come in through
  `/acme/<env>/api`, and their handling is in `/acme/<env>/maintenance`.
  A message in `acme-<env>-webhooks-dead` is a delivery the worker
  could not handle after its retries.

## Log lines

- `delivery <id> failed on receive <n>` at `ERROR` in
  `/acme/<env>/maintenance`: one delivery the consumer could not apply,
  left on the queue to come back, with the exception under it.

## Report

```markdown
- Workers: <worker outcomes>
- Queue: webhooks <n> (dead <n>), deliveries <outcomes>
```
