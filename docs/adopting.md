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

The rules a program can check travel two ways. A rule that reads
only the source travels as `arch-check`, the static checker in this
repository's `checkers/`, which a project runs from the tag it pins
(see "Run the checker" below). The role map and one head per
migration chain are among those. A rule that needs the built system
or a migrated database travels as a test the project writes. The
roots built whole are a unit test in `om/tests/unit/`. The tenancy
scope of every table against the policies the migrations carry, and
the login that is neither superuser nor `BYPASSRLS`, are integration
tests in `om/tests/integration/`. The guideline's
Records of Decisions lists each rule and says which way it is
checked. A fresh scaffold sets up both. An existing codebase adds the
checker to its gate and writes the tests from that list. A rule that
fails the build holds.

For a team that pins versions, add the marketplace from a tag:

```text
/plugin marketplace add https://github.com/baristaze/swe_guidelines.git#v0.35.0
```

## Run the checker

`arch-check` decides the lenses a program can decide: an import that
crosses a layer, a mixin out of order, a storage method without its
tenant. It reads the source with Python's `ast` and never imports it.
It needs Python 3.11 and nothing else. It runs in a second, offline,
in the same gate as the tests. The lenses it cannot decide stay with
the review skills, which run it first and judge the rest.

Pin it at the tag of the guideline the project follows, in the
`Makefile`, and put it in the fast gate:

```make
ARCH_CHECK := uvx --python "$(shell cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.35.0\#subdirectory=checkers" arch-check

arch-check: ## the guideline's static checks
	$(ARCH_CHECK)

check: lint format-check typecheck arch-check test-unit
```

`--python` runs the checker on the Python the project pins. The
checker parses with its own interpreter's grammar, so it refuses to
run (exit 2) on a Python older than `.python-version`, rather than
misread newer syntax. CI runs `make check`, so nothing else changes
there. Bump the tag in
the same commit that bumps the pin in `specs/architecture.md`: the
checker and the lenses it decides move together.

Configure it in the root `pyproject.toml`. A project in the layout the
guideline prescribes names its package in this table. A scaffolded
tree puts no other key there:

```toml
[tool.arch-check]
package = "acme"
```

Three more things go beside it, each only when needed:

- **Options.** A rule that needs the project's own names reads them
  from `[tool.arch-check.options.<RULE-ID>]`, for example the storage
  methods allowed to take no tenant. The default is the name the
  guideline uses. A scaffolded tree sets two lists and no other
  option: the sites that construct a stage, under
  `[tool.arch-check.options.CTX-26] sites`, and the tenant-less
  storage methods, under `[tool.arch-check.options.CTX-12]
  tenantless`. Its role map and scope map carry the default names,
  `TABLE_ROLES` and `TABLE_SCOPES`, so they need no option.
- **Exceptions.** A rule turned off, or a file allowed to break it, is
  a deviation. Each entry names its ADR, and the run refuses an entry
  whose ADR does not exist. An exception that no longer matches
  anything fails the run too.
- **Project-local rules.** `local = ["tools/arch_check"]` loads the
  project's own rules, written against the same API. Write one when a
  rule needs the project's own logic, not only its names; names go in
  options. A local rule names the lens it decides, and the review
  skills treat it exactly like a shipped one.

`arch-check --list` prints every rule with the lens it decides and
whether it decides the whole lens or a part. `checkers/README.md` in
this repository is the full reference: flags, exit codes, the JSON
report, and how to write a rule.

A rule the checker cannot hold without guessing is not shipped. Its
lens stays with the review, which is the fallback for every lens the
checker does not decide.

## Point at the guideline from `specs/`

Create `specs/architecture.md` in the project with this content and
nothing else that belongs to the guideline:

```markdown
# Architecture

This project follows the Software Design and Architecture Guidelines:
<https://github.com/baristaze/swe_guidelines/blob/v0.35.0/architecture.md>
(pinned at `v0.35.0`).

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
commit that also re-runs `arch-review-full all` on the main branch.

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

Tell `arch-check` too. A rule that looks for a named technology reads
the name from an option that defaults to the guideline's choice. Set
the option to the substitute, and the rule holds the substitute to the
same shape. Never `disable` the rule for a substitution: a disable is
a deviation, and a substitute is not one.

## Record deviations as ADRs

`/swe-guidelines:arch-deviate STO-02 "the ledger needs one transaction
per posting"` writes an ADR in `docs/adr/` in the project's own
numbering, quoting the rule verbatim and naming what the project
accepts in exchange. A breach whose ADR is cited next to the code is a
documented exception. A review reports it on one line under
Deviations. It is not a finding, and it never lowers a severity.

## Operate with the built-in skills

A scaffolded tree carries thirteen project-local skills under
`.claude/skills/`, one folder each: `ops-investigate`, `ops-watch`,
`ops-root-cause`, `ops-infra-as-code`, `ops-cloud-deployment-create`,
`ops-cloud-deployment-nuke`, `ops-simulate-traffic`,
`stress-test-create-or-update`, and `stress-test-run`, and the four
audits, `audit-retention`, `audit-query-indexes`,
`audit-database-calls`, and `audit-deploy-time`. They are not
namespaced under the plugin, because they belong to the project:
`/ops-investigate --env staging`.

Each one names what it needs. The reads (investigate, watch, root
cause, the plan in infra-as-code, the signals a traffic or stress run
reads back) hold the read-only investigate profile of the
environment, `<root>-<env>-investigate` in `~/.aws/config`, chained
from the person's identity center sign-in, and refuse to run under a
wider one. Create and nuke hold the environment's administrator
profile, the one `deployment/cloud/environments.json` names beside its
account id, and refuse anything else. The
application side (the operator identity, the error tracker's URL and
token) comes from one owner-only env file per environment,
`~/.config/<root>/ops/<env>.env`, outside the repository; a skill
reads it and never prints a secret from it.

Every operational skill but `ops-cloud-deployment-create` and
`ops-cloud-deployment-nuke`, which act on a cloud only, takes
`--env local|staging|production`, and `local` runs against the compose
stack's `devx` twins with no cloud and no account, so a skill is tested on the developer's machine before it
is trusted with an environment.

An audit answers a question that repeats: which stores grow without
bound, whether the indexes fit the queries, how many database calls
each endpoint makes, where a deploy's minutes go. It reads and reports,
and proposes tickets; it never fixes. The three about the database make
a database of their own on the local stack, seed it at a scale the run
states, measure it, and drop it, with the tools under `ops/audit/`, so
they never log in to a shared one. Each writes its report to
`~/Downloads/<root>_<audit>_<date>.md`.

An existing tree copies the templates from
`skills/_shared/ops-skills/` in this repository into
`.claude/skills/<name>/SKILL.md` and substitutes its root package for
`acme` (`ACME` and `Acme` for the upper and capitalized spellings),
and changes nothing else. The skills assume the roles, the profiles,
the env file, the `<root>-ops` binary, the tools under `ops/audit/`, and the operator plane's read
routes that `arch-scaffold-new` writes; a tree without them adds
them first.

## Optional: vendor the text

A project that wants the guideline text in its tree without the plugin
(a reader with no Claude Code, an offline build) fetches it at a
pinned tag into a folder it does not edit:

```makefile
GUIDELINE_TAG ?= v0.35.0
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
