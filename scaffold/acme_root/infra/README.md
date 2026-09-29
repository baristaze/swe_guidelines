# Infrastructure

The capabilities the platform asks for and never implements itself:
cache, buckets, topics, queues, secrets, and observability. Each is an
interface with two implementations: a twin that runs on a laptop and
an implementation that runs in the cloud. The object model sees the
interface and nothing else, and infra imports nothing from the object
model.

## The capabilities

| Capability | What it promises | Local twin | In the cloud |
|------------|------------------|------------|--------------|
| Cache | Fast reads of data that is expensive to fetch, in named scopes so unrelated consumers never share a key; one atomic counter with a window, for rate limits; fails open | In-process memory, or Valkey in the compose stack | Valkey (ElastiCache), encrypted in transit |
| Buckets | Large blobs, every key prefixed with the tenant so one org cannot read or list another's; put, get, exists, a bounded list (lexical, `limit`, `after`), delete, a presigned download, and a presigned upload by form POST bounded by type and size; the URLs are signed for `ACME_S3_PRESIGN_ENDPOINT_URL` when the browser reaches the store at another address than the process does | In-process, or MinIO over the S3 API | S3, one private versioned bucket per member |
| Topics | Wake-ups and live updates: `work_available` and `entity_changed`; best effort, so a missed message costs latency and never work | In-process, or Valkey pub/sub | Valkey pub/sub |
| Queues | Work whose producer is outside the platform and cannot be told to wait (`webhooks`, and `slack`: what Slack sends, acknowledged); at least once, no deduplication, so the consumer is idempotent; depth is readable | In-process, or ElasticMQ over the SQS API | SQS, one queue and one dead-letter queue per member |
| Secrets | Get, has, put, delete by name; the object model holds a reference, never a value | The settings object, read once at boot from `.env` and the environment | Secrets Manager |
| Observability | Structured logs, Prometheus metrics, OpenTelemetry traces, and error reporting | Prometheus, Grafana, Jaeger, GlitchTip (the `devx` profile of the compose stack) | CloudWatch Logs and Metrics (namespace `Acme`, through a collector sidecar), X-Ray, Sentry |

Which twin a process uses is decided by the environment name: `local`
and `test` may use the in-process and compose backends; `dev`,
`staging`, and `production` refuse them; any other name is refused at
boot.

## What every capability holds to

- **A lifecycle.** The infra root opens every capability at boot
  (`start`) and closes it at shutdown, in reverse order. Nothing
  opens a client per call, and a call before `start` is refused.
- **A timeout on every client.** Every outbound client carries a
  timeout from settings: one for AWS, one for Valkey, one for the
  trace export. A test scans every source root and fails on a client
  built without one.
- **A request's deadline on the calls a request makes.** A queue's
  send, a secret's get, put, and delete, and an object's put, get, and
  exists take the request's `deadline`; the AWS impl cuts the call
  there, botocore's retries included, and it is unreachable (503). A
  worker's calls carry none. `acme.infra.deadline` is the one helper
  every client keeps to it with (ADR 0069).
- **One exception family.** Every driver error is translated into an
  infra exception with a status and a code, the same shape the
  platform's own exceptions have, so the gateway presents both alike.
  Unreachable is a 503, not found keeps its shape.
- **One breaker in front of Valkey.** The cache scopes and the topic
  publisher share one circuit breaker. It counts cost, not errors: a
  call that spends its whole timeout is a failure, a run of them opens
  the breaker, and while it is open a read is a miss, a write is
  dropped, a counter answers no count, and a publish is dropped. It
  answers the way a Valkey that is down answers, at once.
- **A publish says whether it landed.** A topic is best effort, so a
  publish never raises for the bus. It answers False when the bus
  refused it or the breaker is open, and a producer that must not lose
  the message keeps it and sends it again. The outbox relay does. Its four
  outcomes are counted (`opened`, `refused`, `probed`, `closed`), and
  they are what say why the cache and topic counters went quiet.
- **Payloads that only grow.** A topic payload gains only optional,
  defaulted fields, and a consumer ignores a field it does not know,
  so the two sides of a deploy roll out in either order.
- **Counted outcomes.** Cache hits and misses, queue sends, receives,
  deletes, and dead letters, topic publish failures and listener
  failures, all on the one outcome counter, each with a log line.

## Observability, in one paragraph

Every process names itself at boot, right after logging, so every log
line carries the service and the environment, the request id, and the
request that caused the work where a handoff named one. Metrics are
served on `/metrics` by every process. Traces are on when an endpoint
is set and off otherwise, with identical code paths. Error reporting is
on when a DSN is set, and reports unhandled exceptions and ERROR log
lines tagged with the service and the request id. The same four
signals exist locally and in the cloud, read through different tools,
which is what lets one test drive traffic and read every signal back
in either twin.
