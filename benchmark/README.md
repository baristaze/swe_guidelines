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
uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1 --build
uv run benchmark/serve.py --runs benchmark/runs --port 8765
```

`uv run` reads the inline dependencies at the top of `run.py`, so there
is nothing to install first. The harness modules import the standard
library only; the provider clients and the YAML and schema packages are
imported inside the functions that call them. `explain-tenancy` runs in
a container only, so its run needs Docker, and `--build` builds the
image first (see Where a scenario runs).

| Flag | What it does |
|------|--------------|
| `--scenario` | a scenario from `scenarios/`, by the name `list` prints or its file's stem, or a path to a file |
| `--providers` | the judges, as a bit flag (`3`, `7`, `15`), names (`anthropic,openai`), or `all` |
| `--effort` | `low`, `medium`, or `high`; `models.yaml` maps it per provider |
| `--repeat` | how many times the subject runs, 3 by default; every repeat is judged by every provider |
| `--runtime` | `host`, `container`, or `vm`: one of the scenario's `runtimes`, its first by default |
| `--runtime-config` | a JSON or YAML file with the runtime's settings |
| `--target` | a checkout the subject works on, in place of the scenario's own |
| `--out` | where run folders go; `benchmark/runs/` by default |
| `--claude` | the Claude Code binary a skill subject runs; `$CLAUDE_BIN`, else `claude` |
| `--subject-model` | the model the subject runs on; the scenario's `subject.model`, else the first Anthropic model in `models.yaml` |
| `--dry-run` | resolve everything, write `run.json`, call no provider and run no subject |
| `--strict` | a provider without a key fails the run instead of being skipped |
| `--build` | build the container image before running; a `qa` subject runs no command, so it builds none |
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

A repeat whose subject failed is not judged, and it is not dropped
either. It counts as a failure: it scores 0 in every provider's mean,
and a run whose every repeat failed scores 0. A subject fails on a
nonzero exit, a timeout, or `is_error` in its envelope. Dropping the
failures would let a subject that fails one time in three keep the
score of the two times it did not.

One run of a subject is an anecdote, so `--repeat` is 3 by default.
The summary reports the spread: each provider's standard deviation,
and each repeat's mean over its providers with their range and
standard deviation. A Claude subject judged by a panel that includes
Claude is named in the summary under `self_judged`. A model may favor
its own kind, so read the Anthropic score beside the others.

Keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, and
`XAI_API_KEY` with `GROK_API_KEY` as a second name. The Gemini client
is handed `GEMINI_API_KEY` and the ambient `GOOGLE_API_KEY` is taken
out of its way, because those two names often hold different accounts.

The subject has a key of its own: `SUBJECT_ANTHROPIC_API_KEY`. A
subject that runs a command, a skill's `claude -p` included, gets that
value as `ANTHROPIC_API_KEY`: in its environment on the host, by `-e`
in a container, and through a file on another machine (see The vm
runtime). It is spawned from an
environment that never held a judge's key, on every runtime: every
provider key name is taken out, and so is any variable whose value is
a judge's key, whatever its name. So a subject key set to a judge's key
is dropped, not handed on, and the stream says so. Give the subject a
key of its own, one you can cap and revoke on its own. `--strict`
refuses a skill run whose subject has none.

Out of the subject's environment is not out of its reach. The judges'
keys stay in the harness process, out of the subject's reach, in the
container and vm runtimes only. On the host the subject runs as the
harness's own user and can read the harness's environment (see
Runtimes).

## What a run leaves behind

```text
runs/<YYYYMMDD-HHMMSS>-<scenario>-<random>/
  run.json                 the resolved scenario, runtime, models, argv, and versions
  streams/cli.jsonl        one JSON line per output line, written as it happens
  streams/build.jsonl      the image build's output, with `--runtime container --build`
  streams/browser/         frames and index.jsonl, when something captured them
  artifacts/<repeat>/      the answer, the judge prompt, and the collected
                           files under workspace/ at their own paths
  judgements/<repeat>-<provider>.json
  results.json             the record, in schema/result.schema.json
  report.md                the same run for a person
```

The random part of the name tells apart two runs of one scenario
started in the same second. A run folder is created only when it is
not there yet, and a stream file likewise, so no run writes into
another's.

The subject never works in the run folder. It lives in a sandbox
outside the checkout, a fresh temporary folder per run:

```text
<sandbox>/
  plugin/                  a copy of the plugin payload only: the manifest,
                           skills, agents, lenses, architecture.md, checkers
  target/                  a copy of the target folder, tests included,
                           without its siblings
  workspace/<repeat>/      what the subject worked in, empty at the start
  home/<repeat>/, tmp/<repeat>/  the host runtime's private HOME and TMPDIR
```

So no answer key, no earlier repeat's judge prompt, and no
`CLAUDE.md` of the checkout is on a path the subject is given, or
under a folder it starts in. In the container runtime none of them is
in its reach at all, and in the vm runtime none is unless an override
names a whole checkout (see The vm runtime). On the host a subject
that looks for them finds them (see Runtimes). What a scenario
collects from the workspace is copied into `artifacts/`, and the
sandbox is removed when the run ends, however it ends. Every repeat
starts in an empty workspace of its own, so no repeat sees what an
earlier one wrote.

A run folder under `benchmark/runs/` is checked in, and only after
`uv run benchmark/run.py redact --out benchmark/runs` has scanned it
for keys (see The workflow). `runs/README.md` is the index: one row per
run, newest first, linking to its report. The pull request that adds a
run adds its row by hand; nothing generates it. `make runs`, part of
`make check`, fails when a run folder has no row, has two, or a row
names a run that is not there, and when a row sits above a run that
started after it. It also fails on a run whose checkout was not clean
(see Versions), and on a run whose runtime its scenario does not list
(see Where a scenario runs).

## Versions

A score compares with another only when the same things made both. So
a run names every version that decides a score, under `versions` in
`run.json` and `results.json`, and in the report's Versions section.
Each is a reference that resolves off the machine that ran it, never a
path on that machine:

| Field | What it names |
|-------|---------------|
| `checkout` | the commit, the plugin's version from `.claude-plugin/plugin.json`, and whether the tree held changes no commit holds (`dirty`), with those paths and one hash over their content |
| `claude_code` | what `claude --version` answers inside the runtime, for a skill subject |
| `image` | the container image by name and by the id the engine gives it |
| `target` | the target by its path in the repository and a hash of the staged copy the subject read |
| `expected` | the planted findings by their path in the repository and a hash of the file |

The skills under test are staged from the working tree, not from a
commit. So a run on uncommitted changes would name a commit that does
not hold what ran. `dirty` says so. It counts the plugin payload and
`benchmark/`, less the run folders, since those are the paths whose
content decides a score. `make runs` refuses a checked-in run whose
`dirty` is not `false`. A run recorded before the harness kept its
versions has none to check.

Claude Code is asked where the subject runs, because a container or
another machine carries its own, and a tag such as `latest` names
whatever was built last. The ask runs after the image build and before
the first repeat, and `run.json` is written again with the answer. A
dry run asks the runtime nothing, so its `claude_code` and `image` are
null. A runtime that does not answer leaves the field null and the run
says so in its notes. It is never filled in from this machine.

Every other path of the checkout that `run.json` and `results.json`
record is relative to the repository too: the scenario file, the
target, and the expected findings. A path outside the repository is
recorded as it is. The subject's command is recorded as it ran, so on
the `host` runtime it names the sandbox, a folder that is gone once
the run ends.

On the `vm` runtime, the subject reads copies of the plugin and the
target this machine staged, so `checkout` and `target` describe what it
read. A runtime config that names `remote_plugin` or `remote_target`
replaces a copy with what the operator placed on that machine. No
version describes that, and the run's notes say so.

## Runtimes

- `host` runs the subject on this machine with a private `HOME` and a
  private `TMPDIR` in the sandbox. The isolation is a convention, not a
  boundary: it keeps a subject from writing into the operator's account
  by accident, and stops nothing that means to. The subject runs as the
  harness's own user, so it can read what that user reads. That
  includes the harness's environment with the judges' keys in it (on
  Linux, through `/proc/<pid>/environ` of the harness), and the answer
  files at their fixed paths in the checkout. The sandbox keeps them out
  of the paths the subject is given, not out of its reach. Use `host`
  for a quick run on your own machine, never where that matters: a
  scenario whose subject must not reach them does not list it. The
  subject runs in a process group of its own, and the group is killed
  when the subject ends, on a timeout, a clean exit that left children,
  or an interrupt.
- `container` runs `docker run --rm` from the image
  `runtime/Dockerfile` builds. The staged plugin is mounted read-only
  at `/plugin`, the staged target read-only at `/target`, and the
  workspace read-write at `/workspace`. Nothing else of this machine is
  in the container, so the judges' keys and the answer files are out of
  the subject's reach. The image's user is not this machine's user on a
  Linux runner, so the workspace is opened to every user; the sandbox
  around it stays private. The benchmark workflow runs here every
  scenario that lists this runtime, with the image built in a step that
  holds no key.
- `vm` runs the subject on another machine through a configured
  prefix, such as `limactl shell`. The machine is the boundary. The
  harness copies the staged plugin and target there and nothing else
  of this machine, so the judges' keys are out of the subject's reach,
  and so are the answer files unless an override names a whole
  checkout. The harness provisions no machine and starts none.
  `runtime/lima/` holds the template of one that holds nothing of this
  machine (see The vm runtime).

A path on this machine means nothing in a container or on another
machine. So the runtime answers where the plugin checkout and the
target are as the subject sees them, and those are the paths the
subject is given: in `--plugin-dir`, in `--add-dir`, and in the prompt.

All three write the same streams into the run folder.

### Where a scenario runs

A scenario says where it may run: `runtimes`, the runtimes it runs on,
is required, and a scenario without it does not load. A run takes the
first it lists when `--runtime` names none. `run.py` refuses any other
runtime before it makes a run folder, and exits 7. So a caller tells a
scenario that does not run there from one that failed. `run.py list`
prints each scenario's runtimes, and what it requires when it requires
anything.

`requires` names what the runtime must provide. `docker` is the one
requirement there is: a Docker engine the subject runs containers on.
Only the vm runtime provides it. The subject runs there as the
machine's user. `runtime/lima/benchmark.yaml` makes a machine whose
engine listens at the default socket, which that user reaches with no
setting. The container runtime runs no engine and drops every
capability. The host runtime hands the subject a private `HOME` and a
few variables of the harness's environment. So an engine's settings,
`DOCKER_HOST`, `DOCKER_CONTEXT`, or a context under `~/.docker`, never
reach it, and an engine behind them is out of its reach.

A scenario that lists a runtime unable to provide what it requires is
refused when it loads. The check is against what a runtime provides by
its design, not a probe of the machine: another machine with no engine
at its default socket fails the subject, not the start.

A checked-in run is held to the same. `make runs` fails on a run whose
runtime its scenario, as its file is now, does not list, and on a run
whose scenario has no file or one that does not load.

### The vm runtime

A run takes the other machine alone. A lock, the folder
`<remote_workspace>/.lock`, names the run that holds it. A second run
there fails every repeat with a note until the first gives the machine
back at its end. A harness that dies before its end leaves the lock;
remove the folder once no run is using the machine.

Each run gets a folder of its own there, under `remote_workspace`,
named after the run folder. It is made with mode 0700, so no other
user there reads it, and the lock keeps every other run out of it. It
mirrors the sandbox:

```text
<remote_workspace>/
  .lock/                   the run that holds the machine
  <run>/
    plugin/, target/       copies of the staged plugin and target, afresh before every repeat
    workspace/<repeat>/    what the subject works in, empty at the start
    home/<repeat>/, tmp/<repeat>/  the subject's private HOME and TMPDIR
    keys/<repeat>/         one file per key the repeat's subject is handed
    group/<repeat>         the process group the subject runs in
```

Before every repeat, the harness removes the run's folder whole and
makes it again, so nothing an earlier repeat left in it reaches this
one. That matters for a `CLAUDE.md` above the workspace too, because
Claude Code loads every `CLAUDE.md` from its working folder up. The
lock, beside the run's folder, stays, and fetch has already brought each
earlier repeat's workspace back. Then the harness runs the config's
`check` there. The folders above the run's folder, `<remote_workspace>/`
and, with the Lima config, `/var/tmp`, outlast every repeat and the run,
and the subject can write there. The harness resets neither; only a new
machine does.

The runtime config names the commands that reach it:

| Key | What it does |
|-----|--------------|
| `exec_prefix` | the words before every command there, such as `["limactl", "shell", "--workdir", "/", "swe-benchmark", "--"]` |
| `copy` | makes a copy of a staged folder there; `{local}` is the folder here, `{remote}` the path the copy takes there, `plugin/` or `target/` in the run's folder |
| `remote_workspace` | the folder there that holds the lock and the run folders |
| `sync`, `fetch` | optional; copy the repeat's workspace there before the subject runs, and back after; `{local}` and `{remote}` are the two workspaces |
| `remote_plugin`, `remote_target` | optional; a path the operator placed there, used in place of a copy |
| `prefix_env` | optional; the names the prefix takes from this machine's environment besides `PATH` and the subject's own; `HOME` by default |
| `helper_timeout_s` | optional; how long a command other than the subject may take; 600 seconds by default |
| `check` | optional; a command run there before every repeat; a repeat whose check fails runs no subject |

The prefix has to hand its words on as words, as `limactl shell` and
`docker exec` do. `ssh` joins them into one remote shell line and needs
a wrapper. A run with no `exec_prefix`, or one that needs a plugin or a
target and has neither `copy` nor the override, is refused before it
starts.

The copies are made afresh before every repeat, so no repeat reads what
an earlier one changed in them. A dry run makes none. They are the
staged payload, so `versions` describes what the subject read, and no
answer file is among them. An override replaces a copy with whatever
the operator put there. Nothing makes it afresh, no version describes
it, and the run's notes say so. If the plugin there is a whole
checkout, the answer files are in it, and the subject can read them.

The subject's key never travels in a command line, and the prefix on
this machine never holds it. Before every repeat, the harness writes it
through stdin into a file of mode 0600 under the repeat's `keys/`. A
wrapper exports the names the harness hands it, and no other file
there, just before the subject starts. So the key is in no argument, no
stream line, no note, and nothing the run folder keeps. A file a
subject leaves under `keys/` reaches no later repeat. The run's folder
there is removed when the run ends, and the key files with it.

The prefix runs on this machine with the subject's environment, `PATH`,
and the names `prefix_env` lists, and nothing else of the harness's.
`limactl` needs `HOME` to find its machines. A probe, such as
`claude --version`, and every other command the harness runs there get
`PATH` and those names only.

The subject runs in its repeat's workspace, so what it writes is what
fetch brings back. Its HOME and TMPDIR are the repeat's own, as on the
host, and they start empty, so no repeat finds what an earlier one left
in them.

The subject runs in a process group of its own there, made with
`setsid` where the machine has it, and the group's id goes into
`group/<repeat>`. Stopping the prefix here does not stop what it
started there. So however a subject ends, on a timeout, a clean exit,
or an interrupt, the harness kills that group through the prefix
first, then the prefix here. A process that starts a session of its
own leaves the group, and the kill does not reach it. Nor does it reach
a container the subject starts, which is a child of Docker's daemon: it
outlives the stop and the repeat, so a later repeat can find a port
already allocated or a named volume already there.

A step that fails there fails the repeat with a note, and the subject
does not run: the machine stopped or held by another run, a copy, a
key that cannot be written, or a `check` that fails. The run goes on, and it writes
`results.json` and `report.md` either way. Every command other than the
subject has a timeout, `helper_timeout_s`, and one that runs past it is
stopped and noted. At the end the run's folder is removed, through
`sudo` where it answers without a password, because a container the
subject ran as root leaves files its user cannot remove. A folder that
stays is named in the run's notes.

`runtime/lima/benchmark.yaml` makes the machine with Lima, and
`runtime/lima/runtime-config.yaml` drives it:

```bash
limactl create --name swe-benchmark benchmark/runtime/lima/benchmark.yaml
limactl start swe-benchmark
uv run benchmark/run.py --scenario <name> --runtime vm --runtime-config benchmark/runtime/lima/runtime-config.yaml
```

`<name>` is a scenario that lists `vm`.

The machine is Ubuntu 26.04 LTS with 8 processors, 32 GiB of memory,
and a 100 GiB disk. It carries Docker, rootful, with its socket owned
by the machine's user, so a subject runs `docker` without sudo. It
carries uv, Node, and Claude Code at the versions `runtime/Dockerfile`
pins, and pnpm and Terraform at pins of their own.

Nothing of this machine is in it. It runs in Lima's plain mode, which
mounts no folder, forwards no port, and runs no guest agent. SSH
forwards no agent, and no proxy setting of this machine is written into
it. A firewall rule, loaded on every boot, refuses every connection
that leaves the VM through its uplink for a private address. That
covers this machine's loopback, which Lima's network answers at its
gateway, `host.lima.internal`, this machine's address on its own
network, and every other private, link-local, site-local, and shared
(CGNAT) address. DNS to the resolvers and DHCP pass. Docker's networks
inside the VM are not the uplink, so containers reach each other there
as they do anywhere. The public internet stays open, because a subject
needs it. The readiness probe checks the rule, so a machine whose
firewall did not load never reports ready. The firewall script saves
the table's listing as it loads it, and the runtime config's `check`
compares the live table with that listing before every repeat. A table
deleted or emptied, or a rule changed, removed, or added, fails the
check, and the repeat runs no subject. The check catches a change made
by accident or in passing. It cannot stop a subject with sudo that
means to get around it, which can rewrite the saved listing too.

The template pins Lima's `vz` machine type, macOS's own hypervisor. A
host without it, such as Linux, drops that line, and Lima runs QEMU.
QEMU's user network gives the VM an IPv6 prefix that reaches this
machine's loopback, and the rule refuses that prefix too.

The machine persists between runs. The harness removes what a run left
in its own folder, and nothing else: the containers, volumes, and
images a subject made stay. The machine's user has sudo, so a subject
can change the machine itself. A changed pin takes a new machine:
`limactl delete swe-benchmark`, then create it again.

## Scenarios

A scenario is YAML or JSON. Three ship here:

| Scenario | Kind | Runtimes | What it measures |
|----------|------|----------|------------------|
| `explain-tenancy` | `skill` | `container` | the `arch-explain` skill on one question about the tenant fence; the cheap one to run first |
| `review-om` | `skill` | `container` | the `arch-review-om` skill over a checkout with eight planted defects |
| `support-turn` | `qa` | `host`, `container` | a model answering an on-call question directly, with no skill |

The two skills run in a container only. On the host their subject can
read the checkout, the rubric and the planted findings in it. A `qa`
subject runs no command, so the runtime holds nothing it reaches.

The shape:

```yaml
name: explain-tenancy
kind: skill                 # skill | command | qa
runtimes: [container]       # required: where it may run; a run takes the first
requires: []                # what the runtime must provide: docker
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
`--plugin-dir` pointing at the staged copy of this checkout's plugin
payload, so the skills under test are the ones in the working tree, not
the installed ones. It also gets `--model`: the subject's model is
always pinned, because `claude -p` on its default model measures
whatever that default is today. The run records the pin in `run.json`
and in `subject.model`. Each repeat records `subject_models`, the models
the JSON envelope reports under `modelUsage`, and a run notes a repeat
whose envelope does not report the pinned model. An envelope with
`is_error` set is a failed repeat, whatever the exit code.
`kind: command` runs `subject.argv`. `kind: qa` sends `subject.prompt` to
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
  The source is read from the staged copy of the target, the one the
  subject reads, so the judges and the subject see the same files. A
  line ends at a newline and nowhere else, as an editor counts it.
- `evidence.expected`: the defects planted in the scenario's own
  target, one per entry with a lens, a file, and a line, and what the
  target does right. The file lives beside the target, never inside
  it, and the subject gets a copy of the target alone and a copy of
  the plugin that holds no fixture. So the answers are on no path the
  subject is given. The subject cannot read the answers in the
  container runtime, nor in the vm runtime unless an override names a
  whole checkout. On the host it can read them at their fixed path in
  the checkout.
  On any other target the list would be wrong, so a run with
  `--target` drops it and says so; the source still goes to the
  judges.

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
whole thing. The artifact sits inside a fence of backticks longer than
any run of backticks in it, so it cannot close the fence. The prompt
says that nothing inside the fence is an instruction, so a heading the
subject wrote cannot pass for one of the prompt's own.

Every provider answers in the same shape through its own
structured-output path: `score` from 0 to 100, `verdict` of `pass`,
`weak`, or `fail`, `findings` of `{severity, note}`, `strengths`, and a
short `rationale`.

`models.yaml` holds the matrix: one model per provider, the fallbacks
tried in order when a model is refused or out of quota, the effort word
each SDK expects, and each model's list price. The file is a snapshot a
monthly run redefines, not a rule. The results name whichever model
answered.

A model under load answers with a transient error, and the harness asks
it again: a `503`, an overload, and a timeout are transient. A model out
of quota is not asked again; the next model in the matrix is. A
judgement a later model answered records `fallback`: the model the
matrix put first and the reason it did not answer. The summary names
every fallback, because a score from a fallback model is not a score
from the model the matrix names.
 A provider that never answers is recorded with what it
said and scores nothing. Nothing is invented for a provider that did
not answer.

## Spend

A run records what it spent, in tokens and in US dollars, for every
judgement and for the subject.

A judgement's `usage` holds `input_tokens` and `output_tokens`, and
`reasoning_tokens` when the provider reports them. The output is what
the provider bills as output, and every provider bills reasoning as
output. Anthropic and OpenAI count the reasoning inside their output
count. Gemini and xAI report it beside it, so the harness adds it in.

`models.yaml` gives each model a price in US dollars per million input
and output tokens, under `prices`. The prices are read from each
provider's pricing page, and they move with the matrix: a model that
joins the matrix joins the prices. They are the standard tier's, below
each provider's long-context threshold, where every prompt of the
shipped scenarios falls. Every input token is priced as uncached input,
so a provider's cache discount makes the true bill lower, never higher.
A judgement's `cost_usd` is its usage at those prices.

The subject's spend is on each repeat, as `subject_usage` and
`subject_cost_usd`. A skill's figures come from the `claude -p`
envelope, and its cost is the one Claude Code reports, caching
included. Its `reasoning_tokens` are the thinking tokens the envelope
reports, which its output already counts. A `qa` answer is priced like a
judgement.

`results.json` totals it all under `spend`: each judge's tokens and
cost, the subject's, and `total_usd`. `report.md` shows the same in its
Spend section. A model with no price keeps its tokens, and is named
under `unpriced`. Its cost is in no figure, so a total with anything
unpriced is a lower bound. No price is invented for it.

## Streams

`streams/cli.jsonl` holds one record per output line,
`{"t": <unix>, "s": "out"|"err", "line": ...}`, flushed as the subject
runs. The subject's output is read as UTF-8, and a byte that is not
UTF-8 becomes U+FFFD, so the reader never stops early. A record ends at
a newline and nowhere else, so a U+2028 in an answer stays in it.

Frame folders hold `NNNNNN.jpg` files and an `index.jsonl` of
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

`serve.py` listens on `127.0.0.1` unless `--host` says otherwise, and
answers only a request that names it. Bound to `0.0.0.0`, it answers
any address of this machine and the machine's own name. It refuses
every other name, so a page on a name that resolves here gets nothing.

## The words

- "benchmark", always the whole word.
- A place work happens in an example is a "station".

`scripts/check_leaks.py` refuses the rest of the list in every Markdown
file here.

## The workflow

`.github/workflows/benchmark.yml` runs on demand every scenario that
lists the container runtime, in that runtime. It skips the rest and
names them in a notice. Its `repeat` input is 1 to 5, which bounds what
one dispatch spends on a scenario.

Its job runs in a GitHub environment named `benchmark`, and the
workflow does not create it. Create it under the repository's
Settings, Environments, with these rules:

- Deployment branches: selected branches, `main` only. A dispatch from
  any other branch never reaches the keys.
- Required reviewers: at least one. A dispatch waits until a reviewer
  approves it, because a run spends money and hands a subject a key.
- Secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `XAI_API_KEY`, and `SUBJECT_ANTHROPIC_API_KEY`, as secrets of the
  environment, never of the repository.

Without the environment, the job does not start.

A subject can print anything it can read. So before the workflow
writes the summary or uploads the run folders, it runs
`uv run benchmark/run.py redact --out benchmark/runs`. That scans every
file of every run folder as bytes, frames included, and replaces two
things with `[redacted]`: the value of every provider key the harness
knows by name, and anything shaped like a provider, GitHub, or AWS key.
The summary and the upload run only when the redaction succeeded.

## Tests

The harness logic is tested from the repository root, in
`tests/test_benchmark_*.py`, with the standard library and fake judges,
so `make test` and CI cover it with no key and no network.

```bash
make test
make benchmark          # the smoke scenario in a container, its image built first, two judges (--providers 3), one repeat
make benchmark-serve    # serve benchmark/runs at port 8765
```
