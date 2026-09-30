# browser-judge-swe

The subject is four chat products a reader would use: chatgpt.com,
claude.ai, gemini.google.com, and grok.com, each signed in, in a
browser. Each is asked one question: evaluate this repository as a
senior software engineer and architect, and score it from 0 to 100. The
prompt, and the contract that shapes the answer, are in
[prompt.md](../../browser/prompt.md), and
[arch-benchmark-browser](../../../skills/arch-benchmark-browser/SKILL.md)
runs it. Two t-shirt sizes pick each site's model and effort, as
[sizes.yaml](../../browser/sizes.yaml) maps them. A size names the
current model of its tier, so the model behind a size can move from one
run to the next, and each cell names the one that ran.

There is no judge. Each score is the product's own, from the answer's
`Score: NN/100` line. So a row says what four products thought of the
repository on one day, and the rows show how that moved from release to
release.

Two rows compare when their runs share the sizes, the prompt, and the
contract, as each run's `results.json` records them. The Set column
marks them: rows of one set compare, and rows of two sets do not. A
score marked `smaller-mode` ran on a smaller model or mode than its size
asked for, because the account locks that one, and it compares with no
other score. This page is in the repository the products evaluate, so a
product can read it. A run where an answer names this folder or an
earlier run, or says what an earlier run scored, read the earlier
scores: it is a set of its own, and its note says which site read them.

Each run's folder holds its `results.json` and one answer per session,
redacted. An answer is the product's own Markdown, from the copy
button under it, and its note says so where it is page text instead.
`[redacted]` marks each place a
conversation's address, a device or account name, or the name of the
guideline's reference implementation stood. No screenshot is checked
in. When `arch-benchmark-browser` runs in a checkout of this
repository, it writes the run's folder here and adds its row at the top.

The columns:

- Run: the run's folder, linking its `results.json`.
- Started (UTC): when the run started (`started_at`).
- Head: the commit the repository's default branch pointed at then
  (`repository_head`), short.
- Sizes: the model size, then the effort size.
- chatgpt.com, claude.ai, gemini.google.com, and grok.com: the score,
  linking the answer, or the status where there is none; then the model
  and effort labels the site had checked, and the status when it is not
  `ok`. A site asked twice in one run shows both, in order.
- Set: the rows that compare share a letter.
- Note: why a session is not `ok`, or why a run compares with none.

"—" marks what a run does not record.

| Run | Started (UTC) | Head | Sizes | chatgpt.com | claude.ai | gemini.google.com | grok.com | Set | Note |
|---|---|---|---|---|---|---|---|---|---|
| [20260930-142745](20260930-142745/results.json) | 2026-09-30 14:27 | `d98c218` | m, m | [94](20260930-142745/chatgpt.com.md) Latest, High | [72](20260930-142745/claude.ai.md) Opus 5.5, High | [errored](20260930-142745/gemini.google.com.md) 3.1 Pro | [80](20260930-142745/grok.com.md) Expert | C | claude.ai names the browser benchmark and says it read the run pages, so it may have read earlier scores; gemini.google.com: the second attempt printed no answer and no error |
| [20260929-212716](20260929-212716/results.json) | 2026-09-29 21:27 | `bbdbd26` | m, m | [94](20260929-212716/chatgpt.com.md) Latest, High | [78](20260929-212716/claude.ai.md) Opus 5.5, High | [85](20260929-212716/gemini.google.com.md) 3.1 Pro | [88](20260929-212716/grok.com.md) Fast; smaller-mode | B | grok.com: Expert needs a SuperGrok plan |
| [20260927-204330](20260927-204330/results.json) | 2026-09-27 20:43 | — | m, m | [94](20260927-204330/chatgpt.com.md) Latest, High | [84](20260927-204330/claude.ai.md) Opus 5, High | [85](20260927-204330/gemini.google.com.md) Pro | [89](20260927-204330/grok.com.md) Fast; smaller-mode | B | grok.com: Expert needs a SuperGrok plan |
| [20260920-224602](20260920-224602/results.json) | 2026-09-20 22:46 | — | xl, xl | [87](20260920-224602/chatgpt.com.md) Latest, 6 Pro | [71](20260920-224602/claude.ai.md) Fable 5.1, Max | [refused](20260920-224602/gemini.google.com.md) Flash Extended, then [85](20260920-224602/gemini.google.com.retry.md) Pro (the picker entry 3.1 Pro) | — | A | The sizes, the prompt, and the contract differ from every other run's, so it compares with none; grok.com was not a site yet; gemini.google.com: the first attempt errored, and the retry scored |
