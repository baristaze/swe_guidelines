# Benchmark runs

Every run checked in here, newest first. Each row links to the run's
report. The run's `results.json` holds the numbers the row copies.

The pull request that adds a run folder adds its row, by hand. A row
copies the run's `results.json`: the time it started, the scenario, the
subject's model, the judges' models and effort, the repeats, each
judge's mean, the overall mean, and, for a scenario that plants
findings, how many the artifact named in each repeat. Nothing generates
this file.

Judges: `claude-opus-5-5`, `gpt-6-sol`, `gemini-3.1-pro-preview`, and
`grok-4.7`, unless a row says otherwise.

| Run | Started (UTC) | Scenario | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Planted named |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [review-om-ac618e8a](20260927-055257-review-om-ac618e8a/report.md) | 2026-09-27 12:52 | review-om | `claude-opus-5-5` | high | 3 | 82.3 | 79.3 | 67.3 | 68.3 | **74.3** | 8/8, 8/8, 8/8 |
| [explain-tenancy-35595007](20260927-055257-explain-tenancy-35595007/report.md) | 2026-09-27 12:52 | explain-tenancy | `claude-opus-5-5` | high | 3 | 86.0 | 95.7 | 100.0 | 95.3 | **94.3** | — |
| [support-turn-3b66f5e5](20260927-053907-support-turn-3b66f5e5/report.md) | 2026-09-27 12:39 | support-turn | `claude-opus-5-5` | high | 3 | 72.7 | 84.0 | 85.0 | 80.0 | **80.4** | — |
