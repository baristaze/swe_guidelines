# ADR 0053: The scope rides in the message that begins

**Status**: accepted (2026-09-26). Deviates from the example of The
Second Fence, which sets the scope with bind parameters. The rule the
example serves stands: the settings are set before the first statement
and die with the transaction.

## Context

The guideline's funnel sets the scope of every transaction before its
first statement runs (The Second Fence):

> It sets three transaction settings before the first statement runs,
> and a setting the call does not name stays unset:
>
> ``` sql
> SELECT set_config('app.org_id', :org_id, true);
> ```
>
> The funnel calls `set_config` rather than `SET LOCAL`, because
> `SET LOCAL` takes no bind parameters.

Done that way, the scope is a statement of its own, and every
transaction pays three round trips before any work: `BEGIN`, the
scope, and `COMMIT` or `ROLLBACK`. A read of one statement is four
round trips, and three of them are overhead. A plain authenticated
read makes three such transactions, so it was 14 round trips.

The driver begins a transaction with one simple query, `BEGIN;`. A
simple query may carry several statements, and the server runs them in
order. It carries no bind parameter.

Flows also open several read transactions in a row on one role. Some
of those reads belong to one namespace: the replay's page and its
floor, the operator's counts of orgs and users, and the count and the
top place of one more open task under a plan's bound. Others belong to
several namespaces: a create reads the plan (billing), the top place
(tasks), and the Slack installation (slack); `GET /v1/billing` reads
the account, the members, the media usage, and the open tasks, each
through its own namespace.

## Decision

**The scope goes out with `BEGIN`, as one message.** The funnel sends
`BEGIN; SELECT set_config('app.org_id', '<org>', true), ...;`. The
settings are still `set_config(..., true)`, set inside the transaction
before anything else runs, and they die with it. Every pooled
connection is a `ScopedConnection`, which appends the scope to the next
`BEGIN` and refuses it on any other statement.

**The values are written in, and only a UUID's canonical text is.** A
message of two statements takes no bind parameter. Each value is a
`UUID`, and its text must match eight, four, four, four, and twelve
lower-case hex digits, or nothing is sent. The setting names are
constants of the funnel. Nothing a caller spells reaches the statement.

**The funnel begins the transaction itself.** The driver adapter begins
lazily, on the first statement. The funnel begins it at once, through
the adapter's `_start_transaction`, so a connection the server closed
is met at the message that begins, which writes nothing. The connection
is dropped and the transaction begins on the next one, until a live or
a new connection answers. A new connection that fails to begin fails
the call. `_start_transaction` is SQLAlchemy's private method: the lock
file pins the version, and a unit test fails when it moves.

**Reads of one namespace on one role share a transaction, in a storage
method of their own.** `read_page` reads the page, the floor, and the
head. `count_orgs_and_users` counts both in one statement.
`count_open_and_read_places` reads the count a plan's bound is held to
and the top place.

**Reads of several namespaces keep a transaction each.** STO-02 has no
transaction outlive a storage call and no manager wrap several storage
calls in one. A namespace reads another's rows through that one's
storage interface, never its table. Sharing a transaction across them
needs the unit of work STO-02 names as the violation. That is not taken
here.

## Consequences

Every transaction costs two round trips before its work, not three. A
read of one statement is three round trips. Across the database-call
harness, every flow it runs went from 14,734 round trips to 11,504
(22 % fewer), with the same 3,230 transactions.

| Flow | Round trips | Transactions |
|---|---|---|
| A plain authenticated read (`GET /v1/tasks/{id}`) | 14 → 11 | 3 → 3 |
| `POST /v1/tasks` | 35 → 27 | 8 → 8 |
| `POST /v1/tasks` on Free | 40 → 31 | 9 → 9 |
| `GET /v1/billing` | 26 → 20 | 6 → 6 |
| `GET /v1/admin/size` | 24 → 18 | 6 → 6 |
| `GET /v1/events` | 15 → 12 | 3 → 3 |

The scope's statement is no longer prepared, so it takes no place in a
connection's statement cache.

A restart that closes every pooled connection costs each one a failed
message, once, and each is dropped as it is met. The pool is not
invalidated as a whole.

`set_scope` stays for the one case a transaction moves to a second
tenant: the scope is set again as a statement of its own, with bind
parameters.

A create still reads the plan, the top place, and the Slack
installation in three transactions, and `GET /v1/billing` still makes
four reads. Folding them is a decision about STO-02, not about the
funnel.
