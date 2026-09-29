# ADR 0051: A refused key is unavailable; a refused request is refused

**Status**: accepted (2026-09-28)

## Context

A provider refuses a call for one of two reasons. It refuses the
process's own credential: a key revoked, or a key without the
permission the call needs. Or it refuses the request itself: a code
that is spent, an invitation it will not send, a parameter it calls
invalid.

The two need different answers. The first is not the call's fault. The
same call goes through once a person fixes the key, so work that made
it must wait, not fail. The second gets the same answer every time.
NET-33 says only a failure that can differ is retried, so work that
made it must fail at once.

## Decision

**Every provider client tells the two apart on every call.** One
translation reads the SDK's error by whose problem it is. The identity
provider's client shows the shape:

| The provider answers | Raised | Status, code |
|----------------------|--------|--------------|
| `invalid_client` on any call, or `401` or `403` on a deletion (the key) | `ProviderUnavailable` | `503`, `unavailable` |
| `429`, or a `5xx`, after the SDK's own retries | `ProviderUnavailable` | `503`, `unavailable` |
| No answer, or the request's deadline passed | `ProviderUnavailable` | `503`, `unavailable` |
| `422` (something stands in the way) | `ProviderConflict` | `409`, `provider_conflict` |
| Any other `4xx` (the request) | `ProviderRefused` | `400`, `provider_refused` |

On a sign-in call, a `401` or a `403` is the person's: the provider
refuses the flow, as for an address it has not verified. So there it
stays a refusal of the request. The refusal of the key names the
setting (`ACME_WORKOS_API_KEY`) and never its value, so the log says
which key to fix. A `404` on a deletion is not a refusal: the thing is
gone already, and a rerun is one deletion.

**Work reads the outcome by status.** The worker's `provider_calls`
reads every provider failure the same way. A `503` parks the item for a
minute, spending no attempt. A refusal of the request fails it for
good, with the refusal as its reason. Anything else is the queue's to
retry. `DELETE_ACCOUNT` and `DELETE_ORG` share this one reading.

## Consequences

A request under a refused key answers `503`, which a client reads as
"try again later", not as its own mistake.

An item under a refused key parks until a person fixes the key, and
then finishes. An item whose request the provider refused fails at
once, with the refusal as its reason, for an operator to read and
requeue (`acme-ops work requeue`).

A bootstrap command a person runs has its own translation and keeps
it: the person reads the refusal, which names the missing permission.

A new provider client holds to the same split.
