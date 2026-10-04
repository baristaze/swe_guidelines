# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.50.0 (2026-10-04)

A migration waits for a lock only briefly, and the deploy runs it again
when it gives up. No capability carried in a URL reaches the error
tracker, from the server or the portal. A copy numbers its own ADRs
from 1001. Minor: the guideline gains one rule and sharpens two, and
NET-26's statement deadline is reversed on a migration's connection.

### Added

- Migrating a Deployed Database, and STO-35: a migration's connection
  carries `lock_timeout` from settings, under the serving statements'
  deadline, and no statement deadline. A migration past the bound exits
  with a code that asks for another run, and the deploy runs it again a
  bounded number of times before it fails the apply. The scaffold's ADR
  0071 cites the rule.
- Error Tracking, and the delivery lens's rule on error events: an
  outbound call's breadcrumb keeps the method, the status, and the URL's
  scheme and host. `outgoing_breadcrumb` in the scaffold drops the path,
  where a webhook's capability lives.
- Records of Decisions, and DEL-23: the scaffold's ADRs stay below 1000,
  and a copy numbers its own from 1001. `arch-scaffold-new` and
  `arch-deviate` number a copy's decisions from 1001, and
  `arch-upgrade-scaffold` moves a clashing scaffold ADR into the copy's
  range and rewrites the citations the merge brings.

### Changed

- Reversal: NET-26 asks a statement deadline of a storage impl's serving
  statement alone. A migration's connection carries the lock bound of
  STO-35 instead, and a statement deadline on it that cuts a long
  backfill is now a violation.
- `arch-review-full` runs unattended: the review template, the skill,
  and the reviewer agent list a scope's files with `git ls-files`, run
  git from the root, never with `-C`; the fallback reviewer gets the
  same inputs, read-only tools, and an 80-turn cap; and a group report
  has a stated format test, one re-run, and one rule each for a fix's
  symbol, a tie, and a path's form.
- The `review-om` benchmark scenario sets `evidence.lenses: true`, so
  its judges read the section of every lens an answer cites, and its
  answer key expects OM-16 and the `MANAGER_OWNED_FIELDS` half of OM-03.
  A planted finding may list each file its defect shows in.
- The repository's `CLAUDE.md` moves to `.claude/CLAUDE.md`, `make
  plugin` validates the plugin with `--strict`, and
  `scripts/check_plugin.py` is removed with its test. CI's Claude Code
  pin moves to 2.1.289.

### Fixed

- A presigned URL names the bucket's regional host, so an upload from
  the browser to a bucket made that day outside us-east-1 no longer
  fails on a redirect.
- The portal's error reports cut every URL at its query: breadcrumbs,
  `request.url`, the `Referer` header, and stack frames. An invitation
  token or a sign-in code no longer reaches the tracker.
- The create run writes the error tracker's url, org, and project from
  one `error_tracker` entry in `environments.json`, instead of the
  product's name, and refuses a placeholder or a half-named tracker.
- `arch-benchmark-browser` looks for computer actions on the
  conversation page after the send too, and records such a session
  `not-run`; its evidence takes the extension the tool saves, `.png`.
- The stale-CNAME test reads the site's name from `environments.json`,
  so it passes in a copy's gate.
- The requeue's plan test seeds settled items and live leases, analyzed,
  before it reads its plan, so it no longer fails at random.
