# ADR 0066: The billing account is cached under a generation

**Status**: accepted (2026-09-26).

## Context

Every lever reads the org's billing account to know its plan. A new task
reads it, and so does an invitation, a key, and a reopened task. The
billing page reads it beside three usage counts. Each read is its own
transaction on `core`: `BEGIN`, the tenant scope, one `SELECT`, and
`COMMIT`.

The account changes rarely. It changes when the processor sends a
delivery, when a checkout makes the customer, on a cancellation and its
undo, on a seat count, on a grant, and when the account is closed at the
processor. Each of those is a method of a billing manager.

An api key's principal is read in one statement that joins the account,
so the key's own plan check reads nothing more. The handler behind it
still asked for the plan again, so a key's `POST /v1/tasks` read the
account twice.

The guideline puts caching in the manager (Caching is a business-layer
concern). A read cache is a projection with a generation. The TTL is a
backstop and the bound on staleness. The cache fails open, and a cached
read sits below authorization.

## Decision

**The billing manager caches the account per org.** It takes a new
cache scope, `billing_account`, through its constructor, like the
tenancy manager takes `realtime_ticket`. It does not wrap its storage.
The logic is in `om/src/acme/om/billing/impl/cache.py`.

- **The key carries the org's generation.** A read gets the generation,
  then the entry `account:<generation>`. A miss reads storage and puts
  the account, or its absence, under that key. The generation is read
  before storage, so an account read before a write and put after its
  bump lands under a generation nobody reads any more.
- **Every write bumps the generation after its commit.** One
  `increment`, in `_write`, in the two creates, in `apply_delivery` when
  its commit lands, in `purge_tenant`, and in the operator plane's
  `comp_plan`. The operator plane takes the same scope for that bump
  alone; its own reads stay on storage.
- **The TTL is a setting.** `ACME_BILLING_ACCOUNT_CACHE_SECONDS`, 60 by
  default and an hour at most, read by the API and the worker alike. A
  bump that is lost leaves the old entry readable for that long. The
  generation counter lives a day, far longer than any entry. A counter
  that ends and starts over finds no entry it could revive past the TTL.
- **Only the reads that answer the plan use it**: `get_entitlements` and
  `get_billing`. Each checks the caller's permission first, on every
  call. A write reads the account from storage before it changes it, and
  so do the checkout and the portal.
- **It fails open.** A miss, an entry another build wrote in a shape this
  one cannot read, and a Valkey that cannot be reached are each a read of
  storage.
- **A counter reads back through `get`.** Valkey keeps an `INCR` counter
  as a string, so `get` answers it. The memory impl now keeps counters in
  the same map as values, so it answers the same way, and a contract
  test holds both to it.

**The usage counts stay fresh.** Members, files, and active tasks are
other managers' rows, and a bound is enforced on an exact count.

**The key's plan check is unchanged.** It still reads the account in the
principal's statement and decides with `entitlements_of`.

**The double read on `POST /v1/tasks` goes through the cache.** The
other way was to carry the account the principal read along to the
handler. That helps an api key only, since a session's principal does
not join the account, and it puts the account on the context every
manager takes. The cache removes the handler's read for both
credentials, with no new parameter.

## Measured

`ops/audit/dbcalls.py` on the local stack, warm round trips, before and
after, with the built-in flows and a flows file of the run's own:

| Call | Before | After |
|------|--------|-------|
| `POST /v1/tasks`, session, cache warm | 25 trips, 8 transactions | 22, 7 |
| `POST /v1/tasks`, api key, cache warm | 25, 8 | 22, 7 |
| `POST /v1/tasks`, the first after a write | 25, 8 | 25, 8 |
| `GET /v1/billing`, cache warm | 18, 6 | 15, 5 |
| `POST /v1/billing/checkout` | 32, 10 | 32, 10 |
| `GET /v1/billing`, the first after a checkout | 18, 6 | 18, 6 |

A read that hits costs two Valkey round trips: the generation and the
entry.

## Alternatives

- **Invalidate one key per org instead of a generation.** It works while
  the account is the only entry. A generation is what the guideline
  names, and it orphans whatever else this scope holds for the org
  later.
- **Carry the principal's account to the handler.** Rejected above.
- **Cache the plan instead of the account.** The plan depends on the
  clock: a subscription set to end carries its plan until the period's
  end. The account is what does not change until a write.

## Consequences

- A plan read is at most a minute stale, and only when a bump is lost.
  A lever may then let one more task in on a plan that just ended, or
  refuse one on a plan that just began. Both are levers, never fences.
- Valkey holds one small entry and one counter per active org.
- A new writer of the account must bump the generation after its commit,
  or its reads are stale for a minute. A test that writes the account in
  storage does the same (`account_written` in the API tests' support).
