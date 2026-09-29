# Software Design and Architecture Guidelines

An opinionated guideline for building multi-tenant, service-based
systems in Python, and the tools that hold a project to it.

- **[`architecture.md`](architecture.md)**: the guideline. It tells the
  story, from the object model at the center to deployment at the edge.
  Start with its [Core](architecture.md#the-core).
- **[`scaffold/`](scaffold/README.md)**: the domain-agnostic core of a
  system in this shape, a whole monorepo that runs. A new project
  copies it.
- **[`lenses/`](lenses/README.md)**: 258 lenses in eight groups, the
  checkable detail under each rule.
- **[`checkers/`](checkers/README.md)**: `arch-check`, the static
  checker. It decides the lenses a program can decide, in a second.
- **[`skills/`](skills/)**: Claude Code skills that review a change
  through the lenses, add a piece in the prescribed shape, and record a
  deviation.
- **[`benchmark/`](benchmark/README.md)**: the harness that has
  frontier models score a subject against a rubric.

## How this repository is written

The guideline and the READMEs have two readers, a person and an agent.
They tell the story: what each part is, and why. They point at the
detail rather than spell it out. Nuance that only an agent needs sits
in a short `<!-- agents-only -->` comment, which a rendered page hides.
The lenses and the skills are read by agents. They may be exact, and
they point at the scaffold's files rather than describe code.

A section that deserves one carries a tag, defined in [How to Read
This](architecture.md#how-to-read-this). A `core` section never bends:
a departure is a different architecture. A `default` is swapped by a
substitution, an `optional` section waits for its trigger, and a
`style` departure is a low finding at most. Untagged text is the rule:
a project that departs from it records a deviation in an ADR
(`arch-deviate`). That is where an agent may adapt, and how.

The detail lives in the tools, and that is a choice. The guideline
tells the story. The lenses and the skills hold the detail under each
rule, and they are stricter than the story on purpose. The checker and
the gates are stricter still. A tool may be stricter than the section
it cites, and never contrary to it. So the story stays clean, and the
tools carry the detail.

The scaffold shows the shape. Where the text would describe code, it
links a file under `scaffold/acme_root/`. A team copies the scaffold
rather than writing the shape from the text. `arch-check` is a plain
command that needs only Python, so it runs in any CI, with or without
Claude Code.

## Start a project

```bash
python3 scaffold/new.py ~/code/pressroom
cd ~/code/pressroom && make setup && make check
```

The copy carries its own gates, `arch-check` among them, pinned at this
release. For an existing codebase, [`docs/adopting.md`](docs/adopting.md)
says what to adopt first and what can wait.

## Run the checker

```bash
uvx --python "$(cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.38.0#subdirectory=checkers" arch-check
```

It exits non-zero on a finding, and `--format json` prints one JSON
document. [`checkers/README.md`](checkers/README.md) has its flags and
its configuration.

## Install the skills

The repository is a Claude Code plugin marketplace:

```text
/plugin marketplace add baristaze/swe_guidelines
/plugin install swe-guidelines@swe-guidelines
```

Update before a scaffold or a review, since a skill reads the text it
shipped with:

```text
/plugin marketplace update swe-guidelines
/plugin update swe-guidelines
/reload-plugins
```

## The skills

| Skill                     | What it does                                          |
|---------------------------|-------------------------------------------------------|
| `arch-review-full`        | All eight lens groups in parallel, one report         |
| `arch-review-<group>`     | One group: `om`, `contracts`, `context`, `storage`, `async`, `network`, `delivery`, `ops` |
| `arch-scaffold-new`       | Copies the scaffold, runs its gates, adds the first namespace and its entity |
| `arch-scaffold-namespace` | A new object-model swimlane                           |
| `arch-scaffold-entity`    | One entity end to end, from type to API               |
| `arch-scaffold-service`   | A web service                                         |
| `arch-scaffold-worker`    | A worker role over the work queue                     |
| `arch-scaffold-app`       | A browser app, an operator console, or a CLI          |
| `arch-explain`            | Answers a question about the architecture             |
| `arch-deviate`            | Records a deviation as an ADR in the project          |
| `arch-upgrade-deps`       | Moves every dependency to its latest stable release   |
| `arch-new-aspect`         | Grows the guideline and cascades it through the tools |
| `arch-benchmark`          | Runs a benchmark scenario                             |
| `arch-benchmark-browser`  | Asks the chat products a reader would use             |

The last three run in a checkout of this repository. A review reads and
reports; it never edits. A scaffold writes into the working tree and
never commits.

## Develop

```bash
make check        # everything CI runs
make gen-skills   # regenerate the review skills from the template and the lenses
make gen-toc      # regenerate the table of contents of architecture.md
make benchmark    # the smoke scenario in a container; calls paid APIs, not part of check
```

[`CONTRIBUTING.md`](CONTRIBUTING.md) says what `make check` needs and
how a change lands.

## License

MIT. See [`LICENSE`](LICENSE).
