# ADR 0038: A dead column leaves the mapping one release before it leaves the table

**Status**: accepted (2026-09-25). Supersedes when the dead columns are
dropped in [ADR 0026](0026-what-0-31-0-leaves-as-a-choice.md) (the
last paragraph), [ADR 0028](0028-sign-in-is-the-identity-providers.md)
("Acme keeps no password"), and
[ADR 0034](0034-a-task-is-due-on-a-date.md) (the contract half); the
rest of each record stands. The second step is done (2026-09-25):
migration 202609290000 drops the four columns this record names.

## Context

A column moves by expand and contract. A migration runs before the
services roll, so the release before serves the new schema for the
minutes of the roll, and again after a rollback. A column may leave the
table only when no statement of the release before names it.

Three records planned the drop for the release after the one that
stopped reading. Each release deferred its dead columns: no `SELECT`
names a deferred column. `tasks.remind_at` was also still written on
purpose, for the fast rollback.

A deferred column is still mapped, and the mapper names every mapped
column in the insert it emits. A column the object has no value for is
sent as `NULL`, and one with a server default is read back in the
insert's `RETURNING`. So the release before names
`identities.password_hash`, `identities.failed_sign_ins`, and
`identities.last_failed_sign_in_at` in every identity it inserts, and
`tasks.remind_at` in every task it inserts or updates.

The release that runs on staging shows it. Its integration tests, run
against a schema with the three identity columns dropped, fail every
identity insert with `column "password_hash" of relation "identities"
does not exist`. A drop now would fail every first sign-in, which is a
sign-up, and every task write, for the minutes of the roll. It would
fail them for good if the circuit breaker rolled the deploy back.

## Decision

**A dead column leaves in two releases.** The first takes it out of the
mapping and keeps it in the table: the column is declared in the
table's `__table_args__` and named in the mapper's
`exclude_properties`. No statement names it, and the schema check still
compares it. The second drops it with a migration, and the two lines go.

**The data goes as early as it can.** A value nothing reads goes in the
first release when it is a secret. The password hashes are cleared now,
with the code-linked Slack channel's two tables, which no statement of
the release before names.

**The proof is the release before's own tests.** Before a contract
lands, the release before runs its integration tests against the
contracted schema. A column any of them names stays one more release.

## Consequences

This release takes `password_hash`, `failed_sign_ins`,
`last_failed_sign_in_at`, and `remind_at` out of the mapping, and stops
writing `remind_at`. The next release drops the four columns. Its
release before is this one, which names none of them. This release's
own tests, run against a schema with the four dropped, pass.

After a fast rollback to the release before, a task it writes gets a
`remind_at` again, which nothing of this release reads.

A new dead column follows the same two steps. Deferring it is not a
step: that keeps it out of the reads, and every insert still names it.
