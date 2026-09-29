# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.39.0 (2026-09-29)

A new system starts as a copy of a core that runs, and the text tells
the story. `scaffold/acme_root/` is a whole monorepo in the guideline's
shape with no product domain, and `scaffold/new.py` copies it under a
project's name in a second. The guideline is rewritten around it at a
third of its length, with the code shapes linked to the scaffold's
files and the detail held in the lenses and the skills. The skills
follow the Agent Skills standard, so they run in any agent that reads
it, not only in Claude Code. Minor: rules are added and sharpened, and
the reversals are named below. One rename, `OpContext` to
`TenantContext`, is one every adopter follows on upgrade.

### Changed

- `OpContext` is `TenantContext`, the stage module `om/opcontext.py`
  is `om/context.py`, and the section `## OpContext` is
  `## TenantContext`. `arch-check` reads the stages from
  `<pkg>.om.context` and takes no alias: a tree on the old names
  renames when it moves its pin. CTX-02 fails a tree that declares a
  stage anywhere else under `om`, so a tree still on the old names
  fails instead of passing every stage rule unjudged.
- `architecture.md` tells the story for two readers, a person and an
  agent, in 14,121 prose words from 44,960. How to Read This defines
  four tags (`core`, `default`, `optional`, `style`) and the
  agents-only block, and The Core lists 26 invariants. Every code shape
  is the scaffold's own, beside a link to its file. Every `##` and
  `###` title is kept.
- Reversed: the feed and the identity-scoped table mixins are gone from
  the guideline, the lenses, and `arch-check`. A table whose `org_id`
  leads a compound index sets `__org_id_index__ = False`, and an
  identity table's column is the one its scope map names.
- Reversed: the edge marker is `IdempotencyRecord`, fenced by its
  `attempt_id` (OM-03, NET-24).
- Reversed: an append-only record's time is a field of its own, not
  the time inside its id (OM-06), and a create's times are stamped by
  the manager, not kept as the caller built them (CON-17).
- Reversed: DEL-14 drops from medium to low.
- "Monorepo Folder Structure": a new system starts as a copy of the
  scaffold, `arch-scaffold-new <name>`. It no longer takes a target
  folder, a root package, `--no-portal`, or `--no-worker`: the name is
  the folder and the package. Each scaffold skill names the scaffold
  files whose shape it follows.
- "Clients Live in One Place": the TypeScript client lives in
  `clients/typescript/`, beside the Python one, and every browser app
  imports it. DEL-15 and NET-15 follow, and `arch-scaffold-app` has a
  new app import the package, never the portal.
- The scaffold's operational skills live in `.agents/skills/`, the
  folder the agents that read the Agent Skills standard share, and
  `.claude/skills` is a link to it. Every skill names its files by a
  path from its own folder, resolved with `realpath` where it climbs
  out, instead of `${CLAUDE_SKILL_DIR}`. `arch-review-full` runs its
  groups as subagents where the agent has them, and one after another
  where it has none. OPS-11 finds a built-in skill in either folder.
- `benchmark/README.md` is one page a person reads, and the harness
  detail lives with the code.

### Added

- `scaffold/acme_root/`, the domain-agnostic core: tenancy with the
  operator plane, events and audit, the outbox, idempotency, the work
  queue, orchestrations, and media on four database roles; the API and
  its realtime socket, the maintenance worker, a portal shell, the CLI,
  a landing site, both clients, the ops package and its skills, the
  local stack, Terraform, CI and deploy workflows, and 48 ADRs. CI
  copies it and runs the copy's own gates.
- `scaffold/new.py <dir>/<name>` copies it under a name of one or two
  snake_case words, at most 17 characters, in every form the name
  takes, pins the guideline release, and links `.claude/skills` to
  `.agents/skills` in the copy.
- A lens may name the scaffold file that shows its rule, on a
  `**Shape.**` line, and `make lenses` holds each path.
- Two routes to install the skills: the Claude Code plugin, and for any
  agent that reads the Agent Skills standard, a clone with each skill
  linked into the project's `.agents/skills/`.
- A skill a person starts by name carries Codex's switch,
  `agents/openai.yaml` with `policy.allow_implicit_invocation: false`,
  beside `disable-model-invocation`.
- `make skills` holds every skill to the standard: its frontmatter
  keys, its name, the two switches agreeing, and every path it names
  resolving from its folder.
- `make snippets` fails when a Python or YAML block in the Markdown
  does not parse.

### Fixed

- Two fields of the `SecurityContext` and `OutboxRow` snippets sat one
  level too deep.
