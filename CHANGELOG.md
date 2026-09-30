# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.45.0 (2026-09-30)

A review runs none of the code it reviews, a session made from a
session keeps its deadline, and the scaffold writes no text a tenant
chose into what an operator's skill reads. Minor: `arch-check` gains a
flag and lens CTX-36 is sharpened. Nothing is reversed.

### Added

- `arch-check --no-local` skips a project's local rules, and accepts a
  config entry that names a lens a local rule decides. A project's own
  gate keeps its local rules.

### Changed

- The review skills pass `--no-local`, name the root with `--root`, and
  pre-approve no Python command. A review asks once before it runs the
  checker, and judges the local rules' lenses itself.
- A session made from a session keeps the deadline of the one it ends:
  a switch into an org and the landing after an org's deletion take the
  earlier of the presented session's deadline and a full lifetime.
  Only the exchange of a sign-in starts a new lifetime. CTX-36 and the
  scaffold's ADR 0063 say so.
- The scaffold's log lines and spans name a request by its route's
  template, never its raw path, and the lost-marker warning names an
  idempotency key by a digest. A span records no exception text, the
  server's socket line drops its target, an error event keeps the
  request's method alone, and every engine hides its bound parameters.
- `ops-root-cause` keeps ids, kinds, and timestamps from its two reads
  of the operator plane, never a name, reads a next page of members by
  its cursor, and says what an empty answer means. The skills that hold
  a token pre-approve the ops command alone.
- `docs-compact` tells an expand and contract in flight by the tree:
  the last up file that names the piece decides, and no release tag is
  counted. Its sweep lists no applied migration and no generated file.
  Its gates run `make openapi`, and run the database targets on a
  database the run makes and drops. Its citation search finds every
  form the checker reads and a citation the margin wrapped, and an ADR
  cited from a place the run never edits is kept.

### Fixed

- `WorkManagerImpl.enqueue` requires the permission its kind's table
  gives, and refuses a kind the table lacks.
- A webhook signature outside its alphabet, or a timestamp past what
  `int` reads, is a bad signature and a 400. A one-time code, an
  `If-Match` version, and a `Content-Length` are read as ASCII digits
  alone.
- `scaffold/base.py` writes no member of a release's archive through a
  link, and lets no link leave the root.
- The error tracker reader sends no sort the tracker refuses, so a
  request that left no error event answers none.
- A copy with a two-word name names its local tracker organization by
  the slug the tracker gives it.

### What a copy does

- Nothing but take the release. A session no longer renews by a switch,
  so a person signs in again when the deadline of their sign-in passes.
  An error event in the tracker no longer shows the request's URL,
  query, or headers.
