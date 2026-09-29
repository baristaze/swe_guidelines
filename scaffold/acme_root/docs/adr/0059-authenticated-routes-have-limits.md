# ADR 0059: Authenticated routes have limits

**Status**: accepted (2026-09-26)

## Context

The `rate_limit` cache counts one thing: sign-ins, per client address,
over a 60 second window. Every other route has no limit. A session or an
API key can call as fast as it likes, and every call looks its
credential up in Postgres. An integration that polls with a key pays
that lookup on every poll, and nothing bounds how often it polls.

A bearer that is unknown, expired, or revoked costs a lookup too: one
transaction on the system login's pool, three round trips. Nothing
counts those failures. An address can send a stream of dead tokens and
every one of them reaches the database.

The guideline's rate limit is a dependency on the shared counter. Its
subject is the credential id, or the client address where there is no
credential. A rejection is a 429 with `Retry-After` in the error
envelope, and the limits fail open (NET-08).

## Decision

**Each credential has a budget of its own.** Every session and API key
spends one unit per request, once the credential resolves. Reads (`GET`,
`HEAD`) and writes have separate budgets, split the way admission
splits its lanes. The key is the credential id under the credential's
tenant (`reads:cred:<id>`), counted with the cache's one increment, so
the check is one Valkey round trip. A sign-in or operator credential
has no tenant and counts under the system scope. The defaults are
3,000 reads and 1,200 writes a minute. That is sized for the busiest
honest client: a tab loading a page and following its hints, or a
traffic run's stress profile at its fastest pace.

The check lives in the auth dependency, after the manager's call. The
dependency's signature does not change, and neither does the manager.
`rate_limited(route)` stays the per-route dependency for the sign-in
routes. Its `authenticated` option goes: an authenticated route already
spends its credential's budget.

**Each address has a budget of failed authentications.** A lookup that
fails as `NotAuthenticated` (unknown, expired, revoked) answers 401 and
counts one against the client address. A request with no bearer at all
looks nothing up and counts nothing. The default is 1,000 failures a
minute. One address is often a crowd, and a crowd back from a weekend
sends one expired session per open call of each tab.

Once the budget is spent, every request from that address answers 429
until the window ends, a live credential's included. It is refused
before the lookup, so it costs no database round trip.

**The refusal is remembered in the process.** The shared count decides:
the increment that reaches the budget returns the time left on the
window, and the process keeps that address and that time. The next
request from the address is refused from memory. A shared read before
every lookup would add a Valkey round trip to every good request, to
catch a case that is rare. A replica that has not seen the address yet
learns it from its own next failure. So past the budget, at most one
failure per replica reaches the database per window. An entry is added
only past a spent budget and dropped when its window ends.

**The address is the one the proxy handling settles.** The failure
budget keys on `request.client`, as the sign-in budget does. uvicorn
takes it from `X-Forwarded-For` only when the peer is a trusted proxy
(`ACME_TRUSTED_PROXIES`, the load balancer's block). The edge
middleware moves it one hop further in only beside the edge's secret
(`X-Acme-Edge`). A header an untrusted peer sends is never read, so a
caller cannot choose its own address, or spend another's budget.

**Both limits fail open.** When Valkey is down or the breaker is open,
the increment answers "no count", and the request goes on.

- The credential budget. A cache outage must not become an API
  outage. The credential already proved itself, and admission still
  bounds what the process takes in.
- The failure budget. With no count there is nothing to refuse on. A
  dead-token flood that starts during an outage reaches the database
  at one transaction per request, as it did before this record, and
  admission bounds it. An address already refused stays refused until
  its window ends, because that refusal is in the process's memory.

Failing closed would refuse every authenticated request while the
cache is down. NET-08 names that as a violation, and here it would be
the worse outage.

## Consequences

Every authenticated request pays one Valkey round trip. The database
cost of a good request does not change.

A flood of dead tokens stops reaching the database once its address
has spent the budget. Measured with `ops/audit/dbcalls.py` over a local
database, 100 requests with a signed-out session and 100 with an
unknown, well-formed token, from one address:

| | Signed-out session | Unknown token |
|---|---|---|
| Before | 100 transactions, 300 round trips | 100 transactions, 300 round trips |
| After, budget 10 | 10 transactions, 30 round trips; 90 answered 429 | 0; all 100 answered 429 |

At the default budget of 1,000 a minute, an address can make at most
1,000 lookups fail each minute (3,000 round trips). Every request after
that costs none.

A live person behind an address that is spraying tokens is refused
with it until the window ends. That is the cost of any per-address
budget, and the reason it is generous.

The refusal is the one `rate_limited` envelope. Its message says
which budget was spent: `rate limit exceeded` for a credential,
`too many failed authentications from this address` for an address.
The outcome counter's `rate_limit` subsystem adds `authentication_failed`
and `address_refused` beside `allowed` and `rejected`.

The socket's ticket is not counted here. A ticket is single use, lives
60 seconds, and is minted by a route that spends its credential's
budget.

The limits are fairness, not a flood defence. There is no web firewall
([ADR 0024](0024-what-staging-hands-production-is-recorded-and-verified.md));
admission is what bounds a process under a flood.
