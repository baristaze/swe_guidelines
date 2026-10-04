# ADR 0059: Authenticated routes have limits

**Status**: accepted (2026-09-28)

## Context

A session or an API key that calls as fast as it likes costs a lookup
of its credential in Postgres on every call, and so does every poll of
an integration that polls with a key.

A bearer that is unknown, expired, or revoked costs a lookup too: one
transaction on the system login's pool, three round trips. An address
can send a stream of dead tokens, and every one reaches the database
unless something counts the failures.

The guideline's rate limit is a dependency on the shared counter
(NET-08). Its subject is the credential id, or the client address where
there is no credential. A rejection is a 429 with `Retry-After` in the
error envelope, and the limits fail open.

## Decision

**Each credential has a budget of its own.** Every session and API key
spends one unit per request, once the credential resolves. Reads (`GET`,
`HEAD`) and writes have separate budgets, split the way admission splits
its lanes. The key is the credential id under the credential's tenant
(`reads:cred:<id>`), counted with the cache's one increment, so the
check is one Valkey round trip. A sign-in or operator credential has no
tenant and counts under the system scope. The defaults are 3,000 reads
and 1,200 writes a minute (`ACME_CREDENTIAL_RATE_LIMIT_READS`,
`ACME_CREDENTIAL_RATE_LIMIT_WRITES`), sized for the busiest honest
client: a tab loading a page and following its hints, or a traffic run's
stress profile at its fastest pace.

The check lives in the auth dependency, after the manager's call. The
sign-in routes keep the per-route dependency, `rate_limited(route)`. An
authenticated route spends its credential's budget instead.

**Each address has a budget of failed authentications.** A lookup that
fails as `NotAuthenticated` (unknown, expired, revoked) answers 401 and
counts one against the client address. A request with no bearer looks
nothing up and counts nothing. The default is 1,000 failures a minute
(`ACME_FAILED_AUTHENTICATION_LIMIT`). One address is often a crowd, and
a crowd back from a weekend sends one expired session per open call of
each tab.

Once the budget is spent, every request from that address answers 429
until the window ends, a live credential's included. It is refused
before the lookup, so it costs no database round trip.

**The refusal is remembered in the process.** The shared count decides:
the increment that reaches the budget returns the time left on the
window, and the process keeps that address and that time. The next
request from the address is refused from memory. A shared read before
every lookup would add a Valkey round trip to every good request, to
catch a rare case. A replica that has not seen the address yet learns
it from its own next failure, so past the budget at most one failure
per replica reaches the database per window.

**The address is the one the proxy handling settles.** The failure
budget keys on `request.client`, as the sign-in budget does. uvicorn
takes it from `X-Forwarded-For` only when the peer is a trusted proxy
(`ACME_TRUSTED_PROXIES`, the load balancer's block). The edge middleware
moves it one hop further in only beside the edge's secret
(`X-Acme-Edge`). A header an untrusted peer sends is never read, so a
caller cannot choose its own address, or spend another's budget.

**Both limits fail open.** When Valkey is down or the breaker is open,
the increment answers "no count", and the request goes on.

- The credential budget: a cache outage must not become an API outage.
  The credential already proved itself, and admission still bounds what
  the process takes in.
- The failure budget: with no count there is nothing to refuse on. A
  dead-token flood during an outage reaches the database at one
  transaction per request, and admission bounds it. An address already
  refused stays refused until its window ends, since that refusal is in
  the process's memory.

Failing closed would refuse every authenticated request while the cache
is down. NET-08 names that as a violation, and here it would be the
worse outage.

## Consequences

Every authenticated request pays one Valkey round trip. A good
request's database cost stays the same.

A flood of dead tokens stops reaching the database once its address has
spent the budget. Measured with `ops/audit/dbcalls.py` over a local
database, 100 requests with a signed-out session and 100 with an
unknown, well-formed token, from one address, with a budget of 10:

| | Signed-out session | Unknown token |
|---|---|---|
| No failure budget | 100 transactions, 300 round trips | 100 transactions, 300 round trips |
| Budget of 10 | 10 transactions, 30 round trips; 90 answered 429 | 0; all 100 answered 429 |

At the default budget, an address can make at most 1,000 lookups fail
each minute (3,000 round trips). Every request after that costs none.

A live person behind an address that is spraying tokens is refused with
it until the window ends. That is the cost of any per-address budget,
and the reason it is generous.

The refusal is the one `rate_limited` envelope. Its message says which
budget was spent: `rate limit exceeded` for a credential, `too many
failed authentications from this address` for an address. The outcome
counter's `rate_limit` subsystem counts `authentication_failed` and
`address_refused` beside `allowed` and `rejected`.

The socket's ticket is not counted here. A ticket is single use, lives
60 seconds, and is minted by a route that spends its credential's
budget.

The limits are fairness, not a flood defence. There is no web firewall
([ADR 0024](0024-what-staging-hands-production-is-recorded-and-verified.md));
admission is what bounds a process under a flood.
