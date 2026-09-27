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
  "review-om with all four judges");
- a question about what there is ("which scenarios are there?");
- empty, which means: list the scenarios and the provider
  availability, and stop.

## Procedure

1. Confirm the working directory is a checkout, as "Where it runs"
   says. Every command below runs from it.
2. Run `uv run benchmark/run.py list`. It prints the scenarios with
   their kind, their default judges, the runtimes each runs on, and
   what each requires of its runtime when it requires anything. It
   prints each provider with its flag and whether its key is present.
   Report an absent key as absent; it is a provider that will be
   skipped, not a failure. It also shows whether the subject's own key,
   `SUBJECT_ANTHROPIC_API_KEY`, is present; a skill scenario needs it,
   and without it a `--strict` run refuses.
3. Map what the prompt asks to the flags that exist. The judges are a
   bit flag: `3` is Anthropic and OpenAI, `7` adds Gemini, `15` adds
   xAI; names joined by commas work too. Effort is `low`, `medium`, or
   `high`. `--repeat N` runs the subject N times, 3 by default.
   `--subject-model` pins the subject's model. When the prompt names
   something with no flag behind it, say so and run without it.
4. Choose the runtime. `--runtime` is `host`, `container`, or `vm`,
   and one the scenario lists: its first when the flag is not given.
   The harness refuses a runtime the scenario does not list with exit
   7 and runs nothing. So when the prompt names a runtime that `list`
   does not show for the scenario, run nothing, neither on that runtime
   nor on another, and say so. When the prompt names no runtime, pass
   no `--runtime`. A `qa` subject runs no command on any runtime: the
   harness asks the model itself, and builds no image for it, so it
   needs no engine. The runtime the run takes, named or the scenario's
   first, asks for more:
   - `container` runs the subject with `docker run` on this machine,
     so it needs a Docker engine here. Pass `--build` on every
     container run. The harness builds the image only when `--build`
     is passed, and nothing this skill runs can see whether the image
     is there. A build whose layers are cached takes seconds, and a
     first build takes minutes; say so before every container run.
     With no engine here the build fails, and the harness exits 4
     before the subject runs or any provider is called. Report that,
     and do not retry on another runtime.
   - `vm` runs the subject on another machine, which the runtime
     config reaches. Pass
     `--runtime-config benchmark/runtime/lima/runtime-config.yaml`,
     which drives the Lima machine `swe-benchmark`, unless the prompt
     names another config. This skill does not start the machine. A
     machine that does not answer fails every repeat with a note, no
     judge scores a repeat whose subject did not run, and the harness
     exits 6. Report that.
   - `host` runs the subject on this machine and asks for nothing more.

   What a scenario requires needs no flag. A scenario lists only
   runtimes that provide it, since the harness refuses one that does
   not when the scenario loads; a scenario that requires `docker`
   runs on `vm`. A dry run (step 6) builds nothing and asks no runtime
   anything, so it needs neither the engine nor the machine, and
   `--build` does nothing there. Pass the flags a run would take
   anyway, so the command is the run's.
5. Run the scenario, for example
   `uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1 --build`.
   A run takes minutes and costs money at every provider selected. When
   the prompt has not said which judges or how many repeats, use the
   scenario's default judges and the default of 3 repeats, and say
   which they were.
6. When the prompt asks what a run would do rather than for a
   measurement, add `--dry-run`: it resolves everything, writes
   `run.json`, and calls nothing. A dry run leaves `run.json` and
   nothing else, so read that file and report the resolved plan: the
   subject command, the runtime, the judges with the model and the
   fallbacks each would use, the effort, and the repeats. There is no
   score to report, and inventing one is the worst thing this skill
   could do.
7. List the run folder the command printed with `ls`, then read
   `report.md` in it. Read `results.json` when a number in the report
   needs its source.
8. Never edit a scenario, a rubric, or the model matrix to get a
   better score. A score that needs the rubric changed is the finding.

## Output

After a dry run: the resolved plan from `run.json`, in prose, and the
sentence that nothing was executed and no provider was called.

When the prompt names a runtime the scenario does not list: that
runtime, the runtimes the scenario lists, and the sentence that nothing
was executed and no provider was called. Nothing ran, so there is no
plan to report.

After a measurement, short, in prose:

- the scenario, the runtime, the repeats, and the judges that answered,
  each with its model id;
- the score per provider and the overall mean;
- the findings that matter, most severe first, in one line each;
- any provider that did not answer, with the reason it gave;
- the run folder path, and that it is checked in only once
  `run.py redact` has scanned it for keys.

Say what was measured, not what it means for the roadmap.
