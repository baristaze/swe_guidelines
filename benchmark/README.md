# Benchmark

The benchmark measures a subject against a rubric. It runs the subject,
keeps everything that happened, and has frontier models judge the
result. The subject is a skill of this plugin, a command, or a question
a model answers directly. The judges are Anthropic, OpenAI, Gemini, and
xAI. Each answers in the same structured shape, so their scores
compare.

It replaces pasting a prompt into a provider's console and copying the
answer back. That loop is not repeatable, leaves no record, and asks one
model.

## Run it

```bash
uv run benchmark/run.py list
uv run benchmark/run.py --scenario explain-tenancy --dry-run
uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1 --build
uv run benchmark/serve.py --runs benchmark/runs --port 8765
```

`uv run` reads the dependencies at the top of `run.py`, so there is
nothing to install. `list` prints the scenarios, where each runs, and
which keys are present. A dry run resolves everything and calls no
provider. `serve.py` shows the runs in a browser, live or finished.

The judges read `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, and `XAI_API_KEY` (or `GROK_API_KEY`). A judge
without a key is skipped and named in the results. That is a choice: a
run with three judges is worth more than no run. The subject gets a key
of its own, `SUBJECT_ANTHROPIC_API_KEY`, as its `ANTHROPIC_API_KEY`. No
judge's key ever reaches the subject, so give it a key you can cap and
revoke on its own.

| Flag | What it does |
|------|--------------|
| `--scenario` | the name `list` prints, a file's stem, or a path |
| `--with` | take an optional group of phases; repeat it for more |
| `--providers` | the judges: a bit flag (`3`, `7`, `15`), names, or `all` |
| `--effort` | the judges' effort: `low`, `medium`, or `high` |
| `--repeat` | how many times the subject runs: the scenario's `repeat`, else 3 |
| `--runtime` | `host`, `container`, or `vm`, one the scenario lists; its first by default |
| `--runtime-config` | the runtime's settings, a JSON or YAML file; a vm run needs one |
| `--target` | a checkout the subject works on, in place of the scenario's |
| `--out` | the runs root; `benchmark/runs/` by default |
| `--claude` | the Claude Code binary: `$CLAUDE_BIN`, else `claude` |
| `--subject-model` | the subject's model: the scenario's, else the matrix's first Anthropic model |
| `--max-spend-usd` | the run's spend cap in US dollars (see [Spend](#spend)) |
| `--dry-run` | resolve the run, write `run.json`, call nothing |
| `--preflight` | resolve the run, then check what it needs (see [Preflight](#preflight)) |
| `--rehearsal` | run the scenario small, for at most $5 (see [Rehearsal](#rehearsal)) |
| `--strict` | fail on a judge with no key or no answer, and on a skill subject with no key |
| `--build` | build the container image first; a `qa` subject needs none |
| `--screencast-port` | capture frames from a Chrome listening on that port |
| `--screencast-seconds` | how long to capture; 10 by default |

`judge` and `resume` start from an earlier run, with `--source`,
`--after`, and `--judges` (see [Judge again and resume](#judge-again-and-resume)).
`redact` scans the run folders for keys (see
[Where results live](#where-results-live)).

<!-- agents-only
Exit status of `run.py`: 0 done; 2 refused before anything was made or
spent (a flag, a runtime config, a scenario that does not load, a group
the scenario does not declare, a source with nothing to do or already
continued); 3 `--strict` found a judge or the subject without a key, or
a judge that answered nothing; 4 the image build failed, and no subject
ran; 5 the record missed its schema; 6 a subject failed in some repeat,
or a phase ended the run early; 7 the scenario does not list the
runtime; 8 a preflight check failed; 9 a rehearsal ended `capped` or
`incomplete`; 10 a repeat is marked, since its subject named
`benchmark/runs`. Exit 10 is returned before 6 when both hold.
-->

## Scenarios

A scenario is a YAML or JSON file under `scenarios/`. Four ship here,
and each has a page under `runs/` with what it measures and its runs.

| Scenario | Kind | Runtimes | What it measures |
|----------|------|----------|------------------|
| `explain-tenancy` | `skill` | `container` | `arch-explain` on one question about the tenant fence; the cheap one to run first |
| `review-om` | `skill` | `container` | `arch-review-om` on a checkout with eight planted defects |
| `support-turn` | `qa` | `host`, `container` | a model answering an on-call question directly, with no skill |
| `create-full-system` | `skill` | `vm` | the scaffold skills building a whole system from a product spec |

```yaml
name: explain-tenancy
kind: skill                 # skill | command | qa
runtimes: [container]       # required: where it may run; a run takes the first
requires: []                # what the runtime must provide: docker
repeat: 3                   # optional; how many times a run repeats the subject, 3 by default
max_spend_usd: null         # optional; the run's spend cap in US dollars
preflight:                  # optional; what --preflight checks beyond what every run needs
  registries: []            # https URLs the subject reaches, each answering from where it runs
  disk_gib: null            # the free disk it needs there
  memory_gib: null          # the available memory it needs there
subject:
  skill: arch-explain
  prompt: "How does the guideline hold the tenant fence, and what proves it?"
  max_turns: 14             # optional; a turn cap, passed on only when named
  max_usd: 2                # the most the session may spend, which Claude Code holds
  timeout_s: 900            # the session ends here, which the harness holds
  allowed_tools: [Read, Grep, Glob]
  target: null              # a checkout the subject reads; {target} in the prompt is its path
artifact:
  stdout: true
  files: []                 # globs collected from the workspace after the run
evidence:                   # optional; what one-shot judges get besides the answer
  files: []                 # globs over the target, shown with line numbers
  expected: null            # the findings planted in the scenario's own target
rubric: |
  Score the answer 0 to 100 as a senior architect would...
judges:
  providers: 3
  effort: medium
  mode: one-shot            # one-shot | agentic (see Agentic judges)
```

A `skill` subject is `claude -p` running one skill of this checkout's
plugin, on a pinned model. A `command` subject runs its `argv`. A `qa`
subject is one question to one provider's model, and runs no command.
A skill subject is bounded by money and time; a turn cap applies only
when the scenario names one. An unknown key is refused, because a
misspelled key silently measures something else.

## How a run goes

The harness copies the plugin's payload and the target into a sandbox
outside the checkout. The subject works there, in an empty workspace
for each repeat. It is given the target's path and nothing more: no
rubric, no planted answers, no earlier repeat's work. The sandbox is
removed when the run ends.

One run is an anecdote, so a run repeats the subject, 3 times by
default. A repeat whose subject failed is not judged and scores 0. So a
subject that fails one time in three cannot keep the score of the other
two. The overall mean is the mean of the judges' means, so each judge
weighs once. The summary flags a Claude subject judged by a panel that
includes Claude, since a model may favor its own kind.

A scenario can give one-shot judges evidence: the target's source with
line numbers, and the defects planted in it. The harness then counts,
with no model, which planted findings the answer names.

## Runtimes

Where the subject runs decides what it can reach:

- `host` runs it on this machine, with a private `HOME` and `TMPDIR`.
  That is a convention, not a boundary. The subject runs as your user,
  so it can read the judges' keys and the answer files in the checkout.
- `container` runs it with `docker run`, from the image
  `runtime/Dockerfile` builds. Only the plugin, the target, and the
  workspace are mounted, so the judges' keys and the answers are out of
  reach.
- `vm` runs it on another machine, through a command prefix such as
  `limactl shell`. The machine is the boundary.

A scenario lists its `runtimes`, and a run takes the first unless
`--runtime` names another. `run.py` refuses any other with exit 7,
before it makes or spends anything. A scenario's `requires` names what
its runtime must provide: `docker`, which only the vm runtime provides.
So `create-full-system` lists `vm` alone. `explain-tenancy` and
`review-om`, whose answers sit in the checkout, list `container` alone.
`harness/runtime.py` holds the detail.

### The vm runtime

`runtime/lima/benchmark.yaml` makes the machine, and
`runtime/lima/runtime-config.yaml` drives it:

```bash
limactl create --name swe-benchmark benchmark/runtime/lima/benchmark.yaml
limactl start swe-benchmark
uv run benchmark/run.py --scenario create-full-system --runtime-config benchmark/runtime/lima/runtime-config.yaml
```

The machine is Ubuntu with Docker, Claude Code, and the tools a
scaffolded tree needs, most at pinned versions. Nothing of this machine
is in it. A firewall refuses every connection from it to a private
address, this machine included, and leaves the public internet open.
The comments in both files say how.

A run takes the machine alone, through the lock folder
`<remote_workspace>/.lock`. A harness that dies leaves it behind:
remove it once no run is using the machine. After each repeat, the
harness removes every container, network, and volume the subject's
Docker made. The machine itself persists. A changed template takes a
new machine: `limactl delete swe-benchmark`, then create it again.

## A subject in phases

A skill subject can run in `phases`: sessions in order, each with its
own prompt and bounds, building one folder of the workspace, `output`.
`create-full-system` runs a scaffold, then an MVP. With
`--with extras`, a review reads the tree and a last phase closes its
high findings.

```yaml
subject:
  skill: arch-scaffold-new
  target: ../fixtures/acme-spec
  output: acme                # the folder of the workspace the phases build
  gates: [make check, make test-integration]   # run on the final tree
  gate_timeout_s: 3600        # how long each gate may run there
  groups:                     # optional; phases a run takes only with --with <group>
    extras:
      rubric: "After the build, a review read the tree."   # added to the rubric
  phases:
    - name: scaffold
      prompt: "/swe-guidelines:arch-scaffold-new acme ... The product is described in {target}/spec.md."
      hint: true              # keeps a handoff note, HANDOFF.md, beside the output folder
      max_usd: 360            # the session's spend cap, which Claude Code holds
      max_gate_reruns: 3      # after a failed gate run, at most this many more; 3 by default
      timeout_s: 16200        # the session ends here
    - name: review
      group: extras           # runs only in a run that takes extras
      prompt: "/swe-guidelines:arch-review-full . Write the report to ../review/report.md."
      cwd: output             # starts in the output folder, not the workspace
      session: fresh          # the default: a new session and HOME; resume continues the phase before
      on_cap: continue        # the default: the next phase runs after a bound; stop ends the repeat
      max_turns: 150          # optional; a turn cap, passed on only when named
      max_usd: 75
      timeout_s: 5400
```

Every phase names its `name`, `prompt`, `max_usd`, and `timeout_s`. A
phase learns of the target only where its prompt says `{target}`. A
phase without `hint` never sees the handoff note. So a review reads the
repository, and nothing that says how the tree was built. A phase in a
`group` runs only when the run takes that group with `--with`, and the
group's `rubric` sentence tells the judges what it did.

A phase is bounded by money and time. Claude Code holds the spend cap,
and the harness stops the phase too once its stream prices past it. The
harness holds the timeout and the gate reruns: after a failed run of a
gate, at most `max_gate_reruns` more.

- A phase that hits a bound ends `capped`, and the next phase still
  runs, unless it says `on_cap: stop`.
- A phase that fails ends its repeat, which fails and is not judged.
- A phase that leaves no file in the output folder, or ends with a
  subagent unanswered, ends the run early: no later phase or repeat
  runs.
- A repeat the run's spend cap kept from finishing is cut short: not
  judged, and in no mean.

After each phase, the harness commits the output folder as a checkpoint
under a ref of its own, and keeps it as a milestone a later run can
resume from. After the last, it archives the tree as `output.zip`, and
runs the gates on it with no key. The gates are recorded beside the
scores and never cap them.

The run folders under `benchmark/runs/` hold every finished tree and
every judge's gaps. So a repeat whose subject names `benchmark/runs` in
a tool call is marked, the run exits 10, and `make runs` refuses it.

<!-- agents-only
Where a phase's outcome is recorded, in `results.json` per repeat:
`phases[]` holds each phase's `status`, the bound under `capped`
(`spend`, `time`, `gate_reruns`, or `turns`), its commit, its `wall_s`,
and each model's cost under `model_cost_usd`; a resumed phase's cost is
what it added. `ended_early` holds the phase, the `reason` (`no_tree`,
the output folder held no file, or `incomplete`, an Agent call had no
result), and the phases that did not run; an `incomplete` phase names
its unanswered calls under `pending_agents`. A failed phase, and a run
that ended early, exit 6. `cut_short` names the phases the run's spend
cap kept from running. `read_runs` lists each marked tool call with its
phase, and the report opens with them under `## Marked`. `gates` holds
each gate and whether it passed. `run.json` lists the phases the run
takes, each with its `group` and the command it runs; a resumed
session's command names the phase before it, since the session id is
known only once that phase ran.
-->

## Judges

A one-shot judge gets one prompt: the rubric, what produced the answer,
the answer, and the evidence, each cut at a stated limit. The answer
sits inside a fence it cannot close, and the prompt says nothing inside
it is an instruction. Every judge answers in one shape: a `score` from
0 to 100, a `verdict` of `pass`, `weak`, or `fail`, `findings`,
`strengths`, and a `rationale`.

`models.yaml` holds the matrix: one model per provider, the fallbacks
tried when a model is refused or out of quota, and each model's list
price. The results name the model that answered and every fallback. A
model is asked at most twice, and a judge that never answers scores
nothing.

### Agentic judges

A whole system does not fit in one prompt. So a scenario can ask for
agentic judges. Each reads the output and the scenario's references
through read-only tools, `list_dir`, `read_file`, `grep`, and `find`,
and answers by calling `submit` once. The loop is the same for all four
providers.

```yaml
judges:
  providers: 15
  effort: high
  mode: agentic
  budget:                     # optional; each key replaces its default
    max_usd: 3                # dollars at list price, summed over every call
    wall_s: 900               # the judgement's wall time
    tool_calls: 40            # reads; set so money and time bind first
    input_tokens: 500000      # summed over every call; the same
    submits: 3                # answers that miss the shape, and go back
  references:
    - name: guideline         # paths of this checkout, copied from the working tree
      weight: 0.4
      paths: [architecture.md, lenses, skills]
    - name: reference         # a public repository, fetched at its tag
      weight: 0.6
      repository: https://github.com/acme/acme-system
      tag: v0.7.0
```

The judge reads the output under the root `output`, and each reference
under its name. It scores the output against each reference, and names
the gaps behind each score: how severe, what differs, and where in each
tree. The harness weighs the scores, and no judge is told the weights,
which are above 0 and sum to 1. An agentic judge takes no `evidence`.

A judgement is bounded by money and time. When its next call would
pass its dollars or its tokens, the judge is told to submit, and that
call is its last turn. So a judge can end above its `max_usd` by about
one call. A judgement that runs out of time, or does not submit on its
last turn, is `missed`. `harness/agentic.py` and
`harness/references.py` hold the rest.

## Spend

Two caps hold the money. Each skill session has its `max_usd`, which
Claude Code holds, and each agentic judge its budget. The run has its
spend cap, over the subject and the judges together. The cap is
`--max-spend-usd`. Without the flag, a run that takes no group takes
the scenario's `max_spend_usd`, when it names one. Otherwise a subject
in phases is capped by the caps of the phases that run plus the judges'
budgets, over every repeat. That is $810 for `create-full-system`'s build, and $975 with
extras. A one-session subject whose scenario names no cap has none.

The run's cap is checked before each repeat and each phase. A repeat
starts only when what is left covers all its phases. The cap stops
nothing already running, so a run can end above it by about one phase
and one repeat's judges.

`results.json` records what each judge and the subject spent, in tokens
and dollars, under `spend`. The subject's cost is what Claude Code
reports. A session the harness had to stop reports nothing, so its cost
is the harness's estimate, which reads low, and the total reads "at
least". Judges are priced at `models.yaml`'s list prices with no cache
discount, so the true bill is lower, never higher.

## Preflight

A run that dies an hour in, on a missing tool, an expired key, or a
laptop gone to sleep, has wasted what it spent. `--preflight` resolves
the run, then checks what it needs, where it runs, before anything is
spent:

```bash
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml --preflight --out /tmp/benchmark-preflight
```

The checks are `budgets`, `checkout`, `references`, `awake`,
`subject_key`, `judge_keys`, `runtime`, `workspace`, `tools`,
`resources`, `network`, and `requires`, in that order;
`harness/preflight.py` says what each passes. The first that fails
stops the preflight with exit 8, and says what is wrong and how to fix
it. No paid endpoint is called: no subject runs, no judge is asked, and
each key is checked against its provider's free model list.

A preflight, like a dry run, leaves a run folder with `run.json` alone,
which `make runs` refuses under `benchmark/runs/`. Pass `--out`
elsewhere. A run that spends keeps a Mac awake with `caffeinate`, but a
closed lid still sleeps a laptop. Elsewhere, keep the machine awake
yourself.

## Rehearsal

A run that takes hours and hundreds of dollars should not be the first
time its pipeline runs end to end. `--rehearsal` runs the scenario as it
will really run, only small. A few dollars prove that the run moves
from phase to phase, commits, archives, brings the output back, runs
the gates, and has the judges answer. The scores mean nothing.

```bash
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml --rehearsal --out /tmp/rehearsals
```

It runs its preflight first, and takes `--with` as the run would. It
keeps the runtime, the gates, the judges, and the references. It cuts
every bound: the cheapest subject model, $0.50 and 30 minutes a
session, a stub budget per judge, one repeat, and a $5 spend cap. A
flag can lower a bound, never raise it. `harness/rehearsal.py` holds
the numbers.

A rehearsal ends `completed` only when every step it exists to prove
happened. Otherwise it ends `failed` (exit 6), or `capped` or
`incomplete` (exit 9), and names what it did not prove. It is never
checked in, so pass `--out` outside `benchmark/runs/`.

## Judge again and resume

A long run's build is most of what it spends. A judge can miss, a phase
can fail, and the judges can change. None of that should cost the build
again:

```bash
uv run benchmark/run.py judge --source benchmark/runs/<scenario>/<run folder>
uv run benchmark/run.py resume --source benchmark/runs/<scenario>/<run folder> --after scaffold
uv run benchmark/run.py resume --source benchmark/runs/<scenario>/<run folder> --judges openai
```

`judge` judges an earlier run's archived output again with this
checkout's scenario and judges, and runs no subject. `resume --after`
starts from the milestone a phase left, and runs only the phases after
it, then the gates and the judges. A `resume` of a run whose phases all
ran resumes its judges: those `--judges` names, or those that did not
answer. Each writes a new run folder beside its source, keeps the
records of what it does not run again, and takes `--dry-run`. Its spend
cap covers only what it runs. A flag its source decides, such as `--scenario`, is
refused with exit 2.

A run and the runs that continued it are one measurement: a chain of
run folders, each naming the one before under `source`. The chain's
cost is what its folders spent, and a scenario's page shows it as one
row. A chain has one line, so a source another run already continued
is refused. `harness/chain.py` and `run.py` hold the rest.

## Where results live

A run writes one folder, in its scenario's folder under the runs root:

```text
runs/<scenario>/<YYYYMMDD-HHMMSS>-<scenario>-<random>/
  run.json                 the resolved run: scenario, runtime, models, argv, versions
  streams/cli.jsonl        one JSON line per output line, as it happens: a skill's every turn
  streams/harness.jsonl    what the harness ran where the subject ran: checkpoints, archive, gates
  streams/build.jsonl      the image build, with --build
  artifacts/<repeat>/      the answer, the judge prompt, and the collected files
  artifacts/<repeat>/output.zip, MANIFEST.txt   the output's last commit, and a line per file
  artifacts/<repeat>/milestones/<phase>/        what each phase left
  judgements/<repeat>-<provider>.json           each judgement; .jsonl beside it, an agentic transcript
  results.json             the record, in schema/result.schema.json
  report.md                the same run for a person
```

Start with `report.md`. Two scores compare only when the same things
made both, so a run records every version that decides its score: the
commit, whether the checkout was clean, the Claude Code and the image
the subject ran in, and a hash of each input.

A run folder is checked in under `benchmark/runs/<scenario>/`, and only
after `uv run benchmark/run.py redact --out benchmark/runs` has scanned
it. Each scenario's page lists its runs, newest first, and
`runs/README.md` is the index. The pull request that adds a run adds
its row by hand. `make runs`, part of `make check`, holds the pages to
the folders. It refuses a run from a checkout with uncommitted changes,
a rehearsal, a marked run, and a file that still holds a key.
`scripts/check_runs.py` lists every check.

A subject can print anything it can read. So `redact` scans every file
as bytes, and inside every zip, tar, gzip, bzip2, and xz file. It
replaces with `[redacted]` every key the harness knows, and anything
shaped like a provider, GitHub, or AWS key. It also replaces the
account ids and limit figures that OpenAI's, Anthropic's, and xAI's
errors name. A file it cannot read is replaced by a line that says so.
`harness/redact.py` holds the rest.

## The workflow

`.github/workflows/benchmark.yml` runs on demand, never on push. It
runs every scenario that lists the container runtime, in that runtime.
Its inputs cap each scenario's run at 1 to 5 repeats, and at 1 to 50
dollars, 10 by default. It redacts every run folder before it writes
the summary or uploads anything.

Its job runs in a GitHub environment named `benchmark`, which the
workflow does not create. Create it under Settings, Environments:

- Deployment branches: `main` only, so no other branch reaches the
  keys.
- Required reviewers: at least one, because a run spends money and
  hands a subject a key.
- Secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `XAI_API_KEY`, and `SUBJECT_ANTHROPIC_API_KEY`, as secrets of the
  environment, never of the repository.

## Tests

`tests/test_benchmark_*.py` test the harness with fake judges and a
fake other machine, so `make test` needs no key and no network, and no
test reaches a Docker engine.

```bash
make test
make benchmark          # explain-tenancy in a container, two judges, one repeat
make benchmark-serve    # serve benchmark/runs at port 8765
```

Here "benchmark" is always the whole word, and a place where work
happens, in an example, is a "station".
