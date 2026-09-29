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
| `--scenario` | a scenario from `scenarios/`, by the name `list` prints, else by its file's stem, or a path to a file |
| `--with` | an optional group of the scenario's phases to run as well; repeat it for more. A run takes none by default (see A subject in phases) |
| `--providers` | the judges, as a bit flag (`3`, `7`, `15`), names (`anthropic,openai`), or `all` |
| `--effort` | `low`, `medium`, or `high`; `models.yaml` maps it per provider |
| `--repeat` | how many times the subject runs: the scenario's `repeat`, else 3; every repeat is judged by every provider |
| `--runtime` | `host`, `container`, or `vm`: one of the scenario's `runtimes`, its first by default |
| `--runtime-config` | a JSON or YAML file with the runtime's settings |
| `--target` | a checkout the subject works on, in place of the scenario's own |
| `--out` | the runs root: a run folder goes in `<out>/<scenario>/`; `benchmark/runs/` by default. `judge` and `resume` write beside the source when it is not given |
| `--claude` | the Claude Code binary a skill subject runs; `$CLAUDE_BIN`, else `claude` |
| `--subject-model` | the model the subject runs on; the scenario's `subject.model`, else the first Anthropic model in `models.yaml` |
| `--max-spend-usd` | once the run has spent this many US dollars, on the subject and the judges together, it starts no further repeat or phase; what is running finishes. When not given: the scenario's `max_spend_usd` on the path that takes no group; else, for a subject in phases, the sum of the caps of the phases that run and of the agentic judges' dollar budgets; else no cap |
| `--dry-run` | resolve everything, write `run.json`, call no provider and run no subject |
| `--preflight` | resolve as `--dry-run` does, then check what the run needs where it runs, and stop at the first failure; exit 8 when a check fails. No paid endpoint is called (see Preflight) |
| `--rehearsal` | run the scenario as it will really run, with every bound cut small and a spend cap of $5, after its preflight; never checked in (see Rehearsal) |
| `--strict` | a provider without a key fails the run instead of being skipped |
| `--build` | build the container image before running; a `qa` subject runs no command, so it builds none |
| `--screencast-port` | capture frames from a Chrome already listening on that debugging port |
| `--screencast-seconds` | how long to capture frames; 10 by default |

`judge --source <run folder>` judges an earlier run's archived output
again, and runs no subject (see Judge a run again).

`resume --source <run folder> [--after <phase>]` starts a new run from
the milestone an earlier run kept after a phase, and runs only the
phases after it (see Resume from a milestone). Of a run whose phases
all ran, it runs again only the judges `--judges` names, or those that
did not answer, and carries the other judgements (see Resume a run's
judges).

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
nonzero exit, a timeout, or `is_error` in its result. A subject in
phases fails when one of its phases does, or ends the run early (see A
subject in phases).
Dropping the failures would let a subject that fails one time in three
keep the score of the two times it did not.

One run of a subject is an anecdote, so a run repeats it 3 times,
unless `--repeat` or the scenario's `repeat` says otherwise (see
Scenarios). The summary reports the spread: each provider's standard
deviation, and each repeat's mean over its providers with their range
and standard deviation. A Claude subject judged by a panel that includes
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
runs/<scenario>/<YYYYMMDD-HHMMSS>-<scenario>-<random>/
  run.json                 the resolved scenario, runtime, models, argv, and versions
  streams/cli.jsonl        one JSON line per output line, written as it happens:
                           a skill subject's every turn
  streams/harness.jsonl    what the harness ran where the subject ran, when the
                           subject builds an output: checkpoints, the archive, the gates
  streams/build.jsonl      the image build's output, with `--runtime container --build`,
                           for a skill or command subject
  streams/browser/         frames and index.jsonl, when something captured them
  artifacts/<repeat>/      the answer, the judge prompt, and the collected
                           files under workspace/ at their own paths, byte for byte
  artifacts/<repeat>/output.zip, MANIFEST.txt
                           the output's last commit, whole, and a line per file
  artifacts/<repeat>/milestones/<phase>/
                           what a phase left: its checkpoint as output.zip, with
                           MANIFEST.txt, the collected files under workspace/, and
                           HANDOFF.md when a hinted phase had kept the note
  judgements/<repeat>-<provider>.json
  judgements/<repeat>-<provider>.jsonl
                           an agentic judge's transcript, one line per step
  results.json             the record, in schema/result.schema.json
  report.md                the same run for a person
```

A run folder goes in the folder of its scenario under the runs root,
`benchmark/runs/` unless `--out` names another. The random part of its
name tells apart two runs of one scenario started in the same second.
A run folder is created only when it is not there yet, and a stream
file likewise, so no run writes into another's.

The subject never works in the run folder. It lives in a sandbox
outside the checkout, a fresh temporary folder per run:

```text
<sandbox>/
  plugin/                  a copy of the plugin payload only: the manifest,
                           skills, agents, lenses, architecture.md, checkers
  target/                  a copy of the target folder, tests included,
                           without its siblings
  workspace/<repeat>/      what the subject worked in, empty at the start
  home/<repeat>/, tmp/<repeat>/  the host runtime's private HOME and TMPDIR,
                           one folder in each per session of a subject in phases
  hidden/<repeat>/         the handoff note, while a phase without a hint runs
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

A run folder under `benchmark/runs/<scenario>/` is checked in, and
only after `uv run benchmark/run.py redact --out benchmark/runs` has
scanned it for keys (see The workflow). Each scenario's folder has a
`README.md`: a paragraph on what the subject is asked to do, what it is
given, and how it is scored, then one row per run, newest first,
linking to its report. A run and its resumes are one row (see A run and
its resumes). `runs/README.md` is the index: one line per scenario,
linking its page, and the key to the columns. The pull request that
adds a run adds its row by hand; nothing generates the pages.

`make runs`, part of `make check`, fails when a run folder sits outside
its scenario's folder, when a scenario's folder has no `README.md`, and
when the index does not name each scenario's folder once. It fails when
a run folder is named by no row, or by two, as a row's run or as a part
of its chain; when a row names a run that is not there, or whose chain
breaks; when a row's cost is not its chain's total; and when a row sits
above a run that started after it. It also fails on a run whose
checkout was not clean (see Versions), on a run whose runtime its
scenario does not list (see Where a scenario runs), on a rehearsal (see
Rehearsal), and on a compressed file in a run folder that holds a
string shaped like a key, or that the scan cannot read, on a `.zip` that
does not open, and on a `.git` folder (see The workflow).

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
| `image` | the container image by name and by the id the engine gives it, for a skill or command subject; a `qa` subject runs in no image |
| `target` | the target by its path in the repository and a hash of the staged copy the subject read |
| `expected` | the planted findings by their path in the repository and a hash of the file |
| `references` | what agentic judges read beside the output: paths of the checkout and a hash of their copy, or a repository's URL, tag, commit, and the guideline release it pins (see Agentic judges) |

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

A checked-in run is held to the same. `make runs` finds a run's
scenario by the name the run records, and fails on a run whose runtime
that scenario, as its file is now, does not list. It fails too on a run
whose scenario has no file, one that does not load, or a name two files
share.

### The vm runtime

A run takes the other machine alone. A lock, the folder
`<remote_workspace>/.lock`, names the run that holds it. A second run
there fails every repeat with a note until the first gives the machine
back at its end. A harness that dies before its end leaves the lock;
remove the folder once no run is using the machine. A preflight finds
it (see Preflight).

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
    home/<repeat>/, tmp/<repeat>/  the subject's private HOME and TMPDIR,
                           one folder in each per session of a subject in phases
    hidden/<repeat>/       the handoff note, while a phase without a hint runs
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
| `tools` | optional; what `--preflight` asks there, each `{argv, version}`: the command that prints a tool's version, and the version pinned, left out where none is |

The prefix has to hand its words on as words, as `limactl shell` and
`docker exec` do. `ssh` joins them into one remote shell line and needs
a wrapper. A run with no `exec_prefix`, or one that needs a plugin or a
target and has neither `copy` nor the override, is refused before it
starts.

The copies are made afresh before every repeat, so no repeat reads what
an earlier one changed in them. A dry run makes none. A repeat that
resumes an earlier run starts with that run's milestone in its workspace
(see Resume from a milestone). The harness puts it there by `sync`, or,
with no `sync`, by `copy`, which makes the repeat's workspace there as
it makes `plugin/`. They are the
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
outlives the stop.

So the harness removes what the subject's Docker made there. When the
run takes the machine, it lists every container, network, and volume
Docker holds there. After each repeat, once its gates ran and its
workspace came back, it removes every one that was not on that list:
the containers first, with their anonymous volumes, then the networks,
then the volumes. It does so again when the run gives the machine back,
so a run stopped in the middle of a repeat leaves none either. The
run's notes name what went, and what could not be removed. So no repeat
starts beside an earlier one's stack: no Compose project of the same
name, no port already held, and no earlier subject's database within
reach. What Docker held when the run took the machine stays, because
the run did not make it, and a preflight refuses such a machine (see
Preflight). Images stay too: they hold no subject's data. A Docker that
does not answer when the run takes the machine leaves nothing to tell
the run's containers from the rest, so the harness removes nothing, and
the notes say so. A machine with no Docker holds nothing to remove.

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
pins, and pnpm and Terraform at pins of their own. It carries make and
git from Ubuntu's archive. A tree the scaffold skills make runs its
gates, `make check` and `make test-integration`, with make, git, uv,
Node, pnpm, and Docker Compose. The readiness probe runs each of them,
so a machine that lacks one never reports ready. `runtime-config.yaml`
lists them under `tools`, each at the version the template pins, and a
preflight asks each one. So a machine made from an older template,
whose probe asked for less, is caught before a run starts. uv runs the Python the
tree names: Ubuntu's own when its release matches, else one uv fetches.

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
in its own folder, and the containers, networks, and volumes its
subjects' Docker made, and nothing else: the images a subject pulled or
built stay. The machine's user has sudo, so a subject can change the
machine itself. Lima keeps the template as it read it
at create, so a change to the template, a pin or a package, takes a new
machine: `limactl delete swe-benchmark`, then create it again.

## Scenarios

A scenario is YAML or JSON. Four ship here:

| Scenario | Kind | Runtimes | What it measures |
|----------|------|----------|------------------|
| `create-full-system` | `skill` | `vm` | the scaffold skills building a whole system from a product spec, and with extras a review and its fixes, judged against the guideline and its reference implementation |
| `explain-tenancy` | `skill` | `container` | the `arch-explain` skill on one question about the tenant fence; the cheap one to run first |
| `review-om` | `skill` | `container` | the `arch-review-om` skill over a checkout with eight planted defects |
| `support-turn` | `qa` | `host`, `container` | a model answering an on-call question directly, with no skill |

`explain-tenancy` and `review-om` run in a container only. On the host
their subject can read the checkout, the rubric and the planted
findings in it. A `qa` subject runs no command, so the runtime holds
nothing it reaches.

`create-full-system` requires `docker`, so it runs on the vm runtime
only. Its subject builds `free-journalism` from
`fixtures/create-full-system/product-spec.md` in fresh phases (see A
subject in phases). By default a run is the build: the scaffold, then
the MVP. With extras, `--with extras`, a standalone review reads the
tree and a last phase closes the review's high findings, and the rubric
tells the judges so. The extras are an opt-in, kept while their review
finds high findings in the build's tree, as the scenario file says.
Agentic judges score the tree against the guideline and against the
guideline's reference implementation (see Agentic judges). It runs
once, as its `repeat: 1` says. Its bounds are money and time only, and
its scenario file says how they were sized. Its
run's spend cap is the sum of the caps of the phases that run and of
the four judges' budgets: $810 for the build, and $975 with extras.
So a run of it passes no cap. Run it with
`--preflight` first. The preflight checks, among the rest, that the
machine is up and carries its tools at their pins, that it reaches the
registries a scaffolded tree installs from, and that it has 40 GiB of
disk and 16 GiB of memory free (see Preflight). Before its first long
run, rehearse it: every phase, the checkpoints, the archive, the gates,
and the judges, for at most $5 (see Rehearsal). Each takes `--with
extras` for that path:

```bash
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml --rehearsal --out /tmp/rehearsals
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml --with extras
```

The shape:

```yaml
name: explain-tenancy
kind: skill                 # skill | command | qa
runtimes: [container]       # required: where it may run; a run takes the first
requires: []                # what the runtime must provide: docker
repeat: 3                   # optional; how many times a run repeats the subject, 3 by default
max_spend_usd: null         # optional; the run's spend cap in US dollars, none by default
preflight:                  # optional; what --preflight checks beyond what every run needs
  registries: []            # https URLs the subject reaches, each answering from where it runs
  disk_gib: null            # the free disk it needs there
  memory_gib: null          # the available memory it needs there
subject:
  skill: arch-explain
  prompt: "How does the guideline hold the tenant fence, and what proves it?"
  max_turns: 14             # optional; a turn cap, passed on only when named
  max_usd: 2                # the most the session may spend, in US dollars
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
  mode: one-shot            # one-shot | agentic (see Agentic judges)
```

`kind: skill` runs `claude -p "/<plugin>:<skill> <prompt>"` with
`--plugin-dir` pointing at the staged copy of this checkout's plugin
payload, so the skills under test are the ones in the working tree, not
the installed ones. It also gets `--model`: the subject's model is
always pinned, because `claude -p` on its default model measures
whatever that default is today. The run records the pin in `run.json`
and in `subject.model`. Every session runs under
`env CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, on every runtime, so its
subagents and the commands it starts run in the foreground, and each
result returns to the session that asked for it. In print mode, a task
left in the background ends the main agent's turn, and the session
never gives it back. The runtimes hand the subject its environment each
their own way, and a word of its command reaches it the same way on all
three. The session runs with `--output-format
stream-json --verbose`, so every turn is a line of `streams/cli.jsonl`,
and the answer, the models, and the spend are read from its last line,
the result. Each repeat records `subject_models`, the models the result
reports under `modelUsage`, and a run notes a repeat whose result does
not report the pinned model. A result with `is_error` set is a failed
repeat, whatever the exit code. A skill subject is bounded by money and
time: `max_usd`, which every skill subject names, goes to Claude Code
as `--max-budget-usd`, and `timeout_s` ends the session. A turn count is
no bound. A scenario may name `max_turns`, and only then does Claude
Code get `--max-turns`. The harness also holds the spend from the
stream (see A subject in phases). Each repeat records its
session under `phases`, with the bound that stopped it, if one did.
`kind: command` runs `subject.argv`. `kind: qa` sends `subject.prompt` to
`subject.model` of one provider, and the answer is the artifact. It is
asked again after a transient error, as a judge is, and `timeout_s`
bounds it, the retry included. No token limit below what the model can
write cuts its answer.

A scenario can say how many times a run repeats its subject, `repeat`,
a whole number of at least 1. It can also say the run's spend cap,
`max_spend_usd`, an amount in US dollars above 0 (see The run's spend).
A run takes each when its flag, `--repeat` or `--max-spend-usd`, is not
given, and a flag overrides it. A scenario that names neither runs 3
repeats, with no run cap unless its subject runs in phases (see The
run's spend). `run.py list` prints each one a scenario names, and its
optional groups. A value outside those bounds is refused when the
scenario loads, so no run of it starts.

A scenario can also say what `--preflight` checks for it, beyond what
every run needs, under `preflight`. `registries` are the https URLs its
subject reaches, each of which must answer from where it runs. A URL
with credentials in it is refused. `disk_gib` and `memory_gib` are the
free disk and the available memory it needs there, in GiB (see
Preflight).

An unknown key in a scenario file is refused rather than ignored: a
misspelled key is a scenario that silently measures something else.

A relative path in a scenario is read from the scenario file's folder.

## A subject in phases

A skill subject can run in `phases`: an ordered list of sessions, each
with a prompt and bounds of its own, building one folder of the
workspace, `output`. The harness commits that folder after every phase
and archives the last of those commits.

```yaml
subject:
  skill: arch-scaffold-new
  target: ../fixtures/acme-spec
  output: acme                # a folder of the workspace the phases build
  gates: [make check, make test-integration]
  gate_timeout_s: 3600        # how long each gate may run on the final tree
  groups:                     # optional; phases a run takes only with --with <group>
    extras:
      rubric: "After the build, a review read the tree."   # optional; added to the rubric
  phases:
    - name: scaffold
      prompt: "/swe-guidelines:arch-scaffold-new acme ... The product is described in {target}/spec.md."
      hint: true              # the handoff note; a builder keeps it
      max_usd: 360            # passed to Claude Code as --max-budget-usd
      max_gate_reruns: 3      # after a failed gate run, at most this many more
      timeout_s: 16200        # the session ends here, capped by time
    - name: review
      group: extras           # runs only in a run that takes extras
      prompt: "/swe-guidelines:arch-review-full . Write the report to ../review/report.md."
      cwd: output             # starts in the output folder, not the workspace
      session: fresh          # the default; resume continues the phase before
      on_cap: continue        # the default; stop ends the repeat at a bound
      max_turns: 150          # optional; a turn cap, passed on only when named
      max_usd: 75
      timeout_s: 5400
```

Every phase names its `name`, `prompt`, `max_usd`, and `timeout_s`, and
may name `max_turns`. The subject names no `prompt`, `max_turns`,
`max_usd`, or `timeout_s` of its own: each phase's are the ones in
effect.

**Groups.** A subject in phases can declare named optional `groups`. A
phase that names a `group` runs only in a run that takes it, with
`--with <group>`; repeat the flag to take more. A run takes no group by
default, so it runs the phases in none, and a scenario whose every phase
is in a group does not load. The phases run in the scenario's order,
whatever the order of the flags. A group can give a `rubric` sentence,
which the rubric takes after its own when the run takes the group, so
the judges know what was built. A group that no phase names, a phase
that names a group the scenario does not declare, and a resumed phase
whose phase before it is in another group are refused when the scenario
loads. A run that takes a group the scenario does not declare is refused
before it makes a run folder, with exit 2. `run.json` names the groups a
run took under `groups`, `results.json` under `subject.groups`, and the
report in its opening lines, each only for a scenario that declares
groups; an empty list is the default path. `run.json` and
`results.json` list only the phases that run.

**Sessions.** A phase is `fresh` by default: a new `claude -p` with a new
HOME and a new TMPDIR, so nothing carries over, no session, no Claude
Code memory, and no note. `resume` continues the session of the phase
before it, with `--resume`, in the same HOME and the same working
folder, and the first phase cannot resume. The container runtime starts
every phase in a new container, whose HOME is new each time, so a
scenario that resumes a phase does not list it. A phase starts in the
workspace, or in the output folder with `cwd: output`.

**What a phase is told.** A phase gets its prompt and nothing added to
it. It is told of the target only where its prompt names `{target}`,
and only then gets `--add-dir` for it. A phase with `hint: true` is
also told to keep a handoff note, `HANDOFF.md` in the workspace beside
the output folder, and to read it first when it is there. The harness
takes the note out of the workspace while a phase without the hint
runs, and puts it back for the next hinted one. So a phase that reviews
the tree reads its prompt and the repository, and nothing that says
the tree was scaffolded or what the run measures. The note is kept out
of the paths the subject is given. On the host and on another machine,
a subject that searches the machine can still find it.

**Bounds.** A phase is bounded by money and time. A turn or step count
is no bound: a phase that names `max_turns` gets `--max-turns`, and one
that names none gets no turn cap.

- the spend cap, `--max-budget-usd`, which Claude Code holds. It counts
  only the spend of the call it is given to: a resumed session's earlier
  spend is not counted against it. So a resumed phase's `max_usd` bounds
  that phase's own spend, as a fresh phase's does. The harness also
  prices the usage of every assistant message the stream
  carries, at the matrix's price for its model, and stops the phase
  when that passes the cap. A cache read is priced at a tenth of the
  input price, and a cache write at 1.25 times it, or twice it for a
  one-hour write. A model the matrix does not price is priced at its
  dearest Anthropic model, and named under `unpriced`. The stream shows
  what the session shows it, so a subagent the stream does not carry is
  held by Claude Code's cap alone;
- the timeout, `timeout_s`, which the harness holds: it stops the
  session and every process of its group;
- the gate reruns, which the harness holds, reading each Bash call in
  the stream. A command is split into simple commands the way the shell
  splits it, and a gate run is one whose first words, after any
  variable settings, are a command the scenario lists under `gates`.
  So `cd acme && make check` runs the gate `make check`, and
  `echo make check`, or a commit message that names it, does not. A
  run's outcome is read from the call's result only when the call's
  exit status is the gate's: nothing but `&&` follows the gate. Then
  it fails when the result is an error. A run that pipes the gate
  (`make check | tail`), or follows it with `;`, `||`, or `&`, is
  counted as unread, and it neither fails nor passes. After a failed
  run of a gate come at most `max_gate_reruns` more read runs, 3 by
  default: the first run plus at most 3 reruns. When the last of them
  fails too, the harness stops the phase and records the gate as
  failing. A run that passes ends the streak, so a later step that runs
  the gates again starts with its first run. A phase whose gate runs
  are all unread is bounded by its spend and its time.

A phase that hits a bound ends as `capped`, and its record in
`results.json` names the bound: `spend`, `time`, `gate_reruns`, or
`turns` when it names a turn cap. The next phase still runs, unless the
phase says `on_cap: stop`. A phase that fails, on a nonzero exit or an
error result, ends the repeat, and the repeat fails and is not judged. A repeat whose phases
all ended, finished or capped, is judged, unless the run's spend cap
kept some from running (see The run's spend). A one-phase skill subject is
one session under the same bounds, and a cap there fails its repeat, as
a session that gave no answer.

**Checkpoints.** After each phase, the harness commits the output folder
where the subject runs. The folder is made a repository if it is not
one. A checkpoint leaves the output's branch, HEAD, and index as the
subject left them. It stages the tree, the files git tracks and those
it does not ignore, into an index of its own, commits that tree with an
identity of its own, the message `checkpoint`, and no parent, and keeps
the commit under `refs/checkpoints/<n>`. So the history exists, and a
phase that lists every ref finds commits that say `checkpoint` and
nothing more: no phase's name, no outcome. The branch's own history
holds only what the subject committed, and a review with no argument
reads the change the subject made, not the harness's. The phase before
a checkpoint has ended, and its processes with it, so a lock git left
in the repository is stale, and the checkpoint removes it. A phase that
fails keeps its checkpoint, so the checkpoints so far are never lost.
Each phase's record in `results.json` names its commit; its wall time,
`wall_s`: the session's, from its start to its end, without the
checkpoint; and each model its session used with that model's cost,
`model_cost_usd`, as the result's `modelUsage` gives them. A session's
helpers can run on another model than its main agent, so a run says
what it measured. For a resumed phase, each cost is what the phase
added. The report's Phases table shows all three.

**A phase that ends the run early.** Two things end the run after a
phase: the phase leaves no tree, or its session ends with a subagent
unanswered. Then the phases after it do not run, and the repeat is not
judged. It is a failed repeat: it scores 0 in every mean, and the run
exits 6. Its record names the phase, the reason, and the phases that
did not run under `ended_early`, and no later repeat starts.

- `no_tree`: after the phase, the output folder holds no file. There is
  no folder, or its checkpoint's tree is empty. The phases after it
  would spend on an empty tree.
- `incomplete`: the stream shows an Agent call, the subagent tool
  (`Task` in older releases), with no result when the session's
  `result` event arrives. The session ended while work it asked for was
  not done, whatever its result says, so the phase ends as
  `incomplete`, not `ok`. A call whose result is the launch notice
  ("Async agent launched ...") started its subagent in the background,
  and stays pending to the end of the session: the notice answers the
  call, not the subagent. With background tasks off, such a launch
  means the setting did not hold. The call's input decides nothing: with
  background tasks off, a call that asks for `run_in_background` runs in
  the foreground, and its one result, the subagent's hand-back, answers
  it. Its record names each pending call, its id and what it was asked,
  under `pending_agents`. The phases after it would build on work the
  session never finished. A phase that hit a bound ends `capped`,
  pending calls or not.

**The output.** After the last phase that ran, the harness archives the
last checkpoint with `git archive --format=zip` and brings it back as
`artifacts/<repeat>/output.zip`, beside `MANIFEST.txt`: one line per
file of the zip, its SHA-256, its size in bytes, and its path. So two
runs' outputs diff as text. The zip is redacted member by member before
its hash is taken, and `results.json` records its SHA-256, its size, its
file count, and the commit, under `archive`. A zip the redaction cannot
read is replaced by a line that says so, its record holds no file, and
the run's notes say why; the run goes on to write its results. Files a
scenario collects are copied byte for byte, and a binary one reaches
the judges as its size, not its bytes. Nothing under a `.git` is
collected: its objects are compressed, and the zip is the record of the
output.

**Milestones.** A subject in phases also keeps what each phase left, its
milestone, so a later run can start from it (see Resume from a
milestone). After a phase whose checkpoint holds a file, the harness
archives that checkpoint where the subject ran and brings it back with
the files the scenario collects, as they stand after that phase. It
keeps the handoff note too, as it stands, once a hinted phase has kept
one. The note is hidden between phases, so it is read where it is
hidden; on another machine it is copied into the workspace there for the
fetch, and removed with the zip. They go under
`artifacts/<repeat>/milestones/<phase>/`: `output.zip` with its
`MANIFEST.txt`, the collected files under `workspace/`, and
`HANDOFF.md`. The zip is redacted and recorded as the output's is, under
the phase's `milestone` in `results.json`, with the commit, the paths of
the collected files, and the note's path under `handoff`.
Then the harness removes the zip from the workspace, so no later phase
finds it. A phase that failed keeps its milestone too, since its
checkpoint is kept. A phase after which the output folder holds no file
leaves none. The workspace is gone once the run ends, and what each
phase left stays in the run folder.

**The gates.** Then the harness runs each command under `gates` in the
output folder, where the subject ran, with no key, and records whether
it passed under `gates`, beside the scores. The gates never cap a score
and never fail the run. `streams/harness.jsonl` holds what the
checkpoints, the archive, and the gates printed.

**The run's spend.** The run's spend cap bounds what one run spends on
the subject and the judges together. It is `--max-spend-usd`; else the
scenario's `max_spend_usd`, which holds on the path that takes no
group; else the sum of the caps of the phases that run and of the
agentic judges' dollar budgets, one for each judge the run selects,
over every repeat. So a run that takes a group is capped by the phases
it runs and its judges, unless the flag names a cap. The harness checks it before each repeat and
before each phase, and starts nothing more once the run has spent that
much. A repeat of a skill subject starts only when what is left of the
cap covers the sum of the caps of every phase it runs. Otherwise it, and
every repeat after it, does not start, and the notes say why. So the
judges of one repeat never leave the next too little room to finish, and
a cap below one repeat's phases starts no repeat. It stops nothing that
is running: a phase that starts
below it can spend up to its own `max_usd`, and the judges of a repeat
still judge it. So a run can end above it, by about what one phase and
one repeat's judges spend. The run's notes say where it stopped. A
repeat whose phases the cap kept from running is cut short: its record
names those phases under `cut_short`. It is not judged, since its
output is not the one the scenario measures and a judge would spend
past the cap, and it is in no mean, neither scored nor a failure. The
summary names it under `cut_short`, and a run whose every repeat was cut
short has no score. A phase's spend is Claude Code's own figure, or the harness's estimate
when the session wrote no result. A resumed session's result carries
the session's running total, so a resumed phase's spend is what it
adds to that total. A repeat's is the sum of its phases'.

`run.json` records every phase with the command it runs, so a dry run
shows them. A resumed phase's command names the session it continues
by the phase before it, since the id is known only once that phase ran.

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

The judges are one-shot unless the scenario asks for agentic ones (see
Agentic judges). Every one-shot provider gets the same prompt: the
rubric, what produced the artifact, the artifact, and the evidence when
the scenario gives some, each truncated at a stated limit so the judge
knows whether it saw the whole thing. The artifact sits inside a fence of backticks longer than
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
it again: a `503`, an overload, and a timeout are transient. So is a
per-minute rate limit: a `429` whose error names a limit on tokens or
requests per minute, and names the wait, in a `retry-after` header or in
words such as "Please try again in 135ms". The harness waits that long
and asks again. A per-minute limit frees up within a minute, so a named
wait longer than 60 seconds is not waited. A model out of quota, a `429`
that says `insufficient_quota`, is not asked again; the next model in
the matrix is. A judgement a later model answered records `fallback`:
the model the matrix put first and the reason it did not answer. The
summary names every fallback, because a score from a fallback model is
not a score from the model the matrix names.
 A provider that never answers is recorded with what it
said and scores nothing. Nothing is invented for a provider that did
not answer.

A model is asked at most twice, because the SDKs' own retries are off:
left on, the Anthropic and OpenAI SDKs try each call three times. Each
one-shot call has a timeout of 480 seconds, twice the slowest one-shot
judgement a checked-in run records. No call is sent a token limit below
what the model can write. Anthropic's API requires one, so an Anthropic
call sends the model's own maximum, and the other providers are sent
none. So time bounds one provider's judgement: at most two calls per
model in the matrix, each within its timeout, with at most one wait of
a minute between them. The run's spend cap bounds the run (see The
run's spend).

## Agentic judges

A one-shot judge reads what fits in one prompt. A whole system does not
fit, so a scenario can ask for agentic judges instead. Each one reads
the subject's output and the scenario's references through read-only
tools, `list_dir`, `read_file`, `grep`, and `find`, and answers by
calling `submit` once. `harness/agentic.py` holds the loop and its
tools. The loop is the same on all four providers, so their scores
compare.

```yaml
judges:
  providers: 15
  effort: high
  mode: agentic
  budget:                     # optional; each key replaces its default
    max_usd: 3                # at the list price, summed over every call
    wall_s: 900
    tool_calls: 40            # reads per judgement; set so money and time bind first
    input_tokens: 500000      # summed over every call; the same
    submits: 3                # answers that miss the shape, and are sent back
  references:
    - name: guideline
      weight: 0.4
      paths: [architecture.md, lenses, skills]
    - name: reference
      weight: 0.6
      repository: https://github.com/acme/acme-system
      tag: v0.7.0
```

**The roots.** A judge reads each root under its name. `output` is what
the subject produced. For a subject that builds an output folder, it is
the tree the archive of its last commit holds. Otherwise it is the
repeat's answer, `answer.md`, and the files the scenario collects,
under `workspace/`. Each reference is a root of its own. A path out of
a root is refused, and so is a link out of one.

**The references.** A reference is one of two things:

- Paths of this checkout, such as the guideline's `architecture.md`,
  `lenses`, and `skills`. They are copied from the working tree the
  plugin is staged from. A path the checkout does not hold is refused.
- A public repository, by its `https` URL, pinned at a `tag`. The
  harness fetches that tag alone, at depth 1, and removes the `.git`
  folder, so the judge reads the tree at the tag. A branch of the same
  name is not the tag, and is refused. Git runs with none of this
  machine's git configuration and asks for no credential, so a
  repository that needs one is not fetched.

The weights are shares above 0, and they sum to 1. A reference named
`output` is refused, and so is a name two references share. A reference
that cannot be staged stops the run before the subject runs, with exit
2.

**The answer.** Every judge answers in one shape. For each reference, it
gives a `score` from 0 to 100, the `gaps` behind it, and its
`strengths`. A gap gives its `severity` (`high`, `medium`, or `low`),
`what` is missing or different, `in_output`, where it is in the output,
and `in_reference`, where the reference shows it. It gives the lens and
the fix where they apply. The judge also gives a `rationale`. An answer
that misses the shape goes back to the judge with the problems.

**The weighted score.** The harness computes it, never a judge, and no
judge is told the weights. It is the sum of each reference's score
times its weight, to one decimal place, rounded half up. It is the
judgement's score: each provider's mean, the overall mean, and the
spread are over the weighted scores. The summary also holds each
reference under `references`: its weight, each provider's mean, the
mean of those means, the range of its scores, and its gaps counted by
severity. A failed repeat scores 0 against every reference.

**The budget.** Each judgement has its own, and it is bounded by money
and time: `max_usd` and `wall_s`. The loop also counts tool calls and
input tokens, and a scenario sets them past what its dollars and wall
time allow, so money and time bind first (`create-full-system` says how
it sized them). Each tool result tells the judge how many tool calls are
left, how many input tokens and dollars are left, and how many input
tokens the last call carried. Every call
sends again all the judge has read, so the input tokens go faster with
each read.

When its tool calls run out, the judge is told to submit. A read it asks
for after that ends the judgement as `missed`. The input tokens and the
dollars end the same way, with one last turn. After each turn, the
harness checks whether the next call would pass either one: that call
carries at least the last one's input, and costs at least that input at
its price. When it would, that call is the judge's last turn. The reads
the judge just asked for are not run, and each answer tells it to
submit now. A submission on the last turn is the answer, and it is
scored like any other. Anything else ends the judgement as `missed`.
Wall time has no last turn: a judgement whose wall time runs out is
`missed`, since no call has time left to run. A per-minute rate limit
is waited out on the judgement's wall time, as a transient error is
(see Judges). When the wait it names is longer than the time left, the
judgement is `missed` at once, without waiting.

Neither the input tokens nor the dollars are a hard cap, for two
reasons. The check counts only what the next call carries at least, and
a call also carries what the turn before it answered and read. And the
last turn is a call made past the check, so that what the judge has read
is not thrown away. So a judgement can end above `input_tokens` and
`max_usd` by about one call: the last turn's input, which is about the
input of the call before it, and what the turns around it answered and
read. Each read is at most 40,000 characters, and each answer is what the
model writes in the time left. A last turn of 200,000 input tokens at $5
per million adds $1, and its answer at the output price.

The dollars are at the list prices in `models.yaml`. A model with no
price there is held to the dearest price the file gives, so the check
errs high. As for the one-shot judges, no call is sent a token limit
below what the model can write, and a call's timeout is the wall time
left. A `missed` judgement names the budget and its
figures in `error`, and scores nothing. The summary names it as it names
every judgement that did not answer.

**Where it goes.** `judgements/<repeat>-<provider>.json` holds each
judgement and the answer as the judge submitted it. Its transcript sits
beside it as `.jsonl`: every turn, every tool call with its arguments,
every submission, and the end, written as it happens. `results.json`
holds each judgement's scores, gaps, and strengths per reference under
`judged`, with the weighted score, the tool calls, the turns, and the
transcript's path. `report.md` shows a score per reference and the
weighted score for each judgement, the references in the summary, and
the gaps per reference, most severe first. The task every judge gets is
the repeat's `judge-prompt.md`.

**Versions.** `versions.references` names each reference. A reference of
the checkout is its paths and one SHA-256 over their copy. A repository
is its URL, its tag, the commit the tag names, and the guideline release
it pins. A repository that follows the guideline pins its release in
`specs/architecture.md`, in the words `docs/adopting.md` gives it:
"pinned at" and the release's tag. A repository that pins a release
other than this checkout's, or names none, is judged against all the
same, and the run's notes say so.

A dry run resolves every reference: it copies the paths, fetches each
tag, and records the commit and the release each pins in `run.json`.
Fetching a public repository spends nothing. It calls no judge.

An agentic judge takes no `evidence`: it reads its roots instead, and a
scenario that names both does not load.

The judges of a repeat run in parallel, one thread each. Each keeps its
own budget, its own transcript, and its own error handling, so a
provider that fails ends its own judgement and no other. The results
keep the order of the flag. An interrupt, such as Ctrl-C, stops every
judge before its next call, and the run ends at once: it waits on no
call in flight. A judge waiting to ask again, after a rate limit or a
transient error, stops at once and does not ask. The judges' spend
counts toward the run's spend cap once the last of them has answered,
as one repeat's judges always do (see The run's spend).

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
`subject_cost_usd`. A skill's figures come from each session's result,
and its cost is the one Claude Code reports, caching included. A
session the harness stopped wrote no result, and its cost is the
harness's estimate from the stream, which the run's notes name. Its
`reasoning_tokens` are the thinking tokens the result reports, which
its output already counts. A `qa` answer is priced like a judgement.

`results.json` totals it all under `spend`: each judge's tokens and
cost, the subject's, and `total_usd`. `report.md` shows the same in its
Spend section. A model with no price keeps its tokens, and is named
under `unpriced`. Its cost is in no figure, so a total with anything
unpriced is a lower bound. No price is invented for it.

## Preflight

A run that dies an hour in, on a missing tool, a key that expired, or a
laptop that went to sleep, has wasted what it spent before. A dry run
only resolves the run. `--preflight` resolves it the same way, then
checks what the run needs where it runs, before it spends anything:

```bash
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml --preflight
```

It runs the checks in the order below and stops at the first that
fails. The failed check says what is wrong and how to fix it, and the
preflight exits 8. One that passes exits 0. It calls no paid endpoint:
no subject runs, no judge is asked, and each key is checked against its
provider's model list, which costs nothing. It takes the flags the run
will take, so it checks that run.

| Check | What passes |
|-------|-------------|
| `budgets` | the run has its spend cap: the flag's, the scenario's, or the sum of the caps of the phases that run and of the judges' budgets. Every session of a skill subject has its spend cap and its timeout, and each phase its gate-rerun cap. A turn cap is no bound, and none is needed. A session with no spend bound or no timeout fails |
| `checkout` | the checkout holds no change a commit does not, in what decides a score: the `dirty` of `versions.checkout` is false. A run from a dirty checkout is never checked in (see Versions). A rehearsal skips it |
| `references` | every reference of agentic judges was staged, and every repository reference, fetched at its tag, pins this checkout's release. A reference that a run refuses with exit 2 fails this check instead |
| `awake` | on macOS, `caffeinate` is on the path, and the machine draws AC power |
| `subject_key` | `SUBJECT_ANTHROPIC_API_KEY` is set, is no judge's key, and Anthropic's model list takes it. For a `qa` subject, its provider's key |
| `judge_keys` | every judge the run selects has a key, and its provider's model list takes it |
| `runtime` | on `vm`, the machine answers through `exec_prefix`, and the config's `check` passes there. On `container`, the engine answers and the image is there; with `--build`, the preflight builds it first. On `host`, always |
| `workspace` | on `vm`, `remote_workspace` there holds nothing: no lock, and nothing an earlier run left, which the next subject could read. And the machine's Docker holds no container and no volume, which the next subject would start beside. A failure names each one and the command that clears them |
| `tools` | every tool the runtime config lists under `tools` answers where the subject runs, at its pinned version, and a skill's Claude Code answers. A container run whose config lists none asks the three its Dockerfile pins |
| `resources` | the free disk and the available memory where the subject works are at least the scenario's `preflight.disk_gib` and `preflight.memory_gib` |
| `network` | the model API, for a skill, and every URL under the scenario's `preflight.registries`, answer from where the subject runs, whatever the status |
| `requires` | for `docker`, a Compose stack of one small service starts, turns healthy within 120 seconds, and stops |

A check that does not apply to the run is a `skip`, and says why: the
`awake` check off macOS, the `workspace` check off `vm`, the runtime
checks of a `qa` subject, which runs no command, and the `checkout`
check of a rehearsal, which is never checked in.

`run.json` records every check that ran under `preflight`: its name,
its status, what it found, the fix when it failed, and the facts it
read, such as each tool's answer and each URL's status. It names the
checks that did not run too. A key is recorded by the name of its
variable, never by its value. Like a dry run, a preflight leaves a run
folder that holds `run.json` alone, and `make runs` fails on it under
`benchmark/runs/`. Remove it, or pass `--out` elsewhere.

The Compose check pulls a small public image, `busybox`, and leaves it
on the machine. No other check leaves anything there.

A run that spends holds this machine awake until it ends. On macOS, the
harness starts `caffeinate -i -s` for its own process, so the machine
does not idle to sleep, nor sleep at all on AC power. A closed lid still
sleeps a laptop. Elsewhere, keep the machine awake yourself. A dry run
and a preflight hold nothing.

## Rehearsal

A run that takes hours and many dollars should not be the first
time its pipeline runs end to end. `--rehearsal` runs the scenario as it
will really run, only small, so a few dollars prove that the run
starts, moves from phase to phase, commits, archives, fetches the
output back, runs the gates, and has the judges answer. The scores mean
nothing.

```bash
uv run benchmark/run.py --scenario create-full-system \
  --runtime-config benchmark/runtime/lima/runtime-config.yaml --rehearsal --out /tmp/rehearsals
```

It rehearses the path the run takes, so `--with` takes a group as a run
does. It keeps the runtime, the gates, the judges with their models and
their effort, and the references. It keeps every prompt and adds one
line after each phase's, which a run that is not a rehearsal never
sees: the budget is tiny, build only the smallest piece of the task,
write its files in the first turns, and ask nothing. So the first phase
leaves a tree, and the archive, the gates, and the judges see one. It
cuts every bound, and never raises one the scenario sets lower:

| What | In a rehearsal |
|------|----------------|
| the subject's model | the cheapest the matrix prices for its provider, input price first: `claude-sonnet-5` for a skill. `--subject-model` names another |
| each session | at most $0.50 and a timeout of 1,800 seconds: money and time, and no turn cap unless the scenario names one. A phase that hits a bound hands on to the next one, whatever its `on_cap`, so every phase starts |
| each gate | a timeout of at most 600 seconds |
| each agentic judgement | a stub budget: $0.50, 900 seconds, and 2 submits. The tool calls and the input tokens stay the scenario's, which money and time reach first |
| the run | one repeat, and a spend cap of $5 over the subject and the judges together, or the path's own cap for one repeat when that is lower. `--max-spend-usd` can lower it; a flag that asks for more, or for another repeat, is refused with exit 2 |

It runs the preflight first, and a check that fails stops it with exit
8 before it spends anything (see Preflight). The preflight skips the
`checkout` check: a rehearsal is never checked in, and a change to the
harness is worth rehearsing before it is committed.
`--rehearsal --preflight` runs that preflight alone, and
`--rehearsal --dry-run` resolves the rehearsal and runs nothing.

A session this small can still end before it writes a file in the
output folder. It then ends the rehearsal as it ends any run (see A
subject in phases), and the rehearsal ends `failed`: it proved nothing
past that phase.

Its `run.json` holds `rehearsal: true`, and its `results.json` holds
`rehearsal`: how it ended, the steps it did not prove, its cap, what it
spent, and where the money went, each phase and each judge in the order
they spent it. It ends in one of four ways:

- `failed`: a phase failed, or ended the run early.
- `capped`: the run's spend cap kept a phase, or the repeat, from
  running.
- `incomplete`: a step it exists to prove did not happen. A phase left
  no checkpoint, the archive failed, no archive came back from the
  runtime, the gates did not run, or no judge answered. `missing` names
  each one.
- `completed`: none of these; every step it exists to prove happened.

Only a `completed` rehearsal proved the pipeline to its end. A capped
or an incomplete one exits 9, and a failed one exits 6, as a run whose
subject failed does. The console and the report's Rehearsal
section say how it ended, and name each step it did not prove. The cap
is the run's spend cap, so a rehearsal can end above it by what one
phase and its judges add (see The run's spend).

`make runs` fails on a run folder marked `rehearsal`, so pass `--out`
outside `benchmark/runs/`.

## Judge a run again

A skill subject's build is most of what a run spends. A judge can miss,
a phase can fail before the judges, and the judges can change. None of
these should cost the build again. `judge` judges an earlier run's
archived output again, and runs no subject:

```bash
uv run benchmark/run.py judge --source benchmark/runs/<scenario>/<run folder> --dry-run
uv run benchmark/run.py judge --source benchmark/runs/<scenario>/<run folder>
```

It reads three things from the source run's `run.json`: the scenario's
name, the groups the run took, and the runtime it ran on. The scenario,
its rubric, its judges, and its references are this checkout's, so a
change to the judges reaches the score. `--providers` and `--effort`
choose the judges, as they do for a run.

It judges the archive of an output folder with agentic judges. A
scenario whose subject builds no output folder, or whose judges are
one-shot, is refused with exit 2.

Every repeat the source run recorded, or kept artifacts for, is judged
from its archive, whether the run judged it or not. A repeat whose
phase failed, one that ended the run early, and one whose judge missed
are judged like the rest. A repeat is refused, with its reason and no
judge started, when:

- it kept no archive;
- its archive does not open as a zip, or holds no file;
- its archive's SHA-256 is not the one the source run's `results.json`
  records, so it is not the output that run made.

A source with no repeat to judge makes no run folder, and exits 2.

**What the judges are told.** A repeat's judges are told of the phases
that ran in it, the ones the source run's `results.json` lists for the
repeat. Its rubric takes the sentence of a group the source run took
only when every one of the group's phases ran in it, since the sentence
stands for them all. So a repeat that ended early, or was cut short,
before every phase of a group ran is judged with no sentence of that
group: its judges are not told of work that never happened. A repeat
the source's `results.json` lists no phases for is judged as the source
run took it, with every phase and every group.

**Where it goes.** The judgement lands in a new run folder beside the
source, or in the scenario's folder under the root `--out` names. Each
repeat judged gets the source's
artifacts, all but its judge prompt: the archive, its manifest, the
answer, and the collected files. The judges read the archive's tree, as
a run's judges do, and the repeat's `judge-prompt.md` is this run's
own. `results.json` names the source under `source`: its run folder,
its path, the repeats judged, each repeat refused with why, and the
repeats the run's spend cap kept from being judged, under `capped`. For
a scenario that declares groups, it also names the groups each repeat's
rubric took, under `rubric_groups`, and so does `run.json`. Each
repeat records its archive, its SHA-256 included, and its judgements.
No subject ran, so a repeat records no subject spend, and its exit
status is 0. Its `phases` are the ones its source's repeat ran, each
with its name, session, status, and cap, and none of its spend. So a
folder `judge` wrote, judged again, tells its judges what its own
source ran. The rest of how the subject ended is in the source run's
record. `versions` names this checkout and the references, which decide
the new score; the source's `versions` name what built the output. The
report opens with the source, the repeats refused, the repeats the cap
kept from being judged, and the groups each repeat's rubric took.

**Its spend.** The run's spend cap is the sum of the selected judges'
dollar budgets over the repeats it judges: four judges at $45 over one
repeat is $180. No phase runs, so no phase's cap is in it, and neither
is the scenario's `max_spend_usd`, which covers a subject too.
`--max-spend-usd` overrides it. A repeat's judges start only when what
is left of the cap covers their dollar budgets, so no judge is handed
dollars the cap does not hold. Otherwise that repeat, and every one
after it, is not judged, and the notes say why. The judges of a repeat
that started still judge it, and a judge can end above its budget by
about one call (see Agentic judges). `--dry-run` resolves the source,
the repeats, the references, and the cap, writes `run.json`, and calls
no judge. Like a dry run of a
scenario, it leaves a run folder that holds `run.json` alone, which
`make runs` fails on under `benchmark/runs/`: remove it, or pass
`--out` elsewhere. A `judge` that spends holds this machine awake until
it ends (see Preflight).

`judge` takes no flag of the subject or of the runtime. `--scenario`,
`--with`, `--repeat`, `--runtime`, `--runtime-config`, `--target`,
`--subject-model`, `--preflight`, `--rehearsal`, `--build`, and
`--screencast-port` are refused with exit 2. A run of a scenario
refuses `--source`, since it would run the subject.

A source that is a rehearsal is judged within a rehearsal's bounds.
Each judge gets the stub budget a rehearsal's judges get (see
Rehearsal), and the run's cap is the judges' stub budgets over the
repeats, at most $5. `--max-spend-usd` can lower that cap, and a flag
that would raise it is refused with exit 2. The run folder is marked a
rehearsal, and `make runs` refuses it too.

## Resume from a milestone

A run in phases that ends early, on a failed phase or a defect of the
harness, has paid for the phases before. `resume` starts a new run from
the milestone one of them left, and pays for none of them again:

```bash
uv run benchmark/run.py resume --source benchmark/runs/<scenario>/<run folder> --after scaffold --dry-run
uv run benchmark/run.py resume --source benchmark/runs/<scenario>/<run folder> --after scaffold
```

It reads from the source run's `run.json` the scenario's name, the
groups the run took, the runtime and its config, the target, and the
subject's model. The scenario is this checkout's, so its prompts,
bounds, gates, rubric, and judges are the ones that run.
`--runtime-config` replaces the source's config. `--providers` and
`--effort` choose the judges, as they do for a run.

`--after` names the phase whose milestone the new run starts from. By
default it is the last phase with a milestone. The new run runs only
the phases after it, each with its own bounds, then the gates, the
archive, and the judges. The judges are told of every phase the output
went through, the carried ones included, and a carried phase counts as
one that ran: the rubric takes the sentence of each group the source
took. On the vm runtime, what the subject's Docker made is removed after
a resumed repeat, as after any repeat. A resume after the last phase
has no phase to run, so it resumes the run's judges (see Resume a run's
judges). A resume whose next phase continues the session of the one
before is refused, since only the source run held that session.

Every repeat the source recorded is resumed, each from its own
milestone. Its workspace starts with the milestone's tree as the output
folder, each file with its executable bit and each symlink as a link,
and with the collected files at their paths. The handoff note the
milestone kept is hidden before the first phase, as the phases before
left it: the next hinted phase is shown it, and a phase without the hint
never sees it. It starts in no git repository: the zip holds the tree,
not its history. Before the first
phase, the harness counts the output folder's files where the subject
runs. When they are not the milestone's, no phase runs, and the repeat
fails with a note, so nothing is built on another tree. A repeat is
refused, with its reason and no phase started, when:

- the source kept no milestone after that phase;
- its zip does not open, holds no file, or names a path outside its tree;
- its SHA-256 is not the one the source recorded, or the commit
  `git archive` wrote in it is not the recorded one;
- the source recorded a handoff note with it and kept none.

A source with no repeat to resume makes no run folder, and exits 2.

A run recorded before phases kept milestones names none. Its archive is
then the milestone of its last phase, when the archive's commit is that
phase's checkpoint, and `artifacts/<repeat>/output.zip` is restored with
the collected files beside it. Such a run kept no handoff note, so none
is restored. An archive of another commit is refused.

**What it records.** The new run lands beside the source, or in the
scenario's folder under the root `--out` names. Each repeat keeps the number it had in the source. Its phases
start with the source's records of the phases up to the milestone, each
marked `carried`, and then those it ran. The milestone it restored is
copied into its own `milestones/`, and the carried record of that phase
names the copy; an earlier phase's milestone stays in the source's
folder. `results.json` and `run.json` name the source under `source`:
its run folder, its path, the phase it resumed after, the source's
checkout, which ran the carried phases, and each milestone restored,
with its SHA-256 and commit. `versions` names this checkout, which ran
the rest. `run.json` lists the phases it runs. The report opens with
the source and marks each carried phase.

**Its spend.** The run's spend cap is the sum of the caps of the phases
it runs and of the selected judges' dollar budgets, over the repeats it
resumes: mvp, review, and close at $270, $75, and $90, and four judges
at $45, is $615 for one repeat. No carried phase's cap is in it, and
neither is the scenario's `max_spend_usd`, which covers a run from its
first phase. `--max-spend-usd` overrides it. A repeat starts only when
what is left of the cap covers its phases and its judges. Otherwise it,
and every repeat after it, does not start: the notes say why, and
`source` names them under `capped`. What the run spent is what its own
phases and judges spent; a carried phase's cost is in its record, and
in no total. `--dry-run` resolves the source, the milestones, and the
cap, writes `run.json`, and runs nothing. `--preflight` checks the run
as it checks any run.

`resume` takes no flag that the source decides: `--scenario`, `--with`,
`--repeat`, `--runtime`, `--target`, and `--subject-model` are refused
with exit 2, and so is `--rehearsal`. A source that is a rehearsal is
refused too: its bounds were cut small. `--after` is for `resume` alone,
and so is `--judges`, which a resume after an earlier phase refuses:
every judge judges what the phases after it build.

## Resume a run's judges

A judge that misses leaves its run a score short, and judging the
output again pays for every judge. A `resume` of a run whose phases all
ran runs only the judges that need it, and carries the others:

```bash
uv run benchmark/run.py resume --source benchmark/runs/<scenario>/<run folder> --judges openai --dry-run
uv run benchmark/run.py resume --source benchmark/runs/<scenario>/<run folder>
```

Such a run has no phase after its last, so `resume` resumes its judges:
by default, and when `--after` names the last phase. The run's judges
are the source's. `--judges` names the ones to run again, in the words
`--providers` takes. With no `--judges`, it runs those whose judgement
in the source did not answer: an `error`, a `missed`, or a `skipped`
one. Each runs on the source's archive, as `judge` runs its judges (see
Judge a run again), and at the effort the source's `run.json` records,
the one its judgements were made at, unless `--effort` names another.
Every other judgement of the source is carried: the new run holds it as
the source recorded it, marked `carried`. So its scores, its means, and
its report hold every judge, and a mean is never over fewer judges than
the run has. The weighted score is the
harness's, so a carried one is weighed with this checkout's weights. A
source whose judges all answered has nothing to resume: it makes no run
folder, starts no judge, and exits 2.

A judgement is carried only when the source judged the same output with
the task this run's judges get. A repeat is refused, with its reason
and no judge started, when:

- `judge` would refuse it: it kept no archive, its archive does not open
  or holds no file, or its SHA-256 is not the one the source recorded;
- the source recorded no SHA-256 of its archive, so nothing says which
  output its judges read;
- the source holds no judgement of it;
- the source kept no judge prompt of it, or the one it kept is not the
  one this run builds for it.

The judge prompt holds the rubric with the sentence of each group it
took, the phases the judges are told of, and the references at their
versions. So a score of another output, of another rubric, or against
another copy of a reference is never mixed into a mean. A repeat
refused for its task is judged whole by `judge`. A source with no
repeat to resume makes no run folder, and exits 2.

**What it records.** The new run lands beside the source, or in the
scenario's folder under the root `--out` names, as a folder `judge`
writes: each repeat's artifacts, its archive, and the phases its
source's repeat ran, with no subject spend. Each repeat keeps the source's judge prompt, which is the one its
judges get. Its judgements are the ones it ran and the carried ones, in
the flag's order. A carried one keeps its answer under `judgements/`,
and its transcript is copied there. `results.json` and `run.json` name
the source under `source`, with `judges`: for each repeat, the judges it
ran and those it carried. `run.json`'s `providers` are the judges it
runs. The report opens with the source and each repeat's judges, and
marks each carried judgement.

**Its spend.** The run's spend cap is the dollar budgets of the judges
it runs, summed over the repeats: one judge at $45 is $45. A carried
judgement's cost is in its record, and in no total, so what the run
spent is what its own judges spent. `--max-spend-usd` overrides the
cap. As in `judge`, a repeat's judges start only when what is left of
the cap covers their budgets. `--dry-run` resolves the source, the
judges each repeat runs and carries, their task, and the cap, writes
`run.json`, and calls no judge.

No phase runs, so a resume of the judges takes no flag of a runtime:
`--runtime-config`, `--preflight`, `--build`, and `--screencast-port`
are refused with exit 2. So is `--providers`, since the run's judges
are the source's. Only agentic judgements are carried, so a scenario
whose judges are one-shot is refused.

## A run and its resumes

A run the harness stopped, and the runs that resumed it or its judges,
are one measurement, however many run folders it took. Each folder
names the one before it under `source`, so the folders form a chain,
followed by `source.run_id` to the folder of that name beside it. A run
that judges another's output again continues that run's chain too.
`harness/chain.py` reads the chain from each folder's own records, so a
folder recorded before chains were is read too.

The chain's stages are what each folder ran, repeat by repeat: each
phase, or a subject's one session, and the judges. A carried phase and
a carried judgement are the folder's that ran them, so each stage is
counted once. A phase's cost and time are its record's `cost_usd`, or
the harness's estimate when it reported none, and `wall_s`. The judges'
stage names the judges the folder ran and those that did not answer.
Its cost is theirs, and its time is the longest judgement's for agentic
judges, which run at once, and the sum for one-shot judges, which run
one after another. The chain's total is what its folders spent, each
its `spend.total_usd`. It is a lower bound, `at_least`, when a folder
recorded no spend, a model had no price, or the chain breaks: a source
that is not beside its folder.

A run that resumes or judges another writes its chain under `chain` in
`results.json`: each folder from the first, with its start and what it
spent, each stage with the folder that ran it, its status, cost, and
time, and the total. Its report's Chain section shows the same. On a
scenario's page, the chain is one row. It links the newest folder's
report, its scores are that folder's, and its cost is the chain's
total, which `make runs` holds it to. Under the table, a run in phases
has its stage table, as the report gives it.

## Streams

`streams/cli.jsonl` holds one record per output line,
`{"t": <unix>, "s": "out"|"err", "line": ...}`, flushed as the subject
runs. A skill subject's `out` lines are its session, one JSON event
each, so the stream is the whole session, every turn and every tool
call. The subject's output is read as UTF-8, and a byte that is not
UTF-8 becomes U+FFFD, so the reader never stops early. A record ends at
a newline and nowhere else, so a U+2028 in an answer stays in it.

Frame folders hold `NNNNNN.jpg` files and an `index.jsonl` of
`{"t", "frame"}`; `CdpScreencast` fills one from a headless Chrome's
DevTools endpoint.

`serve.py` reads those files and nothing else. It serves a runs root,
the run folders in each scenario's folder, and names a run by its
folder's name. `GET /runs` lists the runs, each with its scenario,
`/runs/<id>/report.md`, `/results.json`, and `/run.json` serve
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
names them in a notice. Its `repeat` input is 1 to 5, and each scenario
runs with `--max-spend-usd` at its `max_spend_usd` input, a whole number
of US dollars from 1 to 50, 10 by default. Together they bound what one
dispatch spends on a scenario: past that amount, no further repeat of it
starts. Both reach `run.py` as flags, so they override a scenario's own
`repeat` and `max_spend_usd`.

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
A compressed file hides its text from a scan of its bytes, so the scan
unpacks the forms the standard library reads: a zip, member by member,
names and comment included; a tar, member by member; and a gzip, bzip2,
or xz stream. What it unpacks is scanned the same way, down to four
levels, and then the file's bytes are scanned as they stand. A file
that held a key is packed again in its own form, and a zip's manifest
and its record in `results.json` are written again, so they describe
the zip that is published. A file the scan cannot read is replaced by a
line that says so, since nothing could say it holds no key: one too
deep, one that would unpack to more than 1 GiB, a member that does not
read (an encrypted one, or one in a compression the standard library
does not know), a stream of those forms that breaks, and a form it
cannot read at all (zstd, 7z, rar, lz4). A file that only looks like a
zip and does not open as one is scanned as bytes. A file the command
cannot read or write is named, the rest are redacted still, and the
command exits 1. The summary and the upload run only when the redaction
succeeded.

## Tests

The harness logic is tested from the repository root, in
`tests/test_benchmark_*.py`, with the standard library and fake judges,
so `make test` and CI cover it with no key and no network. A test's
other machine runs on the machine the test runs on, so every test runs
the harness with a Docker command that is on no machine, and none
reaches that machine's Docker.

```bash
make test
make benchmark          # the smoke scenario in a container, its image built first, two judges (--providers 3), one repeat
make benchmark-serve    # serve benchmark/runs at port 8765
```
