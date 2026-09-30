# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.43.0 (2026-09-30)

A document an agent reads says what holds, and a scaffold skill keeps a
copy to it. Minor: a rule, a lens, and a skill are added, and one rule
loosens, named below.

An agent acts on what it reads. Text that also carries what was, a
superseded decision, a former value, a rejected option, makes it sort
the adopted from the rejected before it acts. So the tree holds what is,
and git, the pull request, and the release notes hold what was.

### Added

- Documentation as Code states the rule, with lens OPS-29: a README, an
  ADR, a spec, and a comment say what holds at the head of the main
  branch, in the present tense. A rejected option stays only as a rule's
  near miss, "X, never Y", when Y is what a reader could reach without
  knowing the past.
- Records of Decisions gives an ADR its life (DEL-23, DEL-25): it is
  rewritten in place when its decision changes and removed when it
  constrains nothing, its number never changes, and its status is one
  date. A substitution is one row of the one ADR that lists them. A
  changelog holds the latest release.
- The scaffold gains the skill `docs-compact`, optional in Operational
  Skills. It rewrites a copy's ADRs to their present decision, removes
  the ones that constrain nothing, cuts the changelog to its latest
  release, cleans the rows of `specs/architecture.md`, and rewrites the
  comments that tell what the code did before. With `--migrations` it
  folds each role's chain. It works on a branch, pushes nothing, and
  reports what it removed, rewrote, and kept.
- `make leaks` reads the scaffold's documents for history, and holds its
  ADRs to a status of one date and no Alternatives section.

### Changed

- Loosened: STO-24 and Migrations allow a fold. An applied migration
  file is still never edited, but a chain may become one revision that
  holds the head's schema under the head's revision id, while every
  database that exists is at that head. A schema dump of the chain and
  of the fold shows them equal, and the fold's commit says so.
- `arch-upgrade-scaffold` asks for no ADR per release and no status that
  names the release that retired a deviation: an ADR whose rule now
  holds is removed, or rewritten to what is left of it.
  `arch-scaffold-app` removes the ADR its console ends.
- The scaffold holds the rule: its ADRs carry no Alternatives section,
  ADR 0030 is a row of ADR 0002, and its comments say what the code
  does.

### What a copy does

- A copy that takes this release runs `/docs-compact` once, and
  `/docs-compact --migrations` when every database it has is at its
  chain's head. A snapshot older than the fold is restored at the last
  release before the fold, which migrates it to the head.
