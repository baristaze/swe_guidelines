# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.52.0 (2026-10-06)

Each database role runs on its own Postgres in a copy's local stack,
and a gate runs the release before on a branch's schema before the
branch merges. Minor: the scaffold gains a CI job, a script, a migrate
command, and ADRs 0083 and 0084, and nothing is reversed.

### Added

- `release-before`, a job in a copy's CI that every pull request runs
  and the main ruleset `cloud_create.sh` writes requires. A pull request
  that changes nothing under `om/migrations` ends green at once.
  Otherwise the stack migrates to the branch's head, and the integration
  suite of each release before (the merge base, and the `release`
  branch's tip when it differs) runs against it. `make release-before`
  runs it locally; `scripts/release_before_deselect.txt` names any test
  the release before cannot pass by design. DEL-50, STO-24, and the
  guideline's Migrations and Deployment sections name it; ADR 0084
  records it.
- `migrate stamp --role|--all --heads-of <checkout>` writes each role's
  version record as another checkout's head, applying nothing. It
  refuses a database that is not local.

### Changed

- A copy's local stack runs `postgres-core`, `postgres-activity`,
  `postgres-queue`, and `postgres-admin`, each with its own port (55432
  to 55435) and volume, and `.env.example` points each role's URL at
  its instance. With no role URL set, every role reads the one URL, as
  the cloud does. `migrate ensure-logins` runs once per database, as the
  master there, for the roles it holds. pgweb has a bookmark per
  instance, and GlitchTip's database sits on the core instance. The
  guideline's Database Roles and Local: Docker Compose sections, STO-19,
  and DEL-04 state the posture; ADR 0083 records it.
- A copy renames `<NAME>_POSTGRES_PORT` to `<NAME>_POSTGRES_CORE_PORT`,
  takes the three new ports and the four role URLs into its `.env`, and
  moves its local data to the new volumes (`make reset`). A second
  checkout repoints all eight database URLs.
