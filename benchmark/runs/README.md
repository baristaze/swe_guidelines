# Benchmark runs

Every run checked in here, one section per scenario, in name order, as
`uv run benchmark/run.py list` prints the scenarios. Runs of one
scenario compare with each other, and runs of two scenarios do not. So
each scenario has a table of its own, with the newest run at the top
and the oldest at the bottom, and only the columns that scenario
records.

The pull request that adds a run folder adds its row by hand, in the
section of the run's scenario. The first run of a scenario adds the
section too: `## <scenario>`, one line on what the scenario measures,
and the table. Nothing generates this file.

Each row links to the run's report and copies the run's
`results.json`:

- Started (UTC): when the run started (`started_at`).
- Subject: the subject's model.
- Effort: the judges' effort.
- Repeats: how many times the subject ran.
- anthropic, openai, gemini, and xai: each judge's mean.
- Overall: the mean of the judges' means.
- Planted named: for a scenario that plants findings, how many of them
  the artifact named in each repeat.
- Cost (USD): what the run spent in all, the subject included
  (`spend.total_usd`). A total that leaves out an unpriced model reads
  "at least".
- Commit: the commit the run ran from, short
  (`versions.checkout.commit`).
- Claude Code: the version of Claude Code that ran the subject. The
  cell shows the version number from `versions.claude_code`. A scenario
  that runs no skill (`qa`, `command`) records no Claude Code, so its
  table has no such column.

"—" marks what a run does not record. A run recorded before the
harness kept its spend or its versions has none to show.

Judges: `claude-opus-5-5`, `gpt-6-sol`, `gemini-3.1-pro-preview`, and
`grok-4.7`, unless a row says otherwise.

## create-full-system

It measures the scaffold skills building a whole system from a product spec, and with extras a review and its fixes, judged against the guideline and its reference implementation.

The three rows are one run, with extras. The oldest ran the scaffold,
and the harness ended its repeat there, so no judge scored it; its
`results.json` counts the repeat as failed, at 0. The middle one resumed
from the scaffold's milestone and ran the MVP, the review, the close,
and the four judges, of which the openai judge hit a rate limit and
scored nothing. The newest resumed those judges: it ran the openai judge
and carried the other three, so its 82.8 is the run's score. The three
spent $200.41 together.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Cost (USD) | Commit | Claude Code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [create-full-system-447562f8](20260928-104821-create-full-system-447562f8/report.md) | 2026-09-28 17:48 | `claude-opus-5-5` | high | 1 | 81.2 | 73.2 | 97.0 | 79.6 | **82.8** | $30.12 | `09aad2f` | — |
| [create-full-system-0a609288](20260928-065025-create-full-system-0a609288/report.md) | 2026-09-28 13:50 | `claude-opus-5-5` | high | 1 | 81.2 | — | 97.0 | 79.6 | **85.9** | $87.76 | `7e03524` | 2.1.283 |
| [create-full-system-11435123](20260927-233327-create-full-system-11435123/report.md) | 2026-09-28 06:33 | `claude-opus-5-5` | high | 1 | — | — | — | — | — | $82.53 | `3009fa9` | 2.1.283 |

## explain-tenancy

It measures the `arch-explain` skill on one question about the tenant fence.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Cost (USD) | Commit | Claude Code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [explain-tenancy-a13cc441](20260927-083630-explain-tenancy-a13cc441/report.md) | 2026-09-27 15:36 | `claude-opus-5-5` | high | 1 | 88.0 | 94.0 | 100.0 | 96.0 | **94.5** | $0.29 | `012ac97` | 2.1.283 |
| [explain-tenancy-cb44b4aa](20260927-064736-explain-tenancy-cb44b4aa/report.md) | 2026-09-27 13:47 | `claude-opus-5-5` | high | 1 | 85.0 | 93.0 | 100.0 | 96.0 | **93.5** | $0.31 | — | — |
| [explain-tenancy-35595007](20260927-055257-explain-tenancy-35595007/report.md) | 2026-09-27 12:52 | `claude-opus-5-5` | high | 3 | 86.0 | 95.7 | 100.0 | 95.3 | **94.3** | — | — | — |

## review-om

It measures the `arch-review-om` skill over a checkout with eight planted defects.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Planted named | Cost (USD) | Commit | Claude Code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [review-om-27d470ec](20260927-083632-review-om-27d470ec/report.md) | 2026-09-27 15:36 | `claude-opus-5-5` | high | 1 | 85.0 | 82.0 | 60.0 | 70.0 | **74.3** | 8/8 | $0.56 | `012ac97` | 2.1.283 |
| [review-om-d56672be](20260927-064736-review-om-d56672be/report.md) | 2026-09-27 13:47 | `claude-opus-5-5` | high | 1 | 86.0 | 78.0 | 80.0 | 75.0 | **79.8** | 8/8 | $0.52 | — | — |
| [review-om-ac618e8a](20260927-055257-review-om-ac618e8a/report.md) | 2026-09-27 12:52 | `claude-opus-5-5` | high | 3 | 82.3 | 79.3 | 67.3 | 68.3 | **74.3** | 8/8, 8/8, 8/8 | — | — | — |

## support-turn

It measures a model answering an on-call question directly, with no skill.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Cost (USD) | Commit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [support-turn-f6a3fe89](20260927-083628-support-turn-f6a3fe89/report.md) | 2026-09-27 15:36 | `claude-opus-5-5` | high | 1 | 72.0 | 80.0 | 75.0 | 84.0 | **77.8** | $0.18 | `012ac97` |
| [support-turn-b935612d](20260927-064736-support-turn-b935612d/report.md) | 2026-09-27 13:47 | `claude-opus-5-5` | high | 1 | 76.0 | 78.0 | 75.0 | 80.0 | **77.3** | $0.17 | — |
| [support-turn-3b66f5e5](20260927-053907-support-turn-3b66f5e5/report.md) | 2026-09-27 12:39 | `claude-opus-5-5` | high | 3 | 72.7 | 84.0 | 85.0 | 80.0 | **80.4** | — | — |
