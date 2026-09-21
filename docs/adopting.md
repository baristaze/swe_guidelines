# Adopting the guideline in a project

A project that follows the guideline needs four things: the skills,
a visible pointer to the guideline from the project's own
specification folder, a record of the technologies it substitutes for
the ones the guideline names, and a place to record deviations. Nothing in the
project's `specs/` folder is owned by this repository; the pointer is
one short file the project writes itself.

## Install the skills

Inside Claude Code, once per machine:

```text
/plugin marketplace add baristaze/swe_guidelines
/plugin install swe-guidelines@swe-guidelines
```

Skills are namespaced: `/swe-guidelines:arch-review-full`,
`/swe-guidelines:arch-scaffold-entity`, and so on. The guideline and
the lens catalog travel inside the plugin, so a skill always reads the
version it shipped with. Before a scaffold or a review:

```text
/plugin marketplace update swe-guidelines
/plugin update swe-guidelines
/reload-plugins
```

Read the version a scaffold names in its output. It is the last
release in the copy that ran. A copy installed from `main` can carry
changes made after that release, because the version moves only when a
release is cut. Pin the tag, and see what came after it with
`git log v<version>..main` in a checkout.

The review-and-fix pass is part of bootstrapping a system, not an
afterthought: `arch-scaffold-new` sweeps the four misses a fresh tree
makes most, runs the full review over what it wrote, and closes every
high finding before it hands the tree over. The scaffolds that add to
a tree that already exists run the repository's own gate instead, so
the review of a change is `/swe-guidelines:arch-review-full` on the
change.

The rules a program can check travel as tests, not as a package: a
fresh scaffold writes them into `om/tests/unit/` (the role map, the
tenant-first storage signatures, the import direction, the interface
check, the construction-site test for stages, the roots built whole,
one head per migration chain) and into `om/tests/integration/` (the
tenancy scope of every table against the policies the migrations
carry, and the login that is neither superuser nor `BYPASSRLS`, both
of which need a migrated database), and an existing codebase copies
them from a scaffolded tree, which writes every one of them, or from
the reference implementation, and adjusts the module names. A rule
that fails the build holds.

For a team that pins versions, add the marketplace from a tag:

```text
/plugin marketplace add https://github.com/baristaze/swe_guidelines.git#v0.21.0
```

## Point at the guideline from `specs/`

Create `specs/architecture.md` in the project with this content and
nothing else that belongs to the guideline:

```markdown
# Architecture

This project follows the Software Design and Architecture Guidelines:
<https://github.com/baristaze/swe_guidelines/blob/v0.21.0/architecture.md>
(pinned at `v0.21.0`).

The guideline is the source of truth for how this system is shaped.
`docs/architecture.md` describes what is implemented; `docs/adr/`
records the decisions that constrain future work, including every
deliberate deviation from the guideline.

## Substitutions

Recorded in `docs/adr/0002-technology-choices.md`.

| Named in the guideline | Here      |
|------------------------|-----------|
| Terraform              | Pulumi    |

## Deviations

| ADR  | Rule                                     | Summary                                       |
|------|------------------------------------------|-----------------------------------------------|
| 0007 | STO-02 (Storage Principles)              | The ledger posts one transaction per posting  |
```

A project without a `specs/` folder puts the same file wherever its
specifications live and names that place in its `README.md`.

Bump the pinned tag when the project adopts a newer guideline, in a
commit that also re-runs `arch-review-full` on the main branch.

## Record technology substitutions in one ADR

The guideline names its technologies on purpose (see its "Technology
Choices and How to Override Them" section). A project that keeps them
all writes nothing. A project that substitutes an equivalent (another
relational engine, another cloud, another view library) writes one ADR
under `docs/adr/`, once, when it adopts the guideline: per
substitution, the choice as named, the substitute, the reason, and the
rules the substitute must still satisfy. `arch-scaffold-new` writes
this record as `docs/adr/0002-technology-choices.md` with the default
stack filled in; edit it rather than adding a second one. The
`Substitutions` table in `specs/architecture.md` links it, so a reader
sees at a glance what differs. A substitution that changes a shape is
a deviation, and goes under "Record deviations as ADRs" instead.

## Record deviations as ADRs

`/swe-guidelines:arch-deviate STO-02 "the ledger needs one transaction
per posting"` writes an ADR in `docs/adr/` in the project's own
numbering, quoting the rule verbatim and naming what the project
accepts in exchange. Review skills treat a deviation recorded this way
as a documented exception when its ADR is cited next to the code.

## Operate with the built-in skills

A scaffolded tree carries nine project-local skills under
`.claude/skills/`, one folder each: `ops-investigate`, `ops-watch`,
`ops-root-cause`, `ops-infra-as-code`, `ops-cloud-deployment-create`,
`ops-cloud-deployment-nuke`, `ops-simulate-traffic`,
`stress-test-create-or-update`, and `stress-test-run`. They are not
namespaced under the plugin, because they belong to the project:
`/ops-investigate --env staging`.

Each one names what it needs. The reads (investigate, watch, root
cause, the plan in infra-as-code, the signals a traffic or stress run
reads back) hold the read-only investigate profile of the
environment, `<root>-<env>-investigate` in `~/.aws/config`, and
refuse to run under a wider one. Create and nuke hold the
administrator profile `<root>-admin` and refuse anything else. The
application side (the operator identity, the error tracker's URL and
token) comes from one owner-only env file per environment,
`~/.config/<root>/ops/<env>.env`, outside the repository; a skill
reads it and never prints a secret from it.

Every skill takes `--env local|staging|production`, and `local` runs
against the compose stack's `devx` twins with no cloud and no
account, so a skill is tested on the developer's machine before it
is trusted with an environment.

An existing tree copies the templates from
`skills/_shared/ops-skills/` in this repository into
`.claude/skills/<name>/SKILL.md` and substitutes its root package for
`acme` (`ACME` and `Acme` for the upper and capitalized spellings),
and changes nothing else. The skills assume the roles, the profiles,
the env file, the `<root>-ops` binary, and the operator plane's read
routes that `arch-scaffold-new` writes; a tree without them adds
them first.

## Optional: vendor the text

A project that wants the guideline text in its tree without the plugin
(a reader with no Claude Code, an offline build) fetches it at a
pinned tag into a folder it does not edit:

```makefile
GUIDELINE_TAG ?= v0.21.0
GUIDELINE_URL := https://raw.githubusercontent.com/baristaze/swe_guidelines/$(GUIDELINE_TAG)

guidelines-sync:  ## fetch the pinned guideline and lenses into vendor/swe_guidelines/
	mkdir -p vendor/swe_guidelines/lenses
	curl -fsSL $(GUIDELINE_URL)/architecture.md -o vendor/swe_guidelines/architecture.md
	for g in README om contracts context storage async network delivery ops; do \
	  curl -fsSL $(GUIDELINE_URL)/lenses/$$g.md -o vendor/swe_guidelines/lenses/$$g.md; done
	@echo "synced $(GUIDELINE_TAG)"
```

Commit the vendored copy or ignore it; either way, `specs/` stays the
project's own.

## Optional: project skills without the plugin

Clone this repository next to the project and symlink the skill
folders into `.claude/skills/`:

```bash
git clone https://github.com/baristaze/swe_guidelines ../swe_guidelines
mkdir -p .claude/skills
for s in ../swe_guidelines/skills/arch-*; do ln -s "$(cd "$s" && pwd)" ".claude/skills/$(basename "$s")"; done
```

The skills reference `${CLAUDE_SKILL_DIR}/../../architecture.md`,
which resolves through the symlink to the clone, so keep the clone at
its checkout layout. Skill discovery through symlinked folders is not a
documented Claude Code feature; the plugin route is the supported one.
