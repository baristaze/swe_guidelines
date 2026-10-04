# ADR 0069: A request has a deadline its provider calls share

**Status**: accepted (2026-09-28)

## Context

Every call to a provider carries a timeout, 10 seconds by default, and
each SDK keeps its own retries. A provider that takes a call and never
answers holds it for the timeout on every attempt, plus the waits
between them:

| Provider | Attempts | A call gives up after, at most |
|----------|----------|--------------------------------|
| WorkOS | 4 | 50.5 seconds, and longer when a 429 or a 5xx asks for a longer wait: the SDK sleeps a `Retry-After` with no cap |
| AWS | 5 | 65 seconds |

A request that makes several calls waits for each in turn: a sign-in
through an org makes three WorkOS calls, and an org's first invitation
three.

Admission bounds how many requests are in flight, not how long one
stays. Every other bound sits outside the process and is shorter than
those worst cases: the portal, the command line, and the Python client
give up at 30 seconds; a draining task keeps a request 45 seconds (15 of
deregistration and 30 of stop timeout); the load balancer and the CDN
give up at 60. Past any of these nobody reads the answer, and the
request still holds its slot. The guideline asks the gateway to bound a
request with a deadline from settings (NET-26).

## Decision

**Admission gives a request its deadline.** When a request takes its
slot, `AdmissionMiddleware` stamps on the scope the instant its time
runs out: now plus `ACME_REQUEST_DEADLINE_SECONDS`. The gateway mints
the request stage with it, and every stage refines that one, so
`ctx.deadline` is there wherever a request's work is. A socket and the
operational routes get none. Neither does a worker's stage: an item is
bounded by its lease.

**It is one instant for the request, never a deadline per call, and it
rides the context.** Calls made one after another share what is left. A
deadline per call bounds one call, not three in turn. The guideline
allows one context variable, the request id for the logs (CTX-07), so
the deadline is a field of `RequestContext`, and a manager hands it to
each call as an argument. A call takes a deadline exactly when a
request can make it: the identity provider's sign-ins, organizations,
invitations, and admin portal link; and AWS's queue send, secret reads
and writes, and object put, get, and head. A test scans the managers
and the API's services for such a call without one.

**Each client keeps to it** (`acme.infra.deadline.bounded`):

- A call that starts with no time left does not start.
- A call still waiting at the deadline is cut there: an attempt in
  flight, the wait between two attempts, or a wait the provider asked
  for.
- WorkOS sends each attempt with the smaller of the timeout and what is
  left, because its SDK sleeps a `Retry-After` with no cap. An answer
  whose `Retry-After` asks for longer than what is left ends the call at
  once. The SDK keeps its retries, its backoff, and its idempotency
  keys, never fewer retries: a retry that fits in the deadline still
  helps, and fewer of them leave a request of several calls unbounded.
  A thin transport under the process's own HTTP client keeps to the
  deadline.
- AWS takes its timeout per client, not per call, so the cut at the
  deadline bounds the attempt in flight.

**The refusal is the one a provider that does not answer already
gets.** WorkOS raises `ProviderUnavailable` and AWS
`BackendUnreachable`; both are `503 unavailable`, which the portal and
the command line read as "try again". A sign-in through an org that
runs out of time while it reads the org's invitations still signs the
person in and joins nothing, as when WorkOS is down.

**The default is 20 seconds.** A healthy provider answers in well under
a second, and the longest request, a sign-in through an org, makes
three calls. 20 seconds sits under every bound outside the process, and
leaves 10 of the clients' 30 for the refusal and the database work
around the calls. It is one setting of the API, and the default serves
every environment.

**The database keeps its own bound.** Every statement carries its 10
second deadline and every checkout its bound, from the pool's settings.
The outbox relay after the answer holds the slot too, and calls no
provider. The handler is never cancelled from outside: a cancel lands
at any `await`, between a provider's side effect and the row that
records it, and those bounds already hold everything else a request
waits on.

## Worst case per route

With every provider hanging, at the default timeouts:

| Route | Calls in turn | Without a deadline | With it |
|-------|---------------|--------------------|---------|
| `POST /v1/auth/callback` | WorkOS exchange; through an org, the organization and the invitation lists | 50.5 s; 151.5 s and 50.5 per further page | 20 s |
| `POST /v1/auth/device`, `/auth/device/token` | WorkOS start, or poll and the callback's org steps | 50.5 s; 151.5 s | 20 s |
| `POST /v1/invitations` | WorkOS organization, its creation, the send; on a conflict the pending list | 151.5 s; 202 s | 20 s |
| `POST /v1/invitations/{id}/resend`, `DELETE /v1/invitations/{id}` | WorkOS resend or revoke | 50.5 s | 20 s |
| `POST /v1/orgs/current/sso-link` | WorkOS organization (the first link), the portal link | 151.5 s | 20 s |
| `POST /webhooks/identity` | SQS send (and the queue's address, the first per process) | 130 s | 20 s |
| `PUT` and `GET /v1/media/files/{id}/content`, `POST .../confirm` | S3 put, get, or head | 65 s | 20 s |

Without the deadline, every one is past the 30 seconds a client waits.

## Consequences

- A provider that hangs costs a request 20 seconds, and the caller reads
  `503 unavailable`, not a 504 from the load balancer or its own
  timeout.
- A method a request can call takes `deadline` in each interface and
  each impl, the twins and the local stand-ins included, which have
  nothing to wait for.
- A worker's calls carry none. The worker still sleeps a WorkOS
  `Retry-After` with no cap while it renews its lease.
- The webhook route takes the request stage, so the OpenAPI document
  names its `x-app` and `x-app-version` headers.
