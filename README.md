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
- **[`lenses/`](lenses/README.md)**: 243 lenses in eight groups. Each
  restates one rule as something a reviewer can check against code and
  cites the section it comes from, by title.
- **[`skills/`](skills/)**: Claude Code skills. Eight group reviews, one
  full review that runs them in parallel, six scaffolds, an explainer,
  a deviation recorder, a dependency upgrader, one skill that grows the
  guideline itself, one that measures a skill against a rubric, and one
  that asks the products a reader would use.
- **[`checkers/`](checkers/README.md)**: `arch-check`, the static
  checker. It decides the lenses a program can decide, from the source,
  in a second, with nothing but Python 3.11. The review skills run it
  first and judge the rest.
- **[`benchmark/`](benchmark/README.md)**: the harness that runs a
  subject, keeps what happened, and has frontier models from several
  providers score it against a rubric. It is not part of `make check`:
  a run costs money and takes minutes.

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

## Run the checker

In a project, pinned at the guideline's tag, in the `Makefile`'s fast
gate:

```make
arch-check: ## the guideline's static checks
	uvx --python "$(shell cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.28.0\#subdirectory=checkers" arch-check
```

and in the root `pyproject.toml`:

```toml
[tool.arch-check]
package = "acme"
```

The scaffolds write both. Options, exceptions, and project-local rules
are in [`docs/adopting.md`](docs/adopting.md#run-the-checker).

## The skills

| Skill                     | What it does                                                                 |
|---------------------------|------------------------------------------------------------------------------|
| `arch-review-full`        | Reviews a change through every lens group, eight reviewers in parallel, one merged report |
| `arch-review-om`          | Object model: source of truth, mixins, immutability, identifiers, namespaces, pure rules |
| `arch-review-contracts`   | Interfaces, injection, roots, call direction, app container                  |
| `arch-review-context`     | context stages and scopes, OperatorContext, authorization, tenancy, provenance  |
| `arch-review-storage`     | Storage principles, tables, translation, database roles, migrations          |
| `arch-review-async`       | Infra capabilities, queues, workers, idempotency, park versus fail           |
| `arch-review-network`     | Topology, gateway, public types, clients, realtime, push-first               |
| `arch-review-delivery`    | Apps, deployment, repo layout, client architecture, logs and telemetry, cross-cutting conventions, substitutions |
| `arch-review-ops`         | Operator roles and credentials, operational skills, dashboards and alarms, scale-out, cost, traffic, READMEs, the knowledge map |
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
| `arch-benchmark`          | Runs a benchmark scenario from this checkout and reports what the frontier models scored it (runs in a checkout of this repository) |
| `arch-benchmark-browser`  | Runs the benchmark prompt through chatgpt.com, claude.ai, and gemini.google.com in a signed-in browser, two t-shirt sizes for the model and the effort, and saves each answer with its conversation URL |

Every review skill takes the same argument (empty for the current
branch, a path, a git range, or `all`). The eight group skills produce
the same report shape, so their reports merge cleanly; the full review
adds a per-group table. The review skills read and report; they never
edit. The scaffold skills write into the working tree and never commit.
The subagent tool the full review fans out with is called `Agent` in
Claude Code 2.1 and later.

## Develop

```bash
make benchmark    # the smoke scenario, judged by two providers, Anthropic and OpenAI (costs money, not part of check)
make check        # what CI runs: markdownlint, ruff and mypy over scripts/, benchmark/, checkers/, and tests/, lens format and citations,
                  # vocabulary leaks, links, table of contents, version copies, generated skills up to date,
                  # skill shape, the reviewer agent against the review template, the checkers' tests,
                  # plugin validation
make gen-skills   # regenerate the eight group review skills from the template
make gen-toc      # regenerate the table of contents of architecture.md
```

Requirements: Python 3.10 or newer, and Node 24, the current LTS.
CI runs the checks on Python 3.10, the floor `pyproject.toml`
declares, and on 3.14, the latest stable release. The scripts need
nothing past the standard library; `make check` also needs `pytest`, for the scripts' own tests; `npx`, for
markdownlint (`make lint` fetches `markdownlint-cli2` through `npx` at
a pinned version); and `uv`, for ruff and mypy (`make ruff` and `make
mypy` fetch them through `uvx` at pinned versions). The pins follow
the guideline's own latest-stable rule on purpose. CI runs `make check`
on every pull request.

The review skills are generated from `skills/_template/review.SKILL.md`
and the lens catalog. Edit the template or the lenses, not the
generated files. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT. See [`LICENSE`](LICENSE).
