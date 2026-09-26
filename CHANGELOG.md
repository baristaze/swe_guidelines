# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.33.0 (2026-09-26)

A column leaves the mapping one release before it leaves the table.
Expand and contract said to switch the code in one release and drop the
column in the next. Keeping a column out of reads does not switch it:
an ORM names every mapped column in each insert, so a deferred column
is still written, and the drop breaks the release before it while it
serves. Minor: a rule is added.

### Changed

- "The Storage Layer, Migrations": switching the code before a drop
  means the release before the drop no longer maps the column at all.
  Keeping it out of reads is not enough, because the ORM names every
  mapped column in each insert, so a column that is only deferred is
  still written, and the drop breaks that release while it serves.
- Lens STO-24 (An applied migration is never edited; expand, then
  contract): **Look for** and **Violation** name a column dropped while
  the release before it still maps it. The Principle and the severity
  are unchanged; 258 lenses.
