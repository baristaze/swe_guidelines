# Contributing

Thank you for taking the time. This repository holds one opinionated
guideline and the tooling that keeps it checkable. Contributions that
sharpen a rule, fix an inconsistency, or improve a skill are welcome.
Contributions that add a rule need a reason the guideline can state in
its own voice.

## Ground rules

- The guideline (`architecture.md`) states what we do. It carries no
  history and no survey of the alternatives it weighed; naming the
  near miss a rule rules out ("X, never Y") is part of the rule and
  stays. It names its technologies on purpose, and what it carries no
  trace of is the vocabulary of any one product: `make leaks` is a
  regression guard for the vocabulary of the origin this guideline was
  extracted from, and a fork replaces that list with its own.
- Every rule lives in exactly one lens under `lenses/`, and every lens
  cites the section it restates. `make lenses` enforces the format and
  the citations.
- The review skills are generated from `skills/_template/` and the lens
  files. Edit the template or the lenses, run `make gen-skills`, and
  commit the result. `make gen-skills-check` fails when a generated
  skill is stale.
- Readability outranks density. A sentence a senior reader has to
  parse twice has failed, however well it is built. Short sentences,
  one idea each; a chain of clauses joined by commas is the thing to
  break up. A paragraph has no cap, because paragraph length is not
  the problem and sentence length is. A longer document is the right
  trade for a document that gets read.
- No em-dashes anywhere. Wrap prose at about 72 columns in the
  guideline; the lens files and skills follow the same habit.

## Workflow

1. Open an issue first for anything beyond a typo, so the change can be
   discussed as a rule before it is discussed as a diff.
2. Fork, branch from `main`, make the change.
3. Run `make check`. It needs Python 3.14 with `pytest`, and Node 24
   (the current LTS); markdownlint is fetched by `npx` on first run,
   and the plugin validation runs when `claude` is installed.
4. Open a pull request. Describe the rule that changes and why, in the
   same voice as the guideline. Link the issue.

## What a good change looks like

- A rule becomes more precise, or gains its named exception.
- A lens's "Look for" or "Violation" text becomes concrete enough that
  two reviewers would flag the same line.
- A skill produces the same report shape as its siblings.
- A checker catches a class of mistake that a reviewer used to catch by
  hand.

## Versioning

Releases are tagged `vMAJOR.MINOR.PATCH`. A change that removes or
reverses a rule is a major release. A change that adds or sharpens a
rule is a minor release. Everything else is a patch. Before 1.0.0 a
removed or reversed rule bumps the minor number, as semver reads
0.x, and the changelog entry names the reversal; 1.0.0 is for the
text that has stopped moving. `CHANGELOG.md` lists every release.

## License

By contributing you agree that your contribution is licensed under the
MIT License in `LICENSE`.
