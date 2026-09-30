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

Untagged text is the rule. A departure from it on purpose is [a
deviation](#record-deviations-as-adrs), recorded in an ADR.

## A new project

From a checkout of this repository:

```bash
python3 scaffold/new.py ~/code/pressroom
```

The last part of the path is the name: one or two snake_case words, at
most 17 characters. The copy carries all four, and the operational
skills, pinned at this release. It runs as it is, and the project
builds its own domain on top. `/swe-guidelines:arch-scaffold-new` does
the same from the plugin, then records the product's first decisions
and adds its first namespace with its entity.

## An existing codebase

An existing codebase adds the four, in this order: the checker, the
pointer, the record of substitutions, and an ADR for each deviation it
keeps. Then it writes the tests for what the checker cannot read: a
rule that needs the built system or a migrated database, such as tenant
isolation. The scaffold's [`om/tests/`](../scaffold/acme_root/om/tests/)
shows them.

## Install the skills

The skills follow the [Agent Skills](https://agentskills.io/specification)
standard, and the [README](../README.md#install-the-skills) gives two
routes. In Claude Code, install the plugin. In any other agent that
reads the standard, clone this repository and link each skill into the
project's `.agents/skills/`. In a copy of the scaffold, `.claude/skills`
links to that folder, so Claude Code finds the linked skills there too.

A copy installed from `main` can carry changes past the release it
names, so a team that pins versions installs from a tag. The plugin
adds the marketplace from it:

```text
/plugin marketplace add https://github.com/baristaze/swe_guidelines.git#v0.42.0
```

The clone checks it out: `git -C ../swe_guidelines checkout v0.42.0`.

## Run the checker

`arch-check` decides the lenses a program can decide, and it runs in
any CI. Pin it at the project's guideline release, in the fast gate:

```make
ARCH_CHECK := uvx --python "$(shell cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.42.0\#subdirectory=checkers" arch-check

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
<https://github.com/baristaze/swe_guidelines/blob/v0.42.0/architecture.md>
(pinned at `v0.42.0`).

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
`arch-review-full all` on the main branch. A copy that keeps the scaffold as
its base moves the pin with the merge that takes the release: see
[Upgrade a copy of the scaffold](#upgrade-a-copy-of-the-scaffold).

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

An ADR says what holds. When its decision changes, rewrite it in place.
When the deviation ends, remove it with its row. Its number never
changes, and its status stays one date: git, the pull request, and the
release notes say what it was.

## Operate with the built-in skills

A copy of the scaffold carries its operational skills and audits under
`.agents/skills/`, the folder every agent that reads the standard
shares, and `.claude/skills` links to it for Claude Code. They belong
to the project, so they are not namespaced: `/ops-investigate --env
staging` in Claude Code, `$ops-investigate --env staging` in Codex. The scaffold's
[`ops/README.md`](../scaffold/acme_root/ops/README.md) lists each one,
the role it holds, and what it answers. Each holds the one role its
row names, and refuses a wider one. A skill that takes `--env` runs
with `local` against the local stack's twins, so it is tested on a
laptop before an environment trusts it. An audit reads and reports; it
never fixes.

`/docs-compact` keeps the tree's documents to what holds: its ADRs, its
changelog, the rows of `specs/architecture.md`, and its comments. With
`--migrations` it also folds each role's migration chain into one
revision under the head's revision id, once every database that exists
is at that head. It works on a branch and pushes nothing.

An existing tree copies the scaffold's `.agents/skills/` into its own
and renames `acme` in them to its own name, in each form
`scaffold/new.py` uses. Then, from the tree's root, it moves any skill
of its own from `.claude/skills/` into `.agents/skills/`, and makes
`.claude/skills` a link to that folder:

```bash
if [ -d .claude/skills ] && [ ! -L .claude/skills ]; then
  for s in .claude/skills/*/; do [ -e "$s" ] && mv -n "${s%/}" .agents/skills/; done
  rmdir .claude/skills
fi
[ -e .claude/skills ] || { mkdir -p .claude && ln -s ../.agents/skills .claude/skills; }
```

A skill of the tree's own that has a scaffold skill's name stays in
`.claude/skills/`, and so does a file that is not a skill's folder.
`rmdir` then says the folder is not empty, and no link is made until
it is.

The skills assume the roles, the profiles, the env file, the
`<root>-ops` binary, the tools under `ops/audit/`, and the operator
plane's read routes that the scaffold carries. A tree without them adds
them first.

## Upgrade a copy of the scaffold

A copy of the scaffold can keep the scaffold as its base, and take each
later release by a merge rather than by hand. Its `scaffold` branch
holds the scaffold as the project took it. Each commit there is the
scaffold at one commit of this repository, renamed to the project's
name, and its parent is the one before. The project's main branch
merges that branch, so the last one merged is the base of the next
merge. Git then brings in what the scaffold changed since, and keeps
what the project changed.

`new.py` starts a copy at its base: from a clean checkout of this
repository, the copy's first commit is the scaffold, on `scaffold` and
the main branch. A later release takes two commands and no clone:

```bash
curl -fsSL https://raw.githubusercontent.com/baristaze/swe_guidelines/v0.42.0/scaffold/base.py | python3 - v0.42.0
git switch -c scaffold-v0-40-0 && git merge scaffold
```

`base.py` reads the scaffold at that release from one tarball, and moves
only the `scaffold` branch. The merge is the project's to resolve.
`/swe-guidelines:arch-upgrade-scaffold` makes the whole move: it
merges, resolves what the merge leaves, and runs the gates. Two things
stay the project's own through every move: its ADRs, which record its
decisions, and its migration chain, which its databases applied and
which only the project folds.

Merge the move into the main branch with a merge commit, never a
squash. A squash drops the parent that records the base, and the next
move would merge against an older one.

A project copied before its base was recorded, or from an archive,
starts at the release it pins. The skill's first move grafts that
release with a merge that changes no file, then merges the next.

## Optional: vendor the text

A project that wants the text in its tree without the plugin fetches it
at a pinned tag into a folder it never edits:

```makefile
GUIDELINE_TAG ?= v0.42.0
GUIDELINE_URL := https://raw.githubusercontent.com/baristaze/swe_guidelines/$(GUIDELINE_TAG)

guidelines-sync:  ## fetch the pinned guideline and lenses into vendor/swe_guidelines/
	mkdir -p vendor/swe_guidelines/lenses
	curl -fsSL $(GUIDELINE_URL)/architecture.md -o vendor/swe_guidelines/architecture.md
	for g in README om contracts context storage async network delivery ops; do \
	  curl -fsSL $(GUIDELINE_URL)/lenses/$$g.md -o vendor/swe_guidelines/lenses/$$g.md; done
	@echo "synced $(GUIDELINE_TAG)"
```
