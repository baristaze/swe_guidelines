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
| `--providers` | the judges, as a bit flag (`3`, `7`, `15`) or names (`anthropic,openai`) |
| `--effort` | `low`, `medium`, or `high`; `models.yaml` maps it per provider |
| `--repeat` | how many times the subject runs; every repeat is judged by every provider |
| `--runtime` | `host`, `container`, or `vm` |
| `--runtime-config` | a JSON or YAML file with the runtime's settings |
| `--target` | a checkout the subject works on |
| `--out` | where run folders go; `benchmark/runs/` by default, which git ignores |
| `--dry-run` | resolve everything, write `run.json`, call no provider and run no subject |
| `--strict` | a provider without a key fails the run instead of being skipped |
| `--build` | build the container image before running |
| `--screencast-port` | capture frames from a Chrome already listening on that debugging port |

A provider whose key is absent is skipped, named in the results, and
does not fail the run. That is a choice: a run with three judges is
worth more than no run at all. `--strict` reverses it.

Keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, and
`XAI_API_KEY` with `GROK_API_KEY` as a second name. The Gemini client
is handed `GEMINI_API_KEY` and the ambient `GOOGLE_API_KEY` is taken
out of its way, because those two names often hold different accounts.

## What a run leaves behind

```text
runs/<YYYYMMDD-HHMMSS>-<scenario>/
  run.json                 the resolved scenario, runtime, models, and argv
  streams/cli.jsonl        one JSON line per output line, written as it happens
  streams/browser/         frames and index.jsonl, when something captured them
  artifacts/<repeat>/      the answer, the collected files, the judge prompt
  judgements/<repeat>-<provider>.json
  results.json             the record, in schema/result.schema.json
  report.md                the same run for a person
  workspace/, home/, tmp/  what the subject worked in
```

Nothing there is checked in. The manual is the repository; a run is a
measurement.

## Runtimes

- `host` runs the subject on this machine with a private `HOME` and a
  private `TMPDIR` under the run folder. The isolation is a convention,
  not a boundary: it keeps a subject from writing into the operator's
  account by accident, and stops nothing that means to.
- `container` runs `docker run --rm` from the image
  `runtime/Dockerfile` builds, with the target mounted read-only and
  the workspace read-write.
- `vm` runs the command on another machine through a configured
  prefix, for example `["limactl", "shell", "default", "--"]`, with a
  configured sync command. The harness provisions no machine and
  starts none; it composes the prefix and the sync.

All three write the same streams into the run folder.

## Scenarios

A scenario is YAML or JSON. Three ship here:

| Scenario | Kind | What it measures |
|----------|------|------------------|
| `explain-tenancy` | `skill` | the `arch-explain` skill on one question about the tenant fence; the cheap one to run first |
| `review-om` | `skill` | the `arch-review-om` skill over a target's object model |
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

## Judges

Every provider gets the same prompt: the rubric, what produced the
artifact, and the artifact, truncated at a stated limit so the judge
knows whether it saw the whole thing. Every provider answers in the
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
runs, `/runs/<id>/report.md` and `/results.json` serve the files,
`/runs/<id>/streams/cli` is an event stream tailing the JSON lines, and
`/runs/<id>/streams/browser.mjpeg` tails the frame folder. There is no
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
make benchmark          # the smoke scenario, three judges, one repeat
make benchmark-serve    # serve benchmark/runs at port 8765
```
