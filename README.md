# Software Design and Architecture Guidelines

An opinionated guideline for building multi-tenant, service-based
systems in Python, and the tools that hold a project to it.

- **[`architecture.md`](architecture.md)**: the guideline. It tells the
  story, from the object model at the center to deployment at the edge.
  Start with its [Core](architecture.md#the-core).
- **[`scaffold/`](scaffold/README.md)**: the domain-agnostic core of a
  system in this shape, a whole monorepo that runs. A new project
  copies it.
- **[`lenses/`](lenses/README.md)**: the checkable detail under each
  rule, in eight groups of lenses, one file each.
- **[`checkers/`](checkers/README.md)**: `arch-check`, the static
  checker. It decides the lenses a program can decide, in a second.
- **[`skills/`](skills/)**: skills that review a change through the
  lenses, add a piece in the prescribed shape, and record a deviation.
  They follow the [Agent Skills](https://agentskills.io/specification)
  standard, so they run in Claude Code and in any agent that reads it.
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
uvx --python "$(cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.65.0#subdirectory=checkers" arch-check
```

It exits non-zero on a finding, and `--format json` prints one JSON
document. [`checkers/README.md`](checkers/README.md) has its flags and
its configuration.

## Install the skills

There are two routes. In Claude Code, the repository is a plugin
marketplace:

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

In any other agent that reads the standard, such as Codex, clone the
repository beside the project and link each skill into the project's
`.agents/skills/`:

```bash
git clone https://github.com/baristaze/swe_guidelines ../swe_guidelines
mkdir -p .agents/skills
for s in ../swe_guidelines/skills/arch-*; do ln -s "$(cd "$s" && pwd)" ".agents/skills/$(basename "$s")"; done
echo '.agents/skills/arch-*' >> .git/info/exclude
```

A skill reads the guideline and the lenses by their paths from its own
folder, so through the link it reads the clone. The links hold this
machine's paths, so they stay out of the project's history.
`git -C ../swe_guidelines pull` updates the skills.

## The skills

Each skill is a folder under [`skills/`](skills/), and its `SKILL.md`
opens with what it does. The name says the family:

- `arch-review-<group>` reviews a change through one lens group, and
  `arch-review-full` runs every group in parallel into one report.
- `arch-scaffold-<piece>` adds the piece it names in the prescribed
  shape. `arch-scaffold-new` starts a project: it copies the scaffold,
  runs its gates, and adds the first namespace and its entity.
- `arch-upgrade-<what>` moves what it names to a later release:
  `arch-upgrade-deps` the dependencies, and `arch-upgrade-scaffold` a
  copy of the scaffold, or a layer on it, by a merge.
- `arch-explain` answers a question about the architecture, and
  `arch-deviate` records a deviation as an ADR in the project.
- `arch-new-aspect` grows the guideline and cascades it through the
  tools, and `arch-benchmark` and the skills named after it run a
  benchmark. These run in a checkout of this repository, started by
  name.

A review reads and reports; it never edits, and it runs no file of the
repository it reviews. A scaffold writes into the working tree and
never commits. `arch-upgrade-scaffold` commits its move, since the
merge is the move.

## Develop

```bash
make help         # every target, with what it does
make check        # everything CI runs
make gen-skills   # regenerate the review skills from the template and the lenses
make gen-toc      # regenerate the table of contents of architecture.md
make benchmark    # the smoke scenario in a container; calls paid APIs, not part of check
```

[`CONTRIBUTING.md`](CONTRIBUTING.md) says what `make check` needs and
how a change lands.

## License

MIT. See [`LICENSE`](LICENSE).
