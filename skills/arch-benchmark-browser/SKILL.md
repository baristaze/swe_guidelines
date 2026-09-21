---
name: arch-benchmark-browser
description: "Run the benchmark prompt through chatgpt.com, claude.ai, and gemini.google.com in a browser the person has signed in to: pick the model and the effort from two t-shirt sizes, check the picker before sending, send the prompt and its contract, wait for the answer, and save it with its conversation URL as proof. Use when a release needs scores from the products a reader would use rather than from the API."
allowed-tools: Read, Write, Bash(mkdir:*), Bash(date:*), Bash(python3:*), mcp__claude-in-chrome__tabs_context_mcp, mcp__claude-in-chrome__tabs_create_mcp, mcp__claude-in-chrome__tabs_close_mcp, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__computer, mcp__claude-in-chrome__read_page, mcp__claude-in-chrome__find, mcp__claude-in-chrome__get_page_text, mcp__claude-in-chrome__browser_batch
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

## What the pages are like

Facts that decide how the steps below go. Read them before the browser.

- The read tools are permitted per domain, and not as a set. On some
  domains `get_page_text` and `read_page` are refused while a
  screenshot works; on others `find` is refused while `read_page`
  works. When a read tool is refused, use a screenshot and `zoom`, and
  transcribe from it. Say in the output which site was read that way.
- The composers on chatgpt.com and claude.ai are rich text: a line that
  starts with a dash and a space becomes a bullet and the next line gets one too, and
  backticks become inline code. gemini.google.com's composer is plain
  text. So the contract is typed without the leading dash on every
  site, one line per item, and the results carry the text as sent.
- Enter sends on all three composers. A new line inside the message is
  `shift+Return`. Type one line, press `shift+Return`, type the next;
  a blank line is two `shift+Return`.
- A `browser_batch` has a deadline of its own: at most five ten-second
  waits and one screenshot per batch, or it times out. A tool that is
  refused once on a domain with "Permission denied for this action" may
  succeed on the next call; retry once before treating it as a fact.
- chatgpt.com: the control at the right of the composer opens a
  "Thinking effort" popover with a five-stop slider. The stop labels
  are names, not effort words ("Instant" at the left, a model-shaped
  name at the right). A chevron beside the stop name opens the model
  list ("Latest" and older models). After a pick, the chip shows the
  stop, not the model.
- claude.ai: one menu at the right of the composer holds the models
  and, below them, "Effort" with five levels. Picking a model closes
  the menu and resets the effort, so the model is picked first and the
  effort second, and the chip must be read after both.
- gemini.google.com: the model chip appears only after the composer
  is clicked once. The list is Flash-Lite, Flash, Pro, then, below a
  separator, "Extended thinking", which is a toggle layered on the
  current model and not a model of its own; the chip then reads
  "Flash Extended". The size map never asks for it. The chip shortens
  "3.1 Pro" to "Pro".
- A finished answer is announced in text: chatgpt.com writes "Worked
  for" and a duration above it; claude.ai's page text contains "Claude
  finished the response"; gemini.google.com shows the answer with the
  composer empty and no stop control. Read that rather than the send
  button's shape.
- The first line of a page's text is chrome (the time worked, the
  echoed prompt, a tool count), not the answer. The score is the line
  that matches `Score: NN/100`, wherever it is.

## Procedure

1. Read `prompt.md` and `sizes.yaml`. Resolve, per site, the model
   label and the effort label the sizes ask for. Say them before
   touching the browser.
2. Note the run's start with `date -u +%Y-%m-%dT%H:%M:%SZ`; make the
   run folder `~/Downloads/benchmark_browser/<YYYYMMDD-HHMMSS>/` from
   the same moment. Every file of the run goes there.
3. Call `tabs_context_mcp` with `createIfEmpty`. If the group already
   holds a tab on a site, reuse it and navigate it to the new-chat URL;
   otherwise `tabs_create_mcp` one per site. The URLs:
   `https://chatgpt.com/`, `https://claude.ai/new`,
   `https://gemini.google.com/app`. Take a screenshot of each. A page
   that shows a sign-in button, a login form, or no composer is
   `not-signed-in`: record it, tell the person which site to sign in
   to, and go on with the sites that are. Never type an email or a
   password, ever.
4. Per site, set the model and the effort, then verify with a
   screenshot of the chip, and record the label the chip shows:
   - chatgpt.com: open the popover, open the chevron, click the model
     the size names, then click the slider stop the size names,
     counting from the left. `model_label` is the model list entry
     that was checked; `effort_label` is the stop's own label.
   - claude.ai: open the menu, click the model; open it again, open
     "Effort", click the level. The chip must read `<model> <effort>`.
   - gemini.google.com: click the composer, open the chip, click the
     model the size names. The chip must read the model's short name.
   If the label does not match what the size asked for, try once more,
   then record the label the page shows and go on: the results carry
   what was actually used, never what was asked for.
5. Click the composer and type the prompt from `prompt.md`, a blank
   line, and the contract, line by line with `shift+Return` between
   lines and without the leading dash on the contract's items. Send
   with `Return`. Record the send time from `date -u` and take one
   screenshot showing the sent message and the chip.
6. Do step 4 and step 5 for every site first, then poll. Poll each
   site with a batch of up to five ten-second waits and one scaled
   (0.4) screenshot, no more often than once a minute per site. Done
   is the text signal named above. Give a site up to thirty minutes;
   past that, record `timed-out` with what the page shows so far.
   Record the finish time from `date -u` when the signal is seen; when
   it was missed and the page shows a relative time or a "Worked for"
   duration, compute it from that and say it is approximate.
7. When a site is done: read the conversation URL from
   `tabs_context_mcp` (it changed from the new-chat URL to one with the
   conversation's id in it); read the answer with `get_page_text`, or
   from screenshots where that is refused; find the `Score: NN/100`
   line. Save `<site>.md` in the run folder: a header with the URL, the
   model label, the effort label, the sent and finished times, then the
   answer as the page gave it. Statuses: `ok` when a score was found;
   `no-score` when there is an answer and no such line, with the number
   the answer gives elsewhere in `note`; `refused` when the product
   declined; `errored` when the product printed its own error in place
   of an answer ("I seem to be encountering an error"), in which case
   start a new chat on that site and send once more before recording
   it; `timed-out` as in step 6. An answer cut short by a tool-use limit
   that still satisfies the contract is `ok` with a `note`; do not
   press Continue.
8. Write `results.json` in the run folder in the schema, with `python3`:
   `run_id` is the folder name, `started_at` from step 2 and
   `finished_at` from the moment of writing, `prompt` and `contract`
   the two texts as sent, `sizes` the two sizes, and one entry per
   site. Read it back and check every required key of the schema is
   there and no other.
9. Take one screenshot per site of the finished conversation showing
   the score line and the chip, with `save_to_disk`. Copy each into
   the run folder as `<site>.png` with `python3` (`shutil.copyfile`
   from the path the screenshot reports): they are the evidence a pull
   request carries.
10. Close the tabs you created; leave the ones you reused on the
    conversation pages unless the person asked otherwise.

## Output

In prose: the run folder path; per site, the model and effort labels
the page showed, the score, and the conversation URL; any site that was
not signed in, refused, errored, or timed out, with the page's own
words; which sites were read from screenshots. Say what was measured.
Do not compare the scores to an earlier run unless the prompt asks.
