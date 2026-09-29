# ADR 0009: Tasks carry a version, and every write names the one it read

**Status**: accepted (2026-09-19), amended (2026-09-22): a mismatch
is 412 `precondition_failed`, and the version comes in `If-Match` or
`expected_version`; see the note at the end.

## Context

The guideline (Shape of an Operation) makes optimistic concurrency
opt-in: last writer wins by default, and an entity whose concurrent
edits matter carries a `version`, the copy increments it, and the write
is a compare-and-set that raises `Conflict` when the row moved. Until
now this repository recorded the default as its choice: no table
carried a `version`, because nothing in Acme had concurrent edits that
matter.

That was wrong for tasks. A task is edited from two windows and two
terminals at once, by design: the portal's drag reorder, the CLI's
`done` and `rm`, and the realtime demo all write the same rows from
different places. The manager's copy on update starts from the stored
row, which keeps the provenance honest, but nothing conditioned the
write: between the read and the upsert another writer could land (a
delete, a move), and the snapshot overwrote it. An edit that raced a
delete brought the task back.

## Decision

`Task` carries `version`. The manager's copy increments it on update,
move, and soft delete, and the storage write is a compare-and-set:
`WHERE version = :expected` in one statement in Postgres, the same
check and write under the lock in the memory impl, raising
`VersionMismatch`, a `Conflict` with the stable code `version_mismatch`,
when the row is at another version or is gone. The update never inserts
a missing row; the create primitive is the only way in. The migration
expands only: the column arrives with a default of 1, every existing
row is at version 1, and the default keeps a build that predates the
column inserting during the rollout.

On the wire, `TaskView` shows `version`, and every write names the one
the caller read: `UpdateTaskRequest` and `MoveTaskRequest` carry it as
a required field, and the DELETE carries it as a required `version`
query parameter. A DELETE has no body, and a header would be the
natural place for a precondition, but `If-Match` means an entity tag
and asks for 412 and an `ETag` on every response to be used honestly.
The version is one integer, the same precondition the other two writes
carry in their bodies; on the query string it is typed as a required
parameter by both generated client type sets, needs no header plumbing
in either transport client, and is refused with the same 409 and the
same code as the other writes. A mismatch is 409 `version_mismatch`
everywhere.

The clients send the version they hold: the portal from the task in
its query cache (the drag reorder is the natural conflict, and a stale
reorder is refused and the list refetched), the CLI from the task it
reads before every verb, the Python client as a required argument.

## Consequences

The sentence in `docs/architecture.md` that recorded last-writer-wins
is gone; the tasks paragraphs there describe the version and the
conflict. A client that writes a task must have read it first and must
read again after a 409; there is no write that wins by default. Every
other table stays at the guideline's default until its concurrent edits
matter, and the next such table follows this shape: the column, the
compare-and-set in both impls, the version on the view and on every
write.

## Amended: 412 and the caller's version

Guideline 0.31.0 settles the wire shape this record argued about.

- A mismatch is `PreconditionFailed`, 412 `precondition_failed`. It
  replaces `VersionMismatch`, the 409 `version_mismatch`.
- The expected version comes from the caller and never from a row read
  inside the update. A PATCH and a DELETE name it in `If-Match`, as an
  entity tag; a move names it in `expected_version`. The gateway parses
  the header, and a write that names neither is 422
  `validation_failed`.
- `Task` declares `MANAGER_OWNED_FIELDS` (`position` and `version`),
  and the copy on update excludes them beside `PROVENANCE_FIELDS`, so
  an edit cannot place a task or set its version.
- The body's and the query's `version` stay accepted, marked
  deprecated, for one release, so a portal tab or a CLI of the release
  before keeps writing while this one rolls out. Release B drops them.

The portal reloads on 412, the Python client sends `If-Match` and
`expected_version`, and the CLI says a 412 in words.
