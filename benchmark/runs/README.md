# Benchmark runs

Every run checked in here, in the folder of its scenario. Runs of one
scenario compare with each other, and runs of two scenarios do not, so
each scenario has a folder and a page of its own: what the subject is
asked to do, what it is given, how it is scored, and its runs.

- [create-full-system](create-full-system/README.md): the scaffold
  skills building a whole system from a product spec, and with extras
  a review and its fixes.
- [explain-tenancy](explain-tenancy/README.md): the `arch-explain`
  skill answering one question about the tenant fence.
- [review-om](review-om/README.md): the `arch-review-om` skill
  reviewing a checkout with ten planted defects.
- [support-turn](support-turn/README.md): a model answering an on-call
  question directly, with no skill.
- [browser-judge-swe](browser-judge-swe/README.md): four chat products a reader would use,
  each scoring the repository from 0 to 100. It is not a harness
  scenario: `arch-benchmark-browser` runs it, there are no judges, and
  its page names its own columns. The skill writes each run's folder
  and row.

## A scenario's page

Each page has one table of its runs, the newest at the top and the
oldest at the bottom, with only the columns that scenario records. A
run and its resumes are one row: a run the harness stopped, and the
runs that resumed it or its judges, are one measurement, however many
run folders it took. The row links the newest folder's report, and its
scores are that folder's.

The pull request that adds a run folder adds its row by hand, or moves
the row of the run it resumes onto it. The first run of a scenario adds
the scenario's folder, its page, and its line here. Nothing generates
these pages, and `make runs` holds them to the run folders.

The columns copy the newest folder's `results.json`, except where one
says it reads every folder of the run's chain:

- Started (UTC): when the run started, as its first folder records it
  (`started_at`).
- Subject: the subject's model.
- Effort: the judges' effort.
- Repeats: how many times the subject ran.
- anthropic, openai, gemini, and xai: each judge's mean.
- Overall: the mean of the judges' means.
- Planted named: for a scenario that plants findings, how many of them
  the artifact named in each repeat.
- Cost (USD): what the run spent in all, the subject included: the sum
  of each of its folders' `spend.total_usd`. A total that leaves out an
  unpriced model, or counts a phase at the harness's estimate, reads
  "at least".
- Commit: the commit the run ran from, short
  (`versions.checkout.commit`). The row of a run and its resumes names
  each distinct commit of its folders, oldest first.
- Claude Code: the version of Claude Code that ran the subject. The
  cell shows the version number from `versions.claude_code`. The row of
  a run and its resumes names each distinct version of the folders that
  ran a phase of the subject; a carried phase, and a folder that ran
  only judges, do not count. A scenario that runs no skill (`qa`,
  `command`) records no Claude Code, so its table has no such column.

"—" marks what a run does not record. A run recorded before the
harness kept its spend or its versions has none to show.

Under the table, a run in phases has its stage table, under a heading
of its row's name. It copies the chain in the newest folder's report:
each stage, a phase or a repeat's judges, with the run folder that ran
it, its status, its cost, and its time, and the total.

Judges: `claude-opus-5-5`, `gpt-6-sol`, `gemini-3.1-pro-preview`, and
`grok-4.7`, unless a row says otherwise.
