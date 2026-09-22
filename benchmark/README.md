# Benchmark

A harness that runs a subject, keeps what happened, and has frontier
models judge the result against a rubric. The subject is a skill of
this plugin, a command, or a question answered by a model. The judges
are Anthropic, OpenAI, Gemini, and xAI, each answering in the same
structured shape, so their scores compare.

It replaces the loop of pasting a prompt into a provider's console and
copying the answer back. That loop is not repeatable, leaves no record,
and asks one model.

## Run it

```bash
uv run benchmark/run.py list
uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1
uv run benchmark/serve.py --runs benchmark/runs --port 8765
```

`uv run` reads the inline dependencies at the top of `run.py`, so there
is nothing to install first. The harness modules import the standard
library only; the provider clients and the YAML and schema packages are
imported inside the functions that call them.

| Flag | What it does |
|------|--------------|
| `--scenario` | a scenario name from `scenarios/`, or a path to a file |
| `--providers` | the judges, as a bit flag (`3`, `7`, `15`), names (`anthropic,openai`), or `all` |
| `--effort` | `low`, `medium`, or `high`; `models.yaml` maps it per provider |
| `--repeat` | how many times the subject runs; every repeat is judged by every provider |
| `--runtime` | `host`, `container`, or `vm` |
| `--runtime-config` | a JSON or YAML file with the runtime's settings |
| `--target` | a checkout the subject works on, in place of the scenario's own |
| `--out` | where run folders go; `benchmark/runs/` by default, which git ignores |
| `--claude` | the Claude Code binary a skill subject runs; `$CLAUDE_BIN`, else `claude` |
| `--dry-run` | resolve everything, write `run.json`, call no provider and run no subject |
| `--strict` | a provider without a key fails the run instead of being skipped |
| `--build` | build the container image before running |
| `--screencast-port` | capture frames from a Chrome already listening on that debugging port |
| `--screencast-seconds` | how long to capture frames; 10 by default |

A provider whose key is absent is skipped, named in the results, and
does not fail the run. That is a choice: a run with three judges is
worth more than no run at all. `--strict` reverses it.

A provider that answers some judgements and misses others, to a
timeout or a `429`, is named under `missed` with the count and the
first reason, and does not fail the run, `--strict` or not: one flaky
answer is a note, not a lost run. `--strict` fails on a provider that
answered none. The overall mean is the mean of the providers' means,
so each provider weighs once, however many judgements it answered.

Keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, and
`XAI_API_KEY` with `GROK_API_KEY` as a second name. The Gemini client
is handed `GEMINI_API_KEY` and the ambient `GOOGLE_API_KEY` is taken
out of its way, because those two names often hold different accounts.
The judges' keys stay in the harness process. A subject that runs a
command, a skill's `claude -p` included, is handed `ANTHROPIC_API_KEY`
and no other key, in its environment on the host and by `-e` in a
container.

## What a run leaves behind

```text
runs/<YYYYMMDD-HHMMSS>-<scenario>/
  run.json                 the resolved scenario, runtime, models, and argv
  streams/cli.jsonl        one JSON line per output line, written as it happens
  streams/build.jsonl      the image build's output, with `--runtime container --build`
  streams/browser/         frames and index.jsonl, when something captured them
  artifacts/<repeat>/      the answer, the judge prompt, and the collected
                           files under workspace/ at their own paths
  judgements/<repeat>-<provider>.json
  results.json             the record, in schema/result.schema.json
  report.md                the same run for a person
```

The subject never works in the run folder. It lives in a sandbox
outside the checkout, a fresh temporary folder per run:

```text
<sandbox>/
  plugin/                  a copy of the plugin payload only: the manifest,
                           skills, agents, lenses, architecture.md, checkers
  target/                  a copy of the target folder, without its siblings
  workspace/<repeat>/      what the subject worked in, empty at the start
  home/<repeat>/, tmp/<repeat>/  the host runtime's private HOME and TMPDIR
```

So no answer key, no earlier repeat's judge prompt, and no
`CLAUDE.md` of the checkout is in the subject's reach. What a scenario
collects from the workspace is copied into `artifacts/`, and the
sandbox is removed when the run ends, however it ends. Every repeat
starts in an empty workspace of its own, so no repeat sees what an
earlier one wrote.

Nothing there is checked in. The manual is the repository; a run is a
measurement.

## Runtimes

- `host` runs the subject on this machine with a private `HOME` and a
  private `TMPDIR` in the sandbox. The isolation is a convention, not a
  boundary: it keeps a subject from writing into the operator's account
  by accident, and stops nothing that means to. The subject runs in a
  process group of its own, and the group is killed when the subject
  ends, on a timeout, a clean exit that left children, or an interrupt.
- `container` runs `docker run --rm` from the image
  `runtime/Dockerfile` builds. The staged plugin is mounted read-only
  at `/plugin`, the staged target read-only at `/target`, and the
  workspace read-write at `/workspace`.
- `vm` runs the command on another machine through a configured
  prefix, for example `["limactl", "shell", "default", "--"]`, with a
  configured sync command. The harness provisions no machine and
  starts none; it composes the prefix and the sync. It copies neither
  the plugin checkout nor the target either: `remote_plugin` and
  `remote_target` in the runtime config say where they are on that
  machine, and a run that needs one and is not told is refused before
  it starts. Each repeat gets its own folder under `remote_workspace`
  (`{remote}` in the sync and fetch commands names it), and the
  subject runs inside that folder, so what it writes is what fetch
  brings back. The prefix has to hand its words on as words, as
  `limactl shell` and `docker exec` do; `ssh` joins them into one
  remote shell line and needs a wrapper.

A path on this machine means nothing in a container or on another
machine. So the runtime answers where the plugin checkout and the
target are as the subject sees them, and those are the paths the
subject is given: in `--plugin-dir`, in `--add-dir`, and in the prompt.

All three write the same streams into the run folder.

## Scenarios

A scenario is YAML or JSON. Three ship here:

| Scenario | Kind | What it measures |
|----------|------|------------------|
| `explain-tenancy` | `skill` | the `arch-explain` skill on one question about the tenant fence; the cheap one to run first |
| `review-om` | `skill` | the `arch-review-om` skill over a checkout with eight planted defects |
| `support-turn` | `qa` | a model answering an on-call question directly, with no skill |

The shape:

```yaml
name: explain-tenancy
kind: skill                 # skill | command | qa
subject:
  skill: arch-explain
  prompt: "How does the guideline hold the tenant fence, and what proves it?"
  max_turns: 14
  allowed_tools: [Read, Grep, Glob]
  target: null
artifact:
  stdout: true
  files: []                 # globs collected from the workspace after the run
evidence:                   # optional; what the judges get besides the artifact
  files: []                 # globs over the target, shown with line numbers
  expected: null            # the findings planted in the scenario's own target
rubric: |
  Score the answer 0 to 100 as a senior architect would...
judges:
  providers: 3
  effort: medium
```

`kind: skill` runs `claude -p "/<plugin>:<skill> <prompt>"` with
`--plugin-dir` pointing at this checkout, so the skills under test are
the ones in the working tree, not the installed ones. `kind: command`
runs `subject.argv`. `kind: qa` sends `subject.prompt` to
`subject.model` of one provider, and the answer is the artifact.

An unknown key in a scenario file is refused rather than ignored: a
misspelled key is a scenario that silently measures something else.

A relative path in a scenario is read from the scenario file's folder.

## Target and evidence

The subject runs in its own empty workspace. A target is a checkout it
reads: the scenario's `subject.target`, or `--target` in its place. The
subject is told where the target is: `{target}` in the prompt becomes
the path, a prompt without it gets one sentence naming the path, and a
skill gets `--add-dir` for it. A `command` subject gets `{target}` and
`{plugin}` filled in its argv.

A judge that sees only a rubric and a review can grade how the review
reads. It cannot tell whether a cited defect is in the code, or what
the review missed, and two judges agreeing does not change that. So a
scenario can give the judges evidence:

- `evidence.files`: the target's source, with line numbers, so a
  finding that names a file and a line is checked against that line.
- `evidence.expected`: the defects planted in the scenario's own
  target, one per entry with a lens, a file, and a line, and what the
  target does right. The file lives beside the target, never inside
  it, and the subject gets a copy of the target alone and a copy of
  the plugin that holds no fixture, so it cannot read the answers. On any other target the
  list would be wrong, so a run with `--target` drops it and says so;
  the source still goes to the judges.

With a planted list, the harness also counts which planted findings
the artifact names by lens id and file. That count is made by no model.
It is a cross-check beside the scores, in `results.json` as `expected`
on each repeat and in the report, not a score of its own: a review can
name a finding and be wrong about it, and can find a planted defect
under another lens. The judges read for both.

`review-om` runs on `fixtures/review-om`, a small object model with
eight defects planted, one lens each, and ten things done right. Its
answers are in `fixtures/review-om.expected.yaml`.

## Judges

Every provider gets the same prompt: the rubric, what produced the
artifact, the artifact, and the evidence when the scenario gives some,
each truncated at a stated limit so the judge knows whether it saw the
whole thing. Every provider answers in the
same shape through its own structured-output path: `score` from 0 to
100, `verdict` of `pass`, `weak`, or `fail`, `findings` of
`{severity, note}`, `strengths`, and a short `rationale`.

`models.yaml` holds the matrix: one model per provider, the fallbacks
tried in order when a model is refused or out of quota, and the effort
word each SDK expects. The file is a snapshot a monthly run redefines,
not a rule. The results name whichever model answered.

A model under load answers with a transient error, and the harness asks
it again. A model out of quota is not asked again; the next model in
the matrix is. A provider that never answers is recorded with what it
said and scores nothing. Nothing is invented for a provider that did
not answer.

## Streams

`streams/cli.jsonl` holds one record per output line,
`{"t": <unix>, "s": "out"|"err", "line": ...}`, flushed as the subject
runs. Frame folders hold `NNNNNN.jpg` files and an `index.jsonl` of
`{"t", "frame"}`; `CdpScreencast` fills one from a headless Chrome's
DevTools endpoint.

`serve.py` reads those files and nothing else: `GET /runs` lists the
runs, `/runs/<id>/report.md`, `/results.json`, and `/run.json` serve
the files, as does any path under `/runs/<id>/artifacts/`,
`/judgements/`, or `/streams/`, `/runs/<id>/streams/cli` is an event
stream tailing the JSON lines, and `/runs/<id>/streams/browser.mjpeg`
tails the frame folder. There is no
subscriber to register and no cost to watching: the files are written
either way, so watching late loses nothing.

## The words

- "benchmark", always the whole word.
- A place work happens in an example is a "station".

`scripts/check_leaks.py` refuses the rest of the list in every Markdown
file here.

## Tests

The harness logic is tested from the repository root, in
`tests/test_benchmark_*.py`, with the standard library and fake judges,
so `make test` and CI cover it with no key and no network.

```bash
make test
make benchmark          # the smoke scenario, two judges (--providers 3), one repeat
make benchmark-serve    # serve benchmark/runs at port 8765
```
