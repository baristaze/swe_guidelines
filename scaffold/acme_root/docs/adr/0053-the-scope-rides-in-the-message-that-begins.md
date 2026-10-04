# ADR 0053: The scope rides in the message that begins

**Status**: accepted (2026-09-28)

## Context

The guideline's funnel (The Second Fence) sets three transaction
settings before the first statement runs, and a setting the call does
not name stays unset. Its example sends each as
`SELECT set_config('app.org_id', :org_id, true)`, since `SET LOCAL`
takes no bind parameters.

Done that way, the scope is a statement of its own, and every
transaction pays three round trips before any work: `BEGIN`, the
scope, and `COMMIT` or `ROLLBACK`. A read of one statement is four
round trips, three of them overhead.

The driver begins a transaction with one simple query, `BEGIN;`. A
simple query may carry several statements, which the server runs in
order, and it carries no bind parameter.

## Decision

This deviates from the example of The Second Fence, which sets the
scope with bind parameters. The rule the example serves stands: the
settings are set before the first statement and die with the
transaction.

**The scope goes out with `BEGIN`, as one message.** The funnel sends
`BEGIN; SELECT set_config('app.org_id', '<org>', true), ...;`. The
settings are `set_config(..., true)`, set inside the transaction before
anything else runs. Every pooled connection is a `ScopedConnection`,
which appends the scope to the next `BEGIN` and refuses it on any other
statement.

**The values are written in, and only a UUID's canonical text is.** A
message of two statements takes no bind parameter. Each value is a
`UUID`, and its text must match eight, four, four, four, and twelve
lower-case hex digits, or nothing is sent. The setting names are
constants of the funnel. Nothing a caller spells reaches the statement.

**The funnel begins the transaction itself.** The driver adapter begins
lazily, on the first statement. The funnel begins at once, through the
adapter's `_start_transaction`, so a connection the server closed is
met at the message that begins, which writes nothing. That connection
is dropped and the transaction begins on the next, until a live or a
new connection answers. A new connection that fails to begin fails the
call. `_start_transaction` is SQLAlchemy's private method: the lock
file pins the version, and a unit test fails when it moves.

**Reads of one namespace on one role share a transaction, in a storage
method of their own.** `read_page` reads the stream's page, its floor,
and its head. `count_orgs_and_users` counts both in one statement.

**Reads of several namespaces keep a transaction each.** STO-02 lets no
transaction outlive a storage call and no manager wrap several storage
calls in one. A namespace reads another's rows through that one's
storage interface, never its table, so a transaction shared across them
is the unit of work STO-02 names as the violation.

## Consequences

Every transaction costs two round trips before its work, not three, and
a read of one statement costs three.

The scope's statement is not prepared, so it takes no place in a
connection's statement cache.

A restart that closes every pooled connection costs each one a failed
message, once, and each is dropped as it is met. The pool is not
invalidated as a whole.

`set_scope` stays for the one case a transaction moves to a second
tenant: the scope is set again as a statement of its own, with bind
parameters.
