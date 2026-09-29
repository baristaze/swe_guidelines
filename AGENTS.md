# Working in this repository

This repository holds one guideline (`architecture.md`), the scaffold
that shows its shape (`scaffold/`), the lenses and the skills that hold
its detail (`lenses/`, `skills/`), the static checker (`checkers/`), the
benchmark harness (`benchmark/`), and the scripts that keep them
consistent (`scripts/`, `Makefile`). Each script's docstring states
what it holds. Read it before you change what it checks.

## The ladder

The guideline tells the story and states every rule. A lens or a skill
holds the checkable detail under a rule the guideline states. It is
stricter than the story on purpose, never contrary to it, and it never
adds a rule the guideline does not state. The checker and the gates are
stricter still. When the guideline changes, every lens and skill that
cites the changed text changes with it.

## Writing

- A person and an agent read `architecture.md`, the READMEs, and
  `docs/`. They tell the story and point at the detail: short
  sentences, one idea each, in the present tense, with no history.
- Only agents read the lenses, the skills, and `agents/`. They may be
  exact, and they name the scaffold file whose shape to follow.
- Detail goes where it is held: code to the scaffold, checkable detail
  to a lens, steps and edge cases to a skill. What only an agent needs
  on a person's page goes in a short agents-only block, opened by
  `<!-- agents-only` alone on its line; nothing inside may close it.
  Tags follow How to Read This, in `architecture.md`.

## Layout

- `architecture.md` is the source of truth. Its Contents block is
  generated (`make gen-toc`) and checked (`make toc`). Headings are
  unnumbered, and every cross-reference names a section by its title.
- `scaffold/acme_root/` is the domain-agnostic core of a system in the
  guideline's shape: a whole monorepo named `acme`, with its own gates.
  `scaffold/new.py` copies it under a project's name, standard library
  only, and `tests/test_scaffold_new.py` holds it. The repository's ruff
  and mypy read `new.py` alone; markdownlint, the links, and the leaks
  read the whole scaffold; CI's `scaffold` job runs a fresh copy's own
  gates, with arch-check from `checkers/`.
- `lenses/<group>.md` holds one group of lenses in the format
  `lenses/README.md` defines. `make lenses` holds the format, the
  citations, every identifier a lens quotes to the section it cites, and
  every scaffold path a `Shape` line names.
- `skills/arch-review-<group>/SKILL.md` is generated from
  `skills/_template/review.SKILL.md`: edit the template or the lenses,
  and run `make gen-skills`. The other skills are hand-written. The six
  scaffold skills share `skills/_shared/scaffold-conventions.md`. Each
  names the files of the tree whose shape it follows, and
  `arch-scaffold-new` copies the scaffold with `scaffold/new.py`.
  `skills/arch-new-aspect` is the one skill that edits this repository:
  it adds an aspect to the guideline and cascades it.
- `scaffold/acme_root/.agents/skills/` holds the skills a new tree runs
  as its own: the operational skills, the audits, and the ticket
  triage. They are not skills of this plugin. `.agents/skills/` is the
  folder every agent that reads the Agent Skills standard shares;
  `scaffold/acme_root/.claude/skills` is a link to it, for Claude Code,
  and `scaffold/new.py` copies the link as a link. `make leaks`, `make
  links`, and `make lint` read the skills, and `scripts/check_skills.py`
  holds their frontmatter, their paths, the link, and their count
  bounds as a skill's.
- `agents/arch-reviewer.md` is the Claude Code subagent
  `arch-review-full` fans out to; subagents have no open standard, so in
  another agent the skill starts a general subagent or runs the groups
  one after another. `scripts/check_agents.py` holds it to the review
  template and to its `maxTurns`. Every review skill repeats "Never edit, stage, or
  commit" on purpose.
- `.claude-plugin/` holds the plugin and marketplace manifests; the
  repository root is the plugin. `plugin.json` carries the one release
  version, and `scripts/check_version.py` holds every copy to it.
- `benchmark/` holds the harness that scores a subject against a
  rubric. A harness module imports only the standard library at import
  time, so its tests run with nothing installed. A run folder is checked
  in under `benchmark/runs/<scenario>/` once `run.py redact` has scanned
  it, with its row added by hand; `scripts/check_runs.py` holds the
  rest. `make benchmark` calls paid APIs and is not part of `make check`.
- `checkers/` holds `arch-check`, its own package
  (`checkers/src/arch_check/`, Python 3.11, standard library only). A
  rule is one module under `rules/`, and its id is the id of the lens it
  decides. `lenses.py` carries every lens id and severity, and
  `tests/test_arch_check_framework.py` holds it equal to `lenses/*.md`.
- `scripts/_common.py` holds what the scripts share: the heading anchor
  rule, the one list of the repository's Markdown (`markdown_files`),
  the argument parser, and the fence rule (`fenced_lines`).
- `tests/` holds one pytest module per script, with a pass and a fail
  path per rule; `make test` runs them.

## Invariants

- No product or hardware vocabulary anywhere the leaks check reads
  (`scripts/check_leaks.py` lists the terms and the files). Agents are
  named as agents.
- "X, never Y" names the near miss a rule rules out. It is part of the
  rule, not history.
- Every skill follows the [Agent Skills
  standard](https://agentskills.io/specification), so it runs in any
  agent that reads it. Its frontmatter holds the standard's fields and
  `disable-model-invocation`, nothing else. It names its own files by a
  path from its own folder (`../../architecture.md`,
  `references/<file>`), never through a path one agent substitutes,
  such as `${CLAUDE_SKILL_DIR}`, and a command runs such a file by its
  absolute path. Its arguments are "the arguments", never `$ARGUMENTS`.
- Every plugin skill's `name` equals its folder name and starts with
  `arch-`, and its `allowed-tools` names only what its body runs.
  `scripts/check_skills.py` holds the frontmatter and the paths; the
  git, uv, and pnpm entries are held by hand.
- A scaffold skill has the sections Input, Created, Changed, Procedure,
  and Output, in that order.
- A skill keeps its spine inline and moves long reference material into
  `skills/<name>/references/`, named by the step that reads it.
- A step that fixes and runs again states its bound: the first run plus
  at most 3 reruns, then stop and say which gate fails and why.
- Exactly one review skill per lens group, and `arch-review-full` names
  all of them.

## Validate

```bash
make check                          # everything CI runs
make checkers-dist                  # builds the arch-check wheel and runs its entry point
claude plugin validate . --strict   # manifests, skills, agents (when claude is installed)
```

CI also runs `make test` and `make checkers-dist` on Python 3.11,
arch-check's floor. `.github/pins/` holds every tool version, and
dependabot updates them.

## Conventions

- Prose wraps at about 72 columns. A concept the guideline uses before
  the section that defines it links that section at its first mention.
- A fix applied to one instance is searched for its siblings, and they
  are fixed in the same change.
- When the guideline renames an identifier or changes a shape, grep the
  scaffold skills and `agents/` for the old spelling; no checker does.
- A commit message has a specific subject and a short body naming the
  rule that changed and why.
- A change never edits `CHANGELOG.md` (`scripts/check_changelog.py`).
  Its pull request description carries what the release needs: what
  changed, the level `CONTRIBUTING.md` (Versioning) gives it, and a
  reversal named as one.
