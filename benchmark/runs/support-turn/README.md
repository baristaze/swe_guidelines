# support-turn

The subject is a model asked directly, with no skill and no tool: the
harness sends the question and keeps the answer. The question is an
on-call page. A service returns 500 for one tenant only, on one
endpoint, since a deploy an hour ago, and nothing changed in that
endpoint's code. The model answers as the person on call: what to look
at, in what order, and what each step rules out. It is given nothing
but the question. The judges score the answer from 0 to 100 as an
engineer on call would. A good answer starts from the signals a request
id ties together, moves from the deploy to the data, and names the
causes only one tenant would hit. Points come off for a checklist that
ignores the question, steps that need access the answer does not have,
and a cause named with no way to confirm it.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Cost (USD) | Commit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [support-turn-f6a3fe89](20260927-083628-support-turn-f6a3fe89/report.md) | 2026-09-27 15:36 | `claude-opus-5-5` | high | 1 | 72.0 | 80.0 | 75.0 | 84.0 | **77.8** | $0.18 | `012ac97` |
| [support-turn-b935612d](20260927-064736-support-turn-b935612d/report.md) | 2026-09-27 13:47 | `claude-opus-5-5` | high | 1 | 76.0 | 78.0 | 75.0 | 80.0 | **77.3** | $0.17 | — |
| [support-turn-3b66f5e5](20260927-053907-support-turn-3b66f5e5/report.md) | 2026-09-27 12:39 | `claude-opus-5-5` | high | 3 | 72.7 | 84.0 | 85.0 | 80.0 | **80.4** | — | — |
