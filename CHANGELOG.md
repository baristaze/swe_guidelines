# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.51.0 (2026-10-04)

A production run asks a person once, a reusable step gates any work
that holds no credential of its own environment, and `main`, `release`,
and `scaffold` cannot be deleted or rewritten. The scaffold's ADRs are
compacted once. Minor: the scaffold gains a reusable workflow, a script,
and ADR 0082, and nothing is reversed.

### Added

- ADR 0082 and the scaffold's `human-approval.yml`, one reusable
  approval step: a `rule` job refuses an environment with no
  required-reviewers rule (or one it cannot read), then an `approve` job
  waits in that environment, `human-approval` by default. A job that
  `needs:` it runs only after a reviewer approved the run. It is for a
  gate whose critical job holds no credential of its own environment,
  such as a publish with the run's own token.
- `scripts/branch_rulesets.sh` in the scaffold sets a ruleset on `main`,
  `release`, and `scaffold`: no deletion, no force push, no bypass
  actor. `--dry-run` prints each one. An administrator runs it once per
  repository. A reset of `release` in the deploy runbook turns off both
  of its rulesets for that push.
- The repository's own `release.yml`, dispatched on `main` with a
  release's squash: it waits on `human-approval`, then fast-forwards
  `release`, tags the squash, and publishes the GitHub release with the
  changelog's section as its notes. It refuses while the environment has
  no required reviewer. `CONTRIBUTING.md` says so.

### Changed

- A production run asks a person once, on the one job that holds the
  deploy credential: `apply`, or `rollback` on a rollback. A test holds
  it so. `grant-operator.yml` and `state-unlock.yml` run the same rule
  check before their credential when the environment is `production`.
  The check's error says that a private repository can have a
  required-reviewers rule only under GitHub Enterprise, so on Free, Pro,
  or Team a production deploy there refuses every run.
- The scaffold's 47 ADRs are compacted in place: each keeps its title,
  status, decision, every bound, identifier, and near miss, and every
  consequence a reader acts on, and loses its repeated context, long
  quotes of the guideline, and points made twice (26,814 words to
  25,483). No ADR is renamed or renumbered. ADR 0024's positions not
  taken (no WAF; no GuardDuty or Security Hub) move into its Decision
  with their triggers.

### Fixed

- The ops audit test's database name carries eight random hex
  characters, inside the `audit_<slug>` pattern the audit tool accepts,
  so two gates on one compose stack no longer drop each other's
  database mid-run.
