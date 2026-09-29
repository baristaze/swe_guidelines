# ADR 0051: A refused key is unavailable; a refused request is refused

**Status**: accepted (2026-09-26)

## Context

A provider refuses a call for one of two reasons. It refuses the
process's own credential: a key revoked (Stripe answers `401`), or a key
without the permission the call needs (`403`). Or it refuses the request
itself: a price it does not know, a card it declined, a parameter it
calls invalid (`400`, `402`, `404`).

The two need different answers. The first is not the call's fault. The
same call goes through once a person fixes the key, so work that made it
must wait, not fail. The second gets the same answer every time. NET-33
says only a failure that can differ is retried, so work that made it
must fail at once.

The WorkOS client already reads a refused key as unavailable. The Stripe
client read both as one `PaymentsRefused` (`502`), except on the way to
ending a deleted account, where a rule of its own told them apart. So
`SYNC_SEATS` could not tell them apart: it either failed for good on a
key a person could fix, or retried a request that could never pass.

## Decision

**The Stripe client tells the two apart on every call.** One translation
reads the SDK's error by whose problem it is:

| Stripe answers | Raised | Status, code |
|----------------|--------|--------------|
| `401`, `403` (the key) | `PaymentsKeyRefused`, a `ProviderUnavailable` | `503`, `payments_key_refused` |
| `429`, after the SDK's own retries | `ProviderUnavailable` | `503`, `unavailable` |
| No answer | `BackendUnreachable` | `503`, `unavailable` |
| `400`, `402`, `404`, an idempotency key reused (the request) | `PaymentsRefused` | `502`, `payments_refused` |
| `5xx`, anything else | `BackendFailed` | `500`, `backend_failed` |

A refused key is logged at error, naming the call, so the log says which
permission to add.

**Work reads the outcome by status.** A `503` parks the item for a
minute, spending no attempt. A refusal of the request fails it for good.
Anything else is the queue's to retry. `SYNC_SEATS` and the account and
org deletions share this one reading.

## Consequences

A checkout, the portal, a cancel, or a resume under a refused key
answers `503` `payments_key_refused`, not `502` `payments_refused`. The
portal says billing is unavailable right now, with the request's
reference. A throttle answers `503` `unavailable`, not `500`
`backend_failed`. A refusal of the request answers `502` as before. The
billing read asks Stripe nothing and is unchanged. The webhook consumer
leaves a delivery on the queue for any failure, as before.

A `SYNC_SEATS` item under a refused key parks until a person fixes the
key, and then brings the seat count up to date. One whose request
Stripe refused fails at once, with the refusal as its reason, for an
operator to read and requeue.

The bootstrap has its own translation and keeps it. A person runs it
and reads the refusal, which names the missing permission.

A new provider client holds to the same split.
