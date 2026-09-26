# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.35.0 (2026-09-26)

Checks run on the merge with the current `main`, and a table may carry
one fence policy per login where planning needs it. Two changes that
were each green alone could land a conflict together, because a pull
request's checks ran against `main` as it was when the branch was last
updated. And one combined policy made the planner misjudge a
system-scope statement on a busy table. Minor: two rules are added, and
one check is fixed.

### Changed

- "Layout Conventions": a pull request's checks run on its merge with
  the current `main` before it lands, through a merge queue or a
  required up-to-date branch. Numbers that must be unique across
  changes are settled there: the later change takes the next free ADR
  number and re-points its migration's parent; the earlier change is
  never renumbered. "Migrations", "Records of Decisions", and "Security
  Defaults" say the same where they apply. Lens DEL-11 judges it.
- `arch-scaffold-new`: the generated CI runs on `merge_group` as well,
  and the `main` ruleset requires checks on an up-to-date branch, which
  every repository can enable (a merge queue needs an organization).
- "The Second Fence": a table whose system-scope statement plans badly
  under the one combined policy may carry one policy per login instead:
  a tenant policy granted to the runtime login, a system policy granted
  to the system login, under the system scope only. It is kept only
  where a measurement shows it, recorded in the migration that makes
  it, and every guarantee of the fence holds; the policy check test and
  the negative control cover both policies. Lens STO-28 names the
  split's shape.

### Fixed

- STO-14 no longer flags a unique index on `org_id` beside a compound
  index that starts with it: a unique index enforces a rule, one row
  per tenant, and is not a second lookup index.
