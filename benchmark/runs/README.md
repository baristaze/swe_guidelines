# Benchmark runs

Every run checked in here, the newest at the top and the oldest at the
bottom. Each row links to the run's report, and the run's
`results.json` holds the numbers the row copies.

The pull request that adds a run folder adds its row, by hand. A row
copies the run's `results.json`: the time it started, the scenario, the
subject's model, the judges' models and effort, the repeats, each
judge's mean, the overall mean, for a scenario that plants findings how
many the artifact named in each repeat, and what the run spent in all
(`spend.total_usd`, the subject included). A run recorded before the
harness kept its spend shows "—" there, and a total that leaves out an
unpriced model reads "at least". Nothing generates this file.

Judges: `claude-opus-5-5`, `gpt-6-sol`, `gemini-3.1-pro-preview`, and
`grok-4.7`, unless a row says otherwise.

| Run | Started (UTC) | Scenario | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Planted named | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [review-om-d56672be](20260927-064736-review-om-d56672be/report.md) | 2026-09-27 13:47 | review-om | `claude-opus-5-5` | high | 1 | 86.0 | 78.0 | 80.0 | 75.0 | **79.8** | 8/8 | $0.52 |
| [explain-tenancy-cb44b4aa](20260927-064736-explain-tenancy-cb44b4aa/report.md) | 2026-09-27 13:47 | explain-tenancy | `claude-opus-5-5` | high | 1 | 85.0 | 93.0 | 100.0 | 96.0 | **93.5** | — | $0.31 |
| [support-turn-b935612d](20260927-064736-support-turn-b935612d/report.md) | 2026-09-27 13:47 | support-turn | `claude-opus-5-5` | high | 1 | 76.0 | 78.0 | 75.0 | 80.0 | **77.3** | — | $0.17 |
| [review-om-ac618e8a](20260927-055257-review-om-ac618e8a/report.md) | 2026-09-27 12:52 | review-om | `claude-opus-5-5` | high | 3 | 82.3 | 79.3 | 67.3 | 68.3 | **74.3** | 8/8, 8/8, 8/8 | — |
| [explain-tenancy-35595007](20260927-055257-explain-tenancy-35595007/report.md) | 2026-09-27 12:52 | explain-tenancy | `claude-opus-5-5` | high | 3 | 86.0 | 95.7 | 100.0 | 95.3 | **94.3** | — | — |
| [support-turn-3b66f5e5](20260927-053907-support-turn-3b66f5e5/report.md) | 2026-09-27 12:39 | support-turn | `claude-opus-5-5` | high | 3 | 72.7 | 84.0 | 85.0 | 80.0 | **80.4** | — | — |
