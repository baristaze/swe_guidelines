# explain-tenancy

The subject is the `arch-explain` skill, run by Claude Code in a
container. It is asked one question: how does the guideline hold the
tenant fence, and what proves it? It is given the plugin and nothing
else to read, and it may read, search, and list files, for at most 14
turns and $2. The judges score its answer from 0 to 100 as a senior
architect would. A full answer names the tenant predicate as the fence,
the cross-tenant tests as its evidence, and the deliberate breach as
the proof the suite would catch a hole, and it cites the guideline by
section title. Points come off for general advice, a section number,
an invented rule, and length that adds nothing.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Cost (USD) | Commit | Claude Code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [explain-tenancy-a13cc441](20260927-083630-explain-tenancy-a13cc441/report.md) | 2026-09-27 15:36 | `claude-opus-5-5` | high | 1 | 88.0 | 94.0 | 100.0 | 96.0 | **94.5** | $0.29 | `012ac97` | 2.1.283 |
| [explain-tenancy-cb44b4aa](20260927-064736-explain-tenancy-cb44b4aa/report.md) | 2026-09-27 13:47 | `claude-opus-5-5` | high | 1 | 85.0 | 93.0 | 100.0 | 96.0 | **93.5** | $0.31 | — | — |
| [explain-tenancy-35595007](20260927-055257-explain-tenancy-35595007/report.md) | 2026-09-27 12:52 | `claude-opus-5-5` | high | 3 | 86.0 | 95.7 | 100.0 | 95.3 | **94.3** | — | — | — |
