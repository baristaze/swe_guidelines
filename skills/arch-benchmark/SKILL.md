---
name: arch-benchmark
description: "Run a benchmark scenario (a skill, a command, or a question) in a declared runtime and have frontier models score it against the rubric. Use to measure a change to a skill or the text."
allowed-tools: Read, Grep, Glob, Bash(uv run:*), Bash(ls:*)
disable-model-invocation: true
---

# arch-benchmark

Measure a subject against a rubric and report what the judges said.
The harness is `benchmark/run.py` in a checkout of the guideline
repository, and it is the only thing that runs a benchmark. Do not
compose a provider call by hand, and do not invent a flag: every flag
is in `benchmark/README.md`, and `uv run benchmark/run.py --help`
prints them.

## Where it runs

The current working directory must be a checkout of the guideline
repository: `architecture.md`, `.claude-plugin/plugin.json`,
`benchmark/run.py`, and `benchmark/README.md` all present. Check with
`ls` before anything else. Otherwise stop and say so: the benchmark
measures the checkout it runs from, and writes its run folders under
that checkout's `benchmark/runs/`. The copy under
`${CLAUDE_SKILL_DIR}/../..` is the installed plugin, not a checkout;
never run the harness from there.

## Input

`$ARGUMENTS` is one of:

- a scenario name, with or without flags ("explain-tenancy",
  "review-om with all four judges", "rehearse create-full-system",
  "create-full-system with extras"), or the path of a scenario file,
  which `--scenario` takes as it is;
- a question about what there is ("which scenarios are there?");
- empty, which means: list the scenarios and the provider
  availability, and stop.

## Procedure

1. Confirm the working directory is a checkout, as "Where it runs"
   says. Every command below runs from it.
2. Run `uv run benchmark/run.py list`. It prints the scenarios with
   their kind, their default judges, the runtimes each runs on, and
   what each requires of its runtime when it requires anything. It
   prints a scenario's optional groups of phases, `groups=`, when it
   declares any. It prints a scenario's `repeat` and `max_spend_usd`
   when it names them: the repeats and the run's spend cap a run of it
   takes by default. It
   prints each provider with its flag and whether its key is present.
   Report an absent key as absent; it is a provider that will be
   skipped, not a failure. It also shows whether the subject's own key,
   `SUBJECT_ANTHROPIC_API_KEY`, is present; a skill scenario needs it,
   and without it a `--strict` run refuses. `list` shows only the
   scenarios under `benchmark/scenarios/`. For a scenario given by its
   path, read its `kind`, `runtimes`, `requires`, `repeat`,
   `max_spend_usd`, `judges`, and `subject.groups` from the file.
3. Map what the prompt asks to the flags that exist. The judges are a
   bit flag: `3` is Anthropic and OpenAI, `7` adds Gemini, `15` adds
   xAI; names joined by commas work too. Effort is `low`, `medium`, or
   `high`. `--repeat N` runs the subject N times. Without it, the run
   takes the scenario's `repeat`, and 3 only when the scenario names
   none. `--with <group>` runs an optional group of the scenario's
   phases as well; repeat it for more. A prompt that asks for a group's
   phases, by the group's name or by naming or describing its phases,
   takes that group; a prompt that does not, takes none. Words that name
   no phase, such as "end to end" or "the full system", take none. A
   group the scenario does not declare is refused with exit 2.
   `create-full-system` runs the build by default, the scaffold and then
   the MVP, so a prompt that asks for the scaffold, the MVP, or the build
   takes no flag. Its one group, `extras`, is the review and the close.
   A prompt that asks for extras, the review, the close, or every phase
   ("all four phases") takes `--with extras`. `--subject-model` pins the subject's model.
   `--max-spend-usd N` is the run's spend cap in US dollars, over the
   subject and the judges together. Without it, a run that takes no
   group takes the scenario's `max_spend_usd`. Else a scenario in
   phases is capped by the sum of the caps of the phases that run and
   of the agentic judges' dollar budgets, over every repeat:
   `create-full-system` by $405 for the build and $487.50
   with extras. A run has no cap only when its subject runs in one
   session and its scenario names none. A flag overrides the
   scenario's value. The cap is not a hard ceiling: the
   harness checks it before each repeat and each phase and starts
   nothing more once it is reached, but what is running finishes, so a
   run can end above N. A repeat starts only when what is left of N
   covers the caps of all its phases, so a cap below one repeat's phases
   runs nothing. Each session of a
   skill subject also has its own cap, which Claude Code holds: the
   subject's `max_usd`, or for a scenario in phases each phase's. Say
   both when the prompt asks for a cap. When it asks what a run could
   spend, say that it can pass N by about the largest session cap and
   one repeat's judges. A dry run does not price one-shot judges. An
   agentic judge's budget `max_usd` is not a hard cap: when its next
   call would pass it, the judge still gets that call, as a last turn to
   submit. So a judge can pass it by about one call: an input about the
   size of the call before it, at the model's input price, and its
   output, at most `max_output_tokens` at the output price. One repeat's
   agentic judges spend about `max_usd` times the number of judges, and
   each judge can end above its `max_usd` by that one call. When the prompt names something with
   no flag behind it, say so and run without it.
4. Choose the runtime. `--runtime` is `host`, `container`, or `vm`,
   and one the scenario lists: its first when the flag is not given.
   The harness refuses a runtime the scenario does not list with exit
   7 and runs nothing. So when the prompt names a runtime the scenario
   does not list, run nothing, not even a dry run, neither on that
   runtime nor on another, and say so. The runtimes a scenario lists are
   the ones `list` shows for it, or, for a scenario given by its path,
   its file's `runtimes`. When the prompt names no runtime, pass no
   `--runtime`.

   A `qa` subject runs no command on any runtime: the harness asks the
   model itself and builds no image for it, so it needs no engine, no
   machine, and no `--build`. On `vm` it still takes the
   `--runtime-config` below, which every vm run needs, a dry run
   included. For a skill or command subject, the runtime the run takes,
   named or the scenario's first, needs more:
   - `container` runs the subject with `docker run` on this machine,
     so it needs a Docker engine here. Pass `--build`. The harness
     builds the image only when `--build` is passed, and nothing this
     skill runs can see whether the image is there. A build whose
     layers are cached takes seconds, and a first build takes minutes;
     say so before a run that builds. With no engine here the build
     fails, and the harness exits 4 before the subject runs or any
     provider is called. Report that, and do not retry on another
     runtime.
   - `vm` runs the subject on another machine, which the runtime
     config reaches. Pass
     `--runtime-config benchmark/runtime/lima/runtime-config.yaml`,
     which drives the Lima machine `swe-benchmark`, unless the prompt
     names another config. This skill does not start the machine. A
     machine that does not answer fails every repeat with a note, no
     judge scores a repeat whose subject did not run, and the harness
     exits 6. Report that.
   - `host` runs the subject on this machine, so a skill subject needs
     Claude Code installed here, and a command subject its command.

   A scenario's requirements need no flag. A scenario that lists a
   runtime unable to provide what it requires does not load, so every
   runtime a scenario lists provides what it requires. A scenario that
   requires `docker` lists `vm` only. A dry run (step 6)
   builds nothing and asks no runtime anything, so it needs neither the
   engine nor the machine. Pass the flags a run would take anyway, so
   the command is the run's.
5. Before a run that spends, run its preflight: the run's own command,
   every flag the same, with `--preflight` and
   `--out /tmp/benchmark-preflight` added, since its run folder is never
   checked in. It checks what the run needs where it runs, and calls no
   paid endpoint (`benchmark/README.md`, "Preflight"). The run that
   spends starts only when the preflight exits 0. Any other exit stops
   there, with nothing spent, and is reported: exit 8, a failed check,
   with what it found and the line the console prints after `fix:`; exit
   2, a flag the harness refused or a config it could not read, with the
   message it printed. When the prompt asks to stop before
   anything is spent, stop after the preflight, whatever it answered,
   and report it.

   A rehearsal is the step before a scenario's first long run, such as
   `create-full-system`'s. It is the run's own command with
   `--rehearsal` and `--out /tmp/benchmark-rehearsals` added. It runs
   every phase with its bounds cut small, commits, archives, fetches
   the output back, runs the gates, and has the judges answer on a stub
   budget, for at most $5 (`benchmark/README.md`, "Rehearsal"). It
   takes the run's `--with`, so it rehearses the path the run takes. It
   runs its own preflight first, so it needs no separate one. It spends, so
   run it only when the prompt asks for a rehearsal. A prompt that asks
   for a rehearsal alone gets the rehearsal and no long run. A prompt
   that asks for a rehearsal and then the run gets the rehearsal first,
   and the long run only when the rehearsal ended `completed`.

   Then run the scenario, for example
   `uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1 --build`.
   A run takes minutes and costs money at every provider selected. When
   the prompt has not said which judges, how many repeats, or what the
   run may spend, leave out `--providers`, `--effort`, `--repeat`, and
   `--max-spend-usd`. A run without those flags takes the scenario's
   own: its judges, its effort, its `repeat`, and its spend cap as step
   3 says. It takes 3 repeats only when the scenario names no `repeat`.
   Say which they were, and which groups the run took.
6. When the prompt asks what a run would do rather than for a
   measurement, add `--dry-run`: it resolves everything, writes
   `run.json`, and calls nothing. For agentic judges, it also copies
   each reference and fetches each repository reference at its tag,
   which spends nothing. A dry run leaves `run.json` and
   nothing else, so read that file and report the resolved plan: the
   subject command, the runtime, the judges with the model and the
   fallbacks each would use (`models` lists each provider's model
   first, then its fallbacks in order), the effort, the repeats
   (`repeat`), the run's spend cap (`max_spend_usd`) when there is one,
   the groups the run takes (`groups`, empty for the default path) when
   the scenario declares any, and the subject's `max_usd`. The
   top-level `repeat` and
   `max_spend_usd` are what the run takes, from its flags or its
   scenario. For a scenario in phases, `run.json` lists each phase the
   run takes under `phases`, with its `group`, and the
   phases' bounds are the ones in effect: the subject's `max_usd` is
   null, and its `max_turns` is null and its `timeout_s` a default
   nothing uses.
   `subject_argv` is only the first phase's command. Report each phase
   with the fields it holds: its `session` (`fresh`, or `resume` of the
   phase before), where it starts (`cwd`: the workspace or the output
   folder), whether it keeps the handoff note (`hint`), its bounds
   (`max_usd` and `timeout_s`, money and time, `max_gate_reruns`, and
   `max_turns` only when it names one, since a turn count is no bound),
   what follows a bound (`on_cap`), and what its `argv` runs:
   the prompt as the `argv` carries it, with the target's path in it when
   the phase's prompt names `{target}` and the handoff sentence when it
   is hinted, and whether it gets `--add-dir` or `--resume`.
   Report the subject's `output` folder and its `gates`, which the
   harness runs on the final tree. `benchmark/README.md`, "A subject in
   phases", says what each field means and what follows when a phase
   hits a bound or fails; answer from it when the prompt asks. When
   `scenario.judges.mode` is `agentic`, report every key of the judges'
   `budget` as `run.json` resolves it, defaults included, and
   each reference with its weight, and what the run resolved for it
   under `versions.references`: the paths of the checkout, or the
   repository's URL, tag, commit, and the guideline release it pins
   (`pins`). Report every line under `notes`. `benchmark/README.md`,
   "Agentic judges", says what each field means. There is
   no score to report, and inventing one is the worst thing this skill
   could do.
7. After a measurement, list the run folder the command printed with `ls`, then read
   `report.md` in it. Read `results.json` when a number in the report
   needs its source. A run that exits 4 leaves no `report.md`; the
   Output section says what to report then.
8. Never edit a scenario, a rubric, or the model matrix to get a
   better score. A score that needs the rubric changed is the finding.

## Output

After a dry run: the resolved plan from `run.json`, in prose, and the
sentence that nothing was executed and no provider was called; for
agentic judges, add that each repository reference was fetched at its
tag.

When the prompt names a runtime the scenario does not list: that
runtime, the runtimes the scenario lists, and the sentence that nothing
was executed and no provider was called. Nothing ran, so there is no
plan to report.

After exit 8, a preflight that failed: the check that failed, what it
found, and what the console prints after `fix:`, and the sentence that
nothing was run and no paid endpoint was called. After a preflight the
prompt stopped at, the same for a preflight that passed: each check and
what it found.

After a rehearsal, exit 0, 6, or 9: the groups it took; how it ended
(`completed`, `failed`, `capped` by its spend cap, or `incomplete`,
with each step it names under `missing`) and where its money went, as
the report's Rehearsal section says; how each phase ended; the
checkpoints, the zip and its manifest, and the gates; and which judges
answered and which did not, each with its reason. Its scores mean
nothing, so report none. It is never checked in.

After exit 4, the image build that failed: the reason
`streams/build.jsonl` in the run folder gives, and the sentence that no
subject ran and no provider was called. There is no score to report.

After exit 6, a subject that failed in some repeats: the measurement
below, with the repeats that failed and the reason the stream
`streams/cli.jsonl` gives for each. A repeat whose record names
`ended_early` ended after one phase: its `reason` is `no_tree`, the
phase left no file in the output folder, or `incomplete`, its session
ended with the Agent calls its `pending_agents` names unanswered. Name
that phase, the reason, the pending calls, and the phases that did not
run, and say the run started no later repeat. `report.md` scores a failed repeat 0
in every mean; say those zeros are failures no judge scored. When every
repeat failed, as on a vm machine that does not answer, report the
reason and no score.

After a measurement, short, in prose:

- the scenario, the runtime, the repeats, the groups it took, and the
  judges that answered, each with its model id;
- the score per provider and the overall mean; for agentic judges, say
  that each score is the weighted score the harness computed, and give
  each reference's weight and mean;
- for a scenario in phases, how each phase ended, its wall time, and
  the cap that stopped any, and which gates passed on the final tree;
- the findings that matter, most severe first, in one line each; for
  agentic judges, the gaps, per reference;
- any provider that did not answer, with the reason it gave, and for
  an agentic judge that ended `missed`, the budget it names;
- the run folder path, and that it is checked in only once
  `run.py redact` has scanned it for keys.

Say what was measured, not what it means for the roadmap.
