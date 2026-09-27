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
   their kind, their default judges, and the runtimes each runs on, and
   each provider with its flag and whether its key is present. Report
   an absent key as absent; it is a provider that will be skipped, not
   a failure. It also shows whether the subject's own key,
   `SUBJECT_ANTHROPIC_API_KEY`, is present; a skill scenario needs it,
   and without it a `--strict` run refuses.
3. Map what the prompt asks to the flags that exist. The judges are a
   bit flag: `3` is Anthropic and OpenAI, `7` adds Gemini, `15` adds
   xAI; names joined by commas work too. Effort is `low`, `medium`, or
   `high`. `--repeat N` runs the subject N times, 3 by default.
   `--subject-model` pins the subject's model. `--max-spend-usd N` is
   the run's spend cap in US dollars, over the subject and the judges
   together. It is not a hard ceiling: the harness checks it before each
   repeat and each phase and starts nothing more once it is reached, but
   what is running finishes, so a run can end above N. Each session of a
   skill subject also has its own cap, the scenario's `max_usd`, which
   Claude Code holds. Say both when the prompt asks for a cap.
   `--runtime` is `host`,
   `container`, or `vm`, and one the scenario lists: its first when the
   flag is not given. The harness refuses any other with exit 7 and
   runs nothing. So when the prompt names a runtime that `list` does
   not show for the scenario, run nothing, neither on that runtime nor
   on another, and say so. When the prompt names something with no flag
   behind it, say so and run without it.
4. Run the scenario, for example
   `uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1`.
   A run takes minutes and costs money at every provider selected. When
   the prompt has not said which judges or how many repeats, use the
   scenario's default judges and the default of 3 repeats, and say
   which they were.
5. When the prompt asks what a run would do rather than for a
   measurement, add `--dry-run`: it resolves everything, writes
   `run.json`, and calls nothing. A dry run leaves `run.json` and
   nothing else, so read that file and report the resolved plan: the
   subject command, the runtime, the judges with the model and the
   fallbacks each would use, the effort, the repeats, the run's spend
   cap when one was given, and the subject's own `max_usd`. For a
   scenario in phases, `run.json` lists each phase under `phases`, and
   `subject_argv` is only the first phase's command. Report each phase
   with the fields it holds: its `session` (`fresh`, or `resume` of the
   phase before), where it starts (`cwd`: the workspace or the output
   folder), whether it keeps the handoff note (`hint`), its bounds
   (`max_turns`, `max_usd`, `max_gate_reruns`, and `timeout_s`, the
   backstop), what follows a bound (`on_cap`), and its `argv`. Report the
   subject's `output` folder and its `gates`, which the harness runs on
   the final tree. `benchmark/README.md`, "A subject in phases", says
   what each field means. There is no
   score to report, and inventing one is the worst thing this skill
   could do.
6. The `container` runtime, named or the scenario's first, runs the
   subject with `docker run`, from an image it builds only when
   `--build` is passed. The first run on a machine needs `--build`; a
   later run adds it when the image's inputs changed. A build takes
   minutes, so say that before starting one.
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
- for a scenario in phases, how each phase ended and the cap that
  stopped any, and which gates passed on the final tree;
- the findings that matter, most severe first, in one line each;
- any provider that did not answer, with the reason it gave;
- the run folder path, and that it is checked in only once
  `run.py redact` has scanned it for keys.

Say what was measured, not what it means for the roadmap.
