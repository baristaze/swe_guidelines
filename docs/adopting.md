# Adopting the guideline in a project

A project that follows the guideline keeps four things of its own: a
pointer to the guideline in its `specs/` folder, the checker in its
gate, one record of the technologies it substitutes, and an ADR for
each deviation. A copy of the scaffold starts with all four.

## Start here

Adopt [the Core](../architecture.md#the-core) on day one. Its
invariants are what make a system this architecture, and every other
rule leans on them. The tags, defined in [How to Read
This](../architecture.md#how-to-read-this), say what can wait:

- An `optional` section waits for its trigger. Until then, its absence
  is no gap.
- A `default` is kept or swapped. A swap is a substitution, recorded
  once, and not a deviation.
- A `style` section is a house convention. A departure is a low finding
  at most.

Untagged text is the rule. A project that departs from it on purpose
[records a deviation](#record-deviations-as-adrs), and a review then
reports the deviation instead of a finding.

## A new project

From a checkout of this repository:

```bash
python3 scaffold/new.py ~/code/pressroom
```

The copy carries the pointer, the checker, the record of
substitutions, and the operational skills, pinned at this release. It
runs as it is, and the project builds its own domain on top.

## An existing codebase

An existing codebase adds what a copy carries, in this order: the
checker in its gate, the pointer in `specs/`, the record of its
substitutions, and an ADR for each deviation it keeps. Then it writes
the tests for what the checker cannot read: a rule that needs the built
system or a migrated database, such as tenant isolation. The scaffold's
[`om/tests/`](../scaffold/acme_root/om/tests/) shows them.

## Install the skills

Install the plugin as the [README](../README.md#install-the-skills)
says. A team that pins versions adds the marketplace from a tag:

```text
/plugin marketplace add https://github.com/baristaze/swe_guidelines.git#v0.38.0
```

A copy installed from `main` can carry changes made after the release
it names, so a team that needs to know what it runs pins the tag.

## Run the checker

`arch-check` decides the lenses a program can decide, and it runs in
any CI. Pin it at the project's guideline release, in the fast gate:

```make
ARCH_CHECK := uvx --python "$(shell cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.38.0\#subdirectory=checkers" arch-check

arch-check: ## the guideline's static checks
	$(ARCH_CHECK)

check: lint format-check typecheck arch-check test-unit
```

Bump the tag in the same commit as the pin in `specs/architecture.md`,
since the checker and its lenses move together. Name the package in the
root `pyproject.toml`:

```toml
[tool.arch-check]
package = "acme"
```

A rule that needs the project's own names reads them from an option. A
rule turned off, or a file let through, is a deviation, and its entry
names the ADR. A rule of the project's own is a local rule.
[`checkers/README.md`](../checkers/README.md) shows each one.

A rule that looks for a named technology reads the name from an option.
After a substitution, set that option to the substitute, and the rule
holds the substitute to the same shape. Never disable a rule for a
substitution: a disable is a deviation, and a substitution is not.

## Point at the guideline from `specs/`

`specs/architecture.md` pins the release and lists what differs:

```markdown
# Architecture

This project follows the Software Design and Architecture Guidelines:
<https://github.com/baristaze/swe_guidelines/blob/v0.38.0/architecture.md>
(pinned at `v0.38.0`).

## Substitutions

Recorded in `docs/adr/0002-technology-choices.md`.

| Named in the guideline | Here      |
|------------------------|-----------|
| Terraform              | Pulumi    |

## Deviations

| ADR  | Rule                          | Summary                                      |
|------|-------------------------------|----------------------------------------------|
| 0007 | STO-02 (Storage Principles)   | The ledger posts one transaction per posting |
```

A project without a `specs/` folder puts the file where its
specifications live, and names that place in its `README.md`. Bump the
tag when the project adopts a newer release, in a commit that also runs
`arch-review-full all` on the main branch.

## Record technology substitutions in one ADR

A project that keeps every technology the guideline names writes
nothing. One that substitutes an equivalent writes one ADR, once: for
each substitution, the choice, the substitute, the reason, and the
rules the substitute must still satisfy. A copy of the scaffold has it
as `docs/adr/0002-technology-choices.md`; edit it rather than add a
second. A substitution that changes a shape is a deviation.

## Record deviations as ADRs

`/swe-guidelines:arch-deviate STO-02 "the ledger needs one transaction
per posting"` writes an ADR in the project's `docs/adr/`, quoting the
rule and naming what the project accepts in exchange. A breach whose ADR
is cited next to the code is a documented exception. A review reports
it on one line under Deviations. It is not a finding, and it never
lowers a severity.

## Operate with the built-in skills

A copy of the scaffold carries its operational skills and audits under
`.claude/skills/`. They belong to the project, so they are not
namespaced: `/ops-investigate --env staging`. The scaffold's
[`ops/README.md`](../scaffold/acme_root/ops/README.md) lists each one,
the role it holds, and what it answers. Each holds the one role its
row names, and refuses a wider one. A skill that takes `--env` runs
with `local` against the local stack's twins, so it is tested on a
laptop before an environment trusts it. An audit reads and reports; it
never fixes.

An existing tree copies the scaffold's `.claude/skills/` and renames
`acme` in them to its own name, in each form `scaffold/new.py` uses.
The skills assume the roles, the profiles, the env file, the
`<root>-ops` binary, the tools under `ops/audit/`, and the operator
plane's read routes that the scaffold carries. A tree without them adds
them first.

## Optional: vendor the text

A project that wants the text in its tree without the plugin fetches it
at a pinned tag into a folder it never edits:

```makefile
GUIDELINE_TAG ?= v0.38.0
GUIDELINE_URL := https://raw.githubusercontent.com/baristaze/swe_guidelines/$(GUIDELINE_TAG)

guidelines-sync:  ## fetch the pinned guideline and lenses into vendor/swe_guidelines/
	mkdir -p vendor/swe_guidelines/lenses
	curl -fsSL $(GUIDELINE_URL)/architecture.md -o vendor/swe_guidelines/architecture.md
	for g in README om contracts context storage async network delivery ops; do \
	  curl -fsSL $(GUIDELINE_URL)/lenses/$$g.md -o vendor/swe_guidelines/lenses/$$g.md; done
	@echo "synced $(GUIDELINE_TAG)"
```

## Optional: project skills without the plugin

Clone this repository beside the project and link the skill folders
into `.claude/skills/`:

```bash
git clone https://github.com/baristaze/swe_guidelines ../swe_guidelines
mkdir -p .claude/skills
for s in ../swe_guidelines/skills/arch-*; do ln -s "$(cd "$s" && pwd)" ".claude/skills/$(basename "$s")"; done
```

A skill reads `${CLAUDE_SKILL_DIR}/../../architecture.md`, which
resolves through the link to the clone. Claude Code does not document
skills found through linked folders, so the plugin is the supported
route.
