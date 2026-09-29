# review-om

The subject is the `arch-review-om` skill, run by Claude Code in a
container. It is asked to review a small checkout against the object
model lenses and to report its findings by lens id. The checkout has
eight defects planted in it, one per lens, and the subject may read,
search, and list files, for at most 30 turns and $3. The judges get
the source it read, with line numbers, and the list of planted
findings, and they score the review from 0 to 100. Most of the score is
recall and precision against that list; the rest is form: a lens id, a
file and a line, the rule quoted, and the change to make. Beside the
scores, the harness counts which planted findings the review named.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Planted named | Cost (USD) | Commit | Claude Code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [review-om-27d470ec](20260927-083632-review-om-27d470ec/report.md) | 2026-09-27 15:36 | `claude-opus-5-5` | high | 1 | 85.0 | 82.0 | 60.0 | 70.0 | **74.3** | 8/8 | $0.56 | `012ac97` | 2.1.283 |
| [review-om-d56672be](20260927-064736-review-om-d56672be/report.md) | 2026-09-27 13:47 | `claude-opus-5-5` | high | 1 | 86.0 | 78.0 | 80.0 | 75.0 | **79.8** | 8/8 | $0.52 | — | — |
| [review-om-ac618e8a](20260927-055257-review-om-ac618e8a/report.md) | 2026-09-27 12:52 | `claude-opus-5-5` | high | 3 | 82.3 | 79.3 | 67.3 | 68.3 | **74.3** | 8/8, 8/8, 8/8 | — | — | — |
