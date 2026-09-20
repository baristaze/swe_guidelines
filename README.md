# Software Design and Architecture Guidelines

An opinionated guideline for building multi-tenant, service-based
systems in Python, plus the tooling that makes it checkable: a catalog
of review lenses derived from the guideline and a set of Claude Code
skills that review code through those lenses or scaffold new pieces in
the prescribed shape.

- **[`architecture.md`](architecture.md)**: the guideline, from the
  object model at the center to deployment at the edge, with a table
  of contents and named anchor links between sections. Read it once
  end to end; it is written to be read that way. It names its
  technologies on purpose, says how a project substitutes its own,
  says which team it is written for and up to which limits the system
  grows by deployment changes alone, and ends with a pointer to a
  reference implementation that applies the whole document to one
  small project.
- **[`lenses/`](lenses/README.md)**: 191 lenses in seven groups. Each
  restates one rule as something a reviewer can check against code and
  cites the section it comes from, by title.
- **[`skills/`](skills/)**: Claude Code skills. Seven group reviews, one
  full review that runs them in parallel, six scaffolds, an explainer,
  a deviation recorder, a dependency upgrader, and one skill that grows
  the guideline itself.

## Install the skills

The repository is a Claude Code plugin marketplace. Inside Claude Code:

```text
/plugin marketplace add baristaze/swe_guidelines
/plugin install swe-guidelines@swe-guidelines
```

Before a scaffold or a review, update; a scaffold reads the version it
shipped with and names it in its output:

```text
/plugin marketplace update swe-guidelines
/plugin update swe-guidelines
/reload-plugins
```

Skills then appear as `/swe-guidelines:arch-review-full` and so on. To
try a checkout without installing:

```bash
claude --plugin-dir /path/to/swe_guidelines
```

Non-plugin use, version pinning, and the pointer file a project keeps
in its own `specs/` folder are in [`docs/adopting.md`](docs/adopting.md).

## The skills

| Skill                     | What it does                                                                 |
|---------------------------|------------------------------------------------------------------------------|
| `arch-review-full`        | Reviews a change through every lens group, seven reviewers in parallel, one merged report |
| `arch-review-om`          | Object model: source of truth, mixins, immutability, identifiers, namespaces |
| `arch-review-contracts`   | Interfaces, injection, roots, call direction, app container                  |
| `arch-review-context`     | context stages and scopes, OperatorContext, authorization, tenancy, provenance  |
| `arch-review-storage`     | Storage principles, tables, translation, database roles, migrations          |
| `arch-review-async`       | Infra capabilities, queues, workers, idempotency, park versus fail           |
| `arch-review-network`     | Topology, gateway, public types, clients, realtime, push-first               |
| `arch-review-delivery`    | Apps, deployment, repo layout, client architecture, cross-cutting conventions |
| `arch-scaffold-new`       | Bootstraps a whole system by sequencing the scaffolds below                  |
| `arch-scaffold-namespace` | A new object-model swimlane, wired into the roots                            |
| `arch-scaffold-entity`    | One entity end to end: type, table, storage, manager, migration, API, tests  |
| `arch-scaffold-service`   | A web service: container, gateway, routers, wire types, ops CLI, image       |
| `arch-scaffold-worker`    | A worker role over the table-backed work queue                               |
| `arch-scaffold-app`       | A browser app, an operator console, or a CLI                                 |
| `arch-explain`            | Answers a question about the architecture with citations                     |
| `arch-deviate`            | Records a deliberate deviation as an ADR in the consuming project            |
| `arch-upgrade-deps`       | Moves every dependency to its latest stable or LTS release and runs the gates |
| `arch-new-aspect`         | Incorporates a new aspect into the guideline and cascades it through lenses, skills, and docs (runs in a checkout of this repository) |

Every review skill takes the same argument (empty for the current
branch, a path, a git range, or `all`). The seven group skills produce
the same report shape, so their reports merge cleanly; the full review
adds a per-group table. The review skills read and report; they never
edit. The scaffold skills write into the working tree and never commit.
The subagent tool the full review fans out with is called `Agent` in
Claude Code 2.1 and later.

## Develop

```bash
make check        # what CI runs: markdownlint, lens format and citations, vocabulary leaks, links,
                  # paragraph length, table of contents, version copies, generated skills up to date,
                  # skill shape, the reviewer agent against the review template, the checkers' tests,
                  # plugin validation
make gen-skills   # regenerate the seven group review skills from the template
make gen-toc      # regenerate the table of contents of architecture.md
```

Requirements: Python 3.14 and Node 24, the latest stable and LTS
releases. The scripts need nothing past the standard library; `make
check` also needs `pytest`, for the scripts' own tests, and `npx`, for
markdownlint (`make lint` fetches `markdownlint-cli2` through `npx` at
a pinned version). The pin follows the guideline's own latest-stable
rule on purpose. CI runs `make check` on every pull request.

The review skills are generated from `skills/_template/review.SKILL.md`
and the lens catalog. Edit the template or the lenses, not the
generated files. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT. See [`LICENSE`](LICENSE).
