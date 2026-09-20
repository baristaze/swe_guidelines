---
name: arch-benchmark-browser
description: "Run the benchmark prompt through chatgpt.com, claude.ai, and gemini.google.com in a browser the person has signed in to: pick the model and the effort from two t-shirt sizes, check the picker before sending, send the prompt and its contract, wait for the answer, and save it with its conversation URL as proof. Use when a release needs scores from the products a reader would use rather than from the API."
allowed-tools: Read, Write, Bash(ls:*), Bash(mkdir:*), Bash(date:*), Bash(cat:*), mcp__claude-in-chrome__tabs_context_mcp, mcp__claude-in-chrome__tabs_create_mcp, mcp__claude-in-chrome__tabs_close_mcp, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__computer, mcp__claude-in-chrome__read_page, mcp__claude-in-chrome__find, mcp__claude-in-chrome__get_page_text, mcp__claude-in-chrome__browser_batch
---

# arch-benchmark-browser

Ask three products the same question and keep the answers with their
proof. The prompt, the contract, the size map, and the schema live at
`${CLAUDE_SKILL_DIR}/../../benchmark/browser/` and
`${CLAUDE_SKILL_DIR}/../../benchmark/schema/browser-session.schema.json`.
If any is missing, stop and say the installation is incomplete.

## Input

`$ARGUMENTS` names two sizes, `model=<size> effort=<size>`, each one of
`xs`, `s`, `m`, `l`, `xl`. When one is missing, it is `m`. It may also
name a subset of sites (`sites=claude.ai,gemini.google.com`); the
default is all three.

## Procedure

1. Read `prompt.md` and `sizes.yaml`. Resolve, per site, the model
   label and the effort label the sizes ask for. Say them before
   touching the browser.
2. Make the run folder: `~/Downloads/benchmark_browser/<YYYYMMDD-HHMMSS>/`
   from `date -u +%Y%m%d-%H%M%S`. Every file of the run goes there.
3. Call `tabs_context_mcp` with `createIfEmpty`. Open one tab per site
   (`tabs_create_mcp`, then `navigate`): `https://chatgpt.com/`,
   `https://claude.ai/new`, `https://gemini.google.com/app`. Take a
   screenshot of each. A page that shows a sign-in button, a login
   form, or no composer is `not-signed-in`: record it in
   `results.json`, tell the person which site to sign in to, and go on
   with the sites that are. Never type a password or an email, ever.
4. Per site, set the model and the effort, then verify:
   - chatgpt.com: the model is fixed behind the composer; the control
     at the right of the composer opens a "Thinking effort" slider with
     five stops. Click the stop the size names, counting from the left.
     Take a screenshot; the control's label must show the stop you
     chose (the leftmost reads "Instant").
   - claude.ai: the control at the right of the composer opens the
     model list; click the model the size names; open it again, open
     "Effort", click the level the size names. Take a screenshot; the
     control must read `<model> <effort>`.
   - gemini.google.com: the control at the right of the composer opens
     the model list; click the entry the size names. Take a
     screenshot; the control must read that entry.
   If the label does not match what the size asked for, try once more,
   then record the label the page shows and go on: the results carry
   what was actually used, never what was asked for.
5. Click the composer, type the prompt from `prompt.md`, a blank line,
   and the contract, exactly as the file has them. Send with Enter (on
   claude.ai and gemini.google.com the composer sends on Enter; on
   chatgpt.com too). Note the time.
6. Wait. An evaluation of a repository takes minutes at the larger
   sizes. Poll with `wait` of ten seconds and a scaled screenshot;
   the answer is done when the stop button has turned back into a send
   or microphone control and the page has stopped growing. Give a site
   up to thirty minutes; past that, record `timed-out` with what the
   page shows so far. Do the three sites in turn, sending all three
   first and then polling each, so the waits overlap.
7. When a site is done: read the conversation URL from
   `tabs_context_mcp` (it changed from the new-chat URL to one with the
   conversation's id in it); read the answer with `get_page_text`;
   read the score from the first line of the answer (`Score: NN/100`),
   and when there is none, record `no-score` and the number the answer
   gives elsewhere, in `note`. Save `<site>.md` in the run folder: a
   header with the URL, the model label, the effort label, the sent
   and finished times, then the answer as the page gave it. A refusal
   or an answer that evaluated something else is `refused` with the
   page's own words in `note`.
8. Write `results.json` in the run folder in the schema: `run_id` is
   the folder name, `prompt` and `contract` are the two texts as sent,
   `sizes` the two sizes, and one entry per site. Read it back and
   check every required key is there.
9. Close the tabs you opened unless the person asked to keep them.

## Output

In prose: the run folder path; per site, the model and effort labels
the page showed, the score, and the conversation URL; any site that was
not signed in, refused, or timed out, with the page's own words. Say
what was measured. Do not compare the scores to an earlier run unless
the prompt asks.
