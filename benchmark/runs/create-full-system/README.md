# create-full-system

The subject is Claude Code with the guideline's scaffold skills, run on
a virtual machine that has Docker. It is asked to build a whole system,
`free-journalism`, from a product spec: a platform where readers follow
and support independent journalists, and sources send them tips. It is
given the spec and the plugin, and no one answers its questions, so it
decides and records each decision in the tree. It works in phases, each
a fresh session bounded by money and time only. The scaffold builds the
system's skeleton. The MVP builds three loops on it, each driven end to
end by an integration test. With extras, a standalone review then reads
the tree, and a last phase closes the review's high findings. Four
agentic judges read the final tree beside two references: the guideline
with its lenses, and the guideline's reference implementation, a
different product in the same shape. They judge the shape, not the
domain. Each scores the tree from 0 to 100 against each reference, and
its score weighs the guideline 0.4 and the reference 0.6. The harness
also runs the tree's `make check` and `make test-integration` and
records them beside the scores. They never cap a score.

| Run | Started (UTC) | Subject | Effort | Repeats | anthropic | openai | gemini | xai | Overall | Cost (USD) | Commit | Claude Code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [create-full-system-447562f8](20260928-104821-create-full-system-447562f8/report.md) | 2026-09-28 06:33 | `claude-opus-5-5` | high | 1 | 81.2 | 73.2 | 97.0 | 79.6 | **82.8** | $200.41 | `09aad2f` | — |

## create-full-system-447562f8

A run with extras, in three folders. The first ran the scaffold, which
ended with subagent calls that had no result, so the harness ended the
repeat there and no judge scored it. The second resumed from the
scaffold's milestone. It ran the MVP, the review, the close, and the
four judges, and the openai judge hit its provider's rate limit and
scored nothing. The third ran that judge again and carried the other
three, so its scores are the run's. The subject ran on Claude Code
2.1.283, from `3009fa9` for the scaffold and `7e03524` for the rest.
The third folder ran no subject, so it records no Claude Code.

| Repeat | Stage | Folder | Status | Cost (USD) | Time |
| --- | --- | --- | --- | --- | --- |
| 0 | scaffold | `20260927-233327-create-full-system-11435123` | incomplete | $82.5281 | 2:42:19 |
| 0 | mvp | `20260928-065025-create-full-system-0a609288` | ok | $22.6312 | 0:55:34 |
| 0 | review | `20260928-065025-create-full-system-0a609288` | ok | $20.3509 | 0:10:31 |
| 0 | close | `20260928-065025-create-full-system-0a609288` | ok | $15.3011 | 0:19:06 |
| 0 | judges (anthropic, openai, gemini, xai) | `20260928-065025-create-full-system-0a609288` | missed: openai | $29.4815 | 0:04:11 |
| 0 | judges (openai) | `20260928-104821-create-full-system-447562f8` | ok | $30.1202 | 0:31:34 |

Total: $200.4131.

What the run measured:

- The review found 24 high findings in the tree the build left. 11 of
  them sit in files the MVP phase added or changed, 3 of those in files
  it added. The other 13 sit in scaffold files the MVP left alone. The
  scaffold stopped at its step 9, on a gate that still failed after its
  reruns, so its own review, step 10, never ran. This review was the
  first to read any of the tree.
- The close reported all 24 closed, and the harness's gates passed on
  the final tree. The close's tree differs from the MVP's in 131 files,
  +2,630/-277 lines, by `git diff --shortstat`.
- The MVP's checkpoint was not judged apart, so the run scores only the
  tree after the extras, and says nothing of what they moved the score.
