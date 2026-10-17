# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.62.0 (2026-10-17)

Retry waits and periodic loops come from one shared package, and a few
retry paths are fixed. Minor: the monorepo names a `libs/` folder for
pure libraries every package may import.

### Added

- `libs/timing` (`acme-timing`), a package with no dependency that
  every package may import: `RetryPolicy`, a growing, capped wait with a
  share of it drawn at random and a server's Retry-After as its floor,
  which never overflows; `Retries`, one loop's count of failures over a
  policy; `retry_after`, which reads seconds or an HTTP date; and
  `Every`, work on a fixed grid, start to start, that skips the ticks a
  long run missed. The folder tree and DEL-07 name `libs/`, and
  arch-check's default sources read it.
- The TypeScript client exports `RetryPolicy` and `Retries`, the twins
  of the Python ones.
- `scripts/release_before.sh` takes `--running-stack`, for a stack
  another process started: it starts nothing and refuses a database URL
  that names the stack's own database.

### Changed

- The outbox, work, and sign-in waits, the Python client's retry and
  reconnect waits, and the topics listener take their waits from
  `RetryPolicy`, each the wait it gave before, but the topics listener's
  reconnect, which now doubles from 0.5 s to 30 s.
- The Python client reads a Retry-After given as an HTTP date.
- The TypeScript client's retry loop and the portal's reconnect wait by
  `RetryPolicy` and `Retries`.
- The worker's heartbeat, sweep, and tally, the Python client's
  keepalive, and the API socket's recheck of a viewer's access run on
  `Every`, start to start, each with its period.
- `arch-upgrade-scaffold` and `docs-compact` state the cases their
  steps left to a guess on a first move across a fold, and for a copy
  that takes its render's folded chains; the merge table moves to
  `references/merge.md`.
- CI's release-before step reads the queue chain's head by its parents.

### Fixed

- The sign-in delay caps its doublings, so a long run of wrong codes no
  longer raises.
- A failed delivery comes back later on each receive: 60 to 120 s
  first, four times longer after each, half of it random, capped below
  the queue's 12 hours.
- The storage bound tests open their connections under the settings'
  checkout bound, so a slow connect no longer fails them.
- `test_openapi_documents.py` admits a document of enums alone.

### Removed

- `MAX_DOUBLINGS` in the outbox, work, and tenancy rules, and the
  Python client's backoff constants: `RetryPolicy` holds the bound.
- The TypeScript client's `retryDelayMs` and `retryWaitMs`, and the
  portal's `backoffDelay`: `RetryPolicy.waitMs` takes their place.
