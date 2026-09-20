# Working in this repository

This repository holds one guideline (`architecture.md`), a lens
catalog derived from it (`lenses/`), Claude Code skills that apply the
lenses (`skills/`), and the checkers that keep the three consistent
(`scripts/`, `Makefile`).

## Layout

- `architecture.md` is the source of truth. Every rule in a lens or a
  skill restates a sentence in it; nothing adds a rule the guideline
  does not state.
- `lenses/<group>.md` holds one group of lenses in the format
  `lenses/README.md` defines. Ids are `<PREFIX>-NN`; every lens cites
  `Section title, Subsection`, by title and never by number.
- `skills/arch-review-<group>/SKILL.md` is generated from
  `skills/_template/review.SKILL.md`; edit the template and run
  `make gen-skills`. The other skills are hand-written and share
  `skills/_shared/scaffold-conventions.md`. `skills/arch-new-aspect`
  is the one skill that edits this repository itself: it incorporates
  a new aspect into the guideline and cascades it through the lenses,
  skills, docs, and changelog.
- `agents/arch-reviewer.md` is the subagent `arch-review-full` fans out
  to. Its procedure and report shape mirror the review template, and
  `scripts/check_agents.py` holds the two together: the four decision
  words, the report block, and the count of procedure steps must
  agree. The sentence "Never edit, stage, or commit" is repeated in
  every review skill on purpose.
- `.claude-plugin/` holds the plugin and marketplace manifests. The
  repository root is the plugin. `plugin.json` carries the one release
  version; `scripts/check_version.py` holds the marketplace manifest,
  the changelog, and `docs/adopting.md` to it.
- `scripts/_common.py` holds what the scripts share, the heading
  anchor rule above all: the generator that writes anchors and the
  checker that resolves them use the same function. `tests/` holds one
  pytest module per script, each on a small fixture tree, with a pass
  and a fail path per rule; `make test` runs them.

## Invariants

- No product or hardware vocabulary in the guideline, the lenses, the
  skills, the docs, the agents, or this file (`scripts/check_leaks.py`
  lists the terms). The product list is a regression guard for the
  vocabulary of the one origin the guideline was extracted from, not a
  general check: it catches that vocabulary flowing back in, and a
  fork replaces it with its own. Agents are named as agents.
- No history in the guideline: it states what we do, in the present
  tense, with no rejected alternatives and no changelog phrasing.
- No em-dashes anywhere.
- No section numbers anywhere: headings are unnumbered, and every
  cross-reference (in the guideline, the lenses, the skills, the docs)
  names the section by title; inside the guideline it is a named
  anchor link. Numbers shift when a section is inserted; titles do not.
- The guideline's Contents block is generated (`make gen-toc`) and
  checked (`make toc`).
- Every lens cites a section and subsection that exist.
- Every skill's `name` equals its folder name and starts with `arch-`;
  every `${CLAUDE_SKILL_DIR}/...` reference resolves; frontmatter is
  flat `key: value` lines; descriptions are one complete double-quoted
  string; `allowed-tools` is comma-separated, the `Bash(cmd:*)` prefix
  form is house style (an exact `Bash(make check)` is accepted too),
  and it names only what the skill runs. `Bash(uv run:*)` and
  `Bash(pnpm run:*)` are a shell in practice: either runs whatever the
  workspace holds. They stay listed because a scaffold has to run the
  project's own tools through the workspace, and naming them says so
  in the frontmatter instead of hiding it behind a bare `Bash`.
- Scaffold skills have the five sections Input, Created, Changed,
  Procedure, Output, in that order (`scripts/check_skills.py` holds
  them to it).
- The release version is written once, in `.claude-plugin/plugin.json`;
  every other copy is checked against it.
- Scaffold skills share `skills/_shared/scaffold-conventions.md`.
- Exactly one review skill per lens group; `arch-review-full` names all
  of them.

## Validate

```bash
make check                       # everything CI runs
claude plugin validate . --strict   # manifests, skills, agents (when claude is installed)
```

## Conventions

- Wrap prose at about 72 columns in the guideline and the lenses.
- A concept the guideline uses before the section that defines it
  carries a named anchor link to that section at its first mention.
- A code snippet that shows a root with "one getter per X" shows two
  getters and a `# ...` line, so the pattern reads at a glance.
- When a fix is applied to one instance, search the repository for
  its siblings and fix them in the same change.
- When the guideline renames an identifier or changes a shape, grep
  every scaffold skill and `agents/` for the old spelling and fix them
  in the same change; no checker does this yet.
- Commit messages: a specific subject line, a short body naming the
  rule that changed and why.
- A change that removes or reverses a rule is a major release; one
  that adds or sharpens a rule is a minor release; before 1.0.0 a
  removed or reversed rule bumps the minor number, as semver reads
  0.x, and the changelog entry names the reversal (`CONTRIBUTING.md`,
  Versioning). Record it in `CHANGELOG.md`.
