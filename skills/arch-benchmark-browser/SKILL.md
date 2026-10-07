---
name: arch-benchmark-browser
description: "Run the benchmark prompt in the chatgpt.com, claude.ai, gemini.google.com, and grok.com products, signed in, and save each answer with its conversation URL as proof; in a checkout of this repository, also check the run in, redacted, with its row."
allowed-tools: Read, Write, Edit, Bash(mkdir:*), Bash(date:*), Bash(python3:*), Bash(git ls-remote:*), Bash(pbpaste:*), Bash(make runs), mcp__claude-in-chrome__tabs_context_mcp, mcp__claude-in-chrome__tabs_create_mcp, mcp__claude-in-chrome__tabs_close_mcp, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__computer, mcp__claude-in-chrome__read_page, mcp__claude-in-chrome__find, mcp__claude-in-chrome__get_page_text, mcp__claude-in-chrome__browser_batch
disable-model-invocation: true
---

# arch-benchmark-browser

Ask four products the same question and keep the answers with their
proof. The prompt, contract, size map, and schema live at
`../../benchmark/browser/` and
`../../benchmark/schema/browser-session.schema.json`, paths from this
skill's folder as `realpath` resolves it. If any is missing, stop and
say the installation is incomplete.

## Input

The arguments name two sizes, `model=<size> effort=<size>`, each of
`xs`, `s`, `m`, `l`, `xl`. A missing one is `m`. It may also
name a subset of sites (`sites=claude.ai,gemini.google.com`); the
default is all four. It may ask for a comparison with earlier runs:
`compare=<run_id>,<run_id>` names them, and `compare=all` means every
earlier run. Without `compare=`, nothing is compared.

## What the pages are like

Facts that decide how the steps below go. Read them before the browser.

- The read tools are permitted per domain, and not as a set. On some
  domains `get_page_text` and `read_page` are refused while a
  screenshot works; on others `find` is refused while `read_page`
  works. When a read tool is refused, use a screenshot and `zoom`, and
  transcribe from it. Say in the output which site was read that way.
- The composers on chatgpt.com, claude.ai, and grok.com are rich text:
  a line that starts with a dash and a space becomes a bullet. On
  chatgpt.com and claude.ai the next line gets one too, and backticks
  become inline code; grok.com keeps backticks as typed.
  gemini.google.com's composer is plain text. Step 5 types the contract
  so that no dash becomes a bullet.
- Enter sends on all four composers. A new line inside the message is
  `shift+Return`. Type one line, press `shift+Return`, type the next;
  a blank line is two `shift+Return`.
- A `browser_batch` has a deadline of its own: at most five ten-second
  waits and one screenshot per batch, or it times out. A tool that is
  refused once on a domain with "Permission denied for this action" may
  succeed on the next call; retry once before treating it as a fact.
- The tab group can hold tabs the skill did not open: an empty New Tab,
  or another agent's tabs. Touch only the tabs you create.
- A dialog that promotes a model or a feature, or asks to allow
  notifications, is closed by its most privacy-preserving choice ("Not
  now", "No thanks"), never by accepting it. The session's `note` says
  so.
- An account can lock a model, a mode, or an effort: clicking it opens
  a plan page or an upgrade prompt, or the entry does not stay checked.
  Never click Upgrade, or anything that buys.
- chatgpt.com: the control at the right of the composer opens a
  "Thinking effort" popover with a five-stop slider. The label above
  the slider names the stop the handle is on; `sizes.yaml` lists the
  five labels, left to right. A chevron beside that label opens the
  model list ("Latest" and older models). After a pick, the chip shows
  the stop's label, not the model.
- claude.ai: one menu at the right of the composer holds the current
  models, then "Effort" with five levels, then "More models" with the
  older ones. Picking a model closes the menu and resets the effort to
  its default, so the model is picked first and the effort second. The
  page may say "Computer actions available": the product can then run
  commands on the person's machine, and step 4 does not send there.
- gemini.google.com: click the composer once before opening the chip.
  The list is Flash-Lite, Flash, Pro, then, below a separator,
  "Extended thinking", which is a toggle layered on the current model
  and not a model of its own; the chip then reads "Flash Extended". The
  size map never asks for it. The chip shortens "3.1 Pro" to "Pro".
- grok.com: the chip at the right of the composer shows the mode. Its
  accessible name is "Model select", the name to give `find`. It opens
  one list of modes: Fast, Build, Auto, Expert, Heavy, and a
  "SuperGrok" row with an Upgrade button. There is no effort control;
  the "+" at the left holds files, projects, skills, and connectors.
  Auto, Expert, and Heavy need a SuperGrok plan. On an account without
  one, clicking them opens a plan page (the URL ends in `#subscribe`)
  and the chip keeps its mode; the × at the page's top right closes it.
  Never switch to "Private" at the top right. An answer advances only
  while its tab is in front.
- A finished answer is announced in text on three sites. chatgpt.com
  writes "Worked for" and a duration above it. claude.ai's page text
  contains "Claude finished the response". gemini.google.com shows the
  answer with the composer empty and no stop control. On these three,
  read that rather than the send button's shape.
- grok.com announces nothing in text. It writes "Worked for" above its
  answer while the text is still revealing, so that line is not the
  signal. Its answer is done when the page text is the same on two
  polls in a row, which step 6 spaces at least a minute apart. The
  button at the right of the composer is a check, not the signal.
  Before sending, with the composer empty, it is "Enter voice mode",
  drawn as a blue waveform in a circle. A stop control in its place
  means the answer is still coming.
- The page text before and after an answer is chrome: the time worked,
  the echoed prompt, the speaker label ("ChatGPT said:", "Claude
  responded:", "Gemini said"), a tool count, and after the answer the
  chip's label ("Fast") or a hover line ("Add to chat"). The answer
  starts after the speaker label and ends at its own last line.
  grok.com writes no speaker label: its answer starts after the echoed
  prompt, whose last line is the contract's last item, and after the
  "Worked for" line where one shows. Step 6 and step 7 both mean the
  answer so bounded. The score is the line that matches
  `Score: NN/100`, wherever it is. gemini.google.com's page text also
  holds the sidebar's conversation titles. They are the person's:
  never copy them.

## Procedure

1. Read `prompt.md`, which holds both the prompt and the contract, and
   `sizes.yaml`. The browser README is for people and is not an input.
   Resolve, per site, the model and effort labels the sizes ask for.
   Say them before touching the browser, and always put them in the
   run's `note` in `results.json`.
2. Note the run's start with `date -u +%Y-%m-%dT%H:%M:%SZ`, the form
   of every time the run records; make the run folder
   `~/Downloads/benchmark_browser/<YYYYMMDD-HHMMSS>/` from the same
   moment, in UTC. Every file of the run goes there. Then read
   the commit the repository's default branch points at, with
   `git ls-remote <url> HEAD`, where `<url>` is the repository URL in
   the prompt. The first field it prints is the run's
   `repository_head`, whatever each answer says it read. When the
   command fails, it is `unknown` and the run's `note` gives the error.
3. Call `tabs_context_mcp` with `createIfEmpty`; never use or close an
   empty tab it makes. Then `tabs_create_mcp` one tab per site, and
   navigate each to its new-chat URL:
   `https://chatgpt.com/`, `https://claude.ai/new`,
   `https://gemini.google.com/app`, `https://grok.com/`. Take a
   screenshot of each. A page that shows a sign-in button, a login
   form, or no composer is `not-signed-in`: record it, tell the person
   which site to sign in to, and go on with the sites that are. Never
   type an email or a password, ever.
4. Per site, first look, with `find` or a screenshot, for a line that
   says the product can act on the person's machine, such as
   claude.ai's "Computer actions available". When one shows, on any
   site, set nothing and send nothing: the session is `not-run`, and
   its `note` asks the person to turn that setting off. Never change
   it yourself. Some sites show the line only once a message is sent,
   such as claude.ai's "Connected" line under the device's name: look
   again right after the send and at every poll of step 6. When it
   shows, click the stop control ("Stop response") where one shows and
   record the session `not-run` with the same `note`: both times are
   the stop's moment, `polls` the polls made, and nothing it answered
   is scored.

   Otherwise set the model and the effort, then verify with a
   screenshot of the chip. `model_label` is the model picker's checked
   entry as the list writes it; `effort_label` is the checked effort
   entry or the stop's label. The chip is the check.
   - chatgpt.com: open the popover, open the chevron, click the model
     the size names; then click the slider stop whose label the size
     names.
   - claude.ai: open the menu and click the model, under "More models"
     when it is not at the top; open the menu again, open "Effort",
     click the level. The chip must read `<model> <effort>`.
   - gemini.google.com: click the composer, open the chip, click the
     model the size names. The chip must read the model's short name.
   - grok.com: open the chip, click the mode the size names. The chip
     must read the mode.

   Click the size's effort even when the chip already shows it. Where
   the size map's effort is `none`, `effort_label` is `none`. If the
   label does not match what the size asked for, try once more, then
   record the label the page shows and go on: the results carry what
   was actually used, never what was asked for.

   A locked model, mode, or effort is the exception, on every site, and
   is not tried once more. Close what opened. Then take that site's
   entries in `sizes.yaml`, in the same map (model or effort), for the
   smaller sizes, from the next smaller one down to `xs`: skip an entry
   already tried, and click the first one that stays checked (Fast on
   grok.com, for Expert or Heavy). Record that entry, name the locked
   one in `note` ("Expert needs a SuperGrok plan"), and step 7 records
   the session `smaller-mode`. A locked entry is not tried again in the
   run. When no smaller size's entry stays checked, send nothing: the
   session is `not-run`, and `note` names the locked entries.
5. Click the composer and type the prompt from `prompt.md`, a blank
   line, and the contract, line by line with `shift+Return` between
   lines. Type each of the contract's items without its leading dash:
   this is the one place that rule lives, and `contract` records the
   text as typed. Send with `Return`. The send time, from `date -u`, is
   the session's `started_at`. Take one screenshot showing the sent
   message and the chip; it is a check and is not saved.
6. Do step 4 and step 5 for every site first, then poll each site at
   most thirty times, and count its polls into `polls`. A poll starts
   at least a minute after the site's last one; where no other site's
   poll fills that minute, wait out the rest with ten-second waits. A
   poll is one batch: first a scaled (0.4) screenshot, which brings
   the tab to the front, then up to five ten-second waits. The poll
   also looks for step 4's line in its screenshot and any page text it
   reads. On claude.ai and grok.com the signal is in the page text:
   end the batch with one `get_page_text` and keep it to four waits,
   so the batch stays inside its deadline. Where `get_page_text` is
   refused, on either site, compare the poll's screenshot with the
   previous one instead: the same last line of the answer, and no stop
   control.
   - grok.com: the text is the same when the answer's length and its
     own last line match the previous poll's; the chrome after the
     answer is ignored. When they match, check the button beside the
     composer with `find` or `read_page`, or with a `zoom` where both
     are refused. "Enter voice mode" confirms the answer is done. A
     stop control means it is not, whatever the text did: keep
     polling. A tab that was not in front for its waits is never called
     done.

   Done is the signal named above. Record the finish time from `date -u`
   when the signal is seen. On grok.com, run `date -u` at the end of
   every poll: the finish time is the first of the two polls that
   matched. An error the product prints in place of an answer ends the
   polling of that attempt, and step 7's `errored` says what follows.
   When an answer is already done at the first poll, the finish time is
   the send time plus the site's own "Worked for" figure where it shows
   one, except on grok.com, or else the first poll's time, and `note`
   says which. After thirty polls or thirty minutes from the send,
   record `timed-out` with what the page shows so far.
7. When a site is done, read the conversation URL from
   `tabs_context_mcp` and drop its query string. Then read the answer,
   once: with the site's copy button and `pbpaste`, as
   `references/copy-answer.md` says, or, where that fails, with
   `get_page_text`. A copy button that shows later, as at step 9,
   changes nothing. Find the
   `Score: NN/100` line, and what the answer's Method says it read: a
   commit, a tag, or a branch and a date, for `read_version` ("not
   stated" when it names none). Save `<site>.md` in the run folder:

   ```text
   # <site>

   - URL: <url>
   - Model: <model_label>
   - Effort: <effort_label>
   - Sent: <started_at>
   - Finished: <finished_at> (how it was read)
   - Status: <status>
   - Score: <NN>/100
   - Read: <read_version>
   - Polls: <polls>
   - Note: <note>

   ## Answer

   <the answer>
   ```

   Where `score` is null, the header's line is `- Score: none`; where
   there is no note, it is `- Note: none`. A copied answer is saved as
   it came. A page-text answer is bounded as "What the pages are like"
   says, and the chrome on either side is left out; tool steps,
   citation chips ("GitHub", "10 sources"), and image captions stay as
   the page gave them. The statuses:
   - `ok`: a score was found.
   - `smaller-mode`: in place of `ok`, when step 4 fell back from a
     locked model, mode, or effort.
   - `no-score`: an answer and no score line; the number the answer
     gives elsewhere goes in `note`.
   - `refused`: the product declined.
   - `errored`: the product printed its own error in place of an answer
     ("I seem to be encountering an error"). Read that attempt's URL
     from `tabs_context_mcp`, without its query string, and try once
     more: navigate the site's tab to its new-chat URL, do step 4 again
     without clicking a locked entry, do step 5, and poll as step 6
     says. Every field of the session is the second attempt's, and its
     `polls` count from 0, with thirty polls and thirty minutes of its
     own. A fallback on the first attempt holds for the second: it is
     `smaller-mode` in place of `ok`, and `note` still names the locked
     entry. `note` keeps the first attempt, as one remark: the error in
     the page's words, its URL, and its poll count. When the second
     attempt errors too, the session is `errored`, and there is no
     third.
   - `timed-out`: as in step 6.
   - `not-signed-in` and `not-run`: as in steps 3 and 4. There is no
     answer. `url` is the address the tab shows, without its query
     string; `model_label` and `effort_label` are `not set`;
     `read_version` is `not stated`; `score` is null; `polls` is 0, or
     the polls made before step 4's stop; both times are the moment
     step 3 or step 4 found or stopped it; and `<site>.md` holds the
     header only.

   An answer cut short by a tool-use limit that still satisfies the
   contract is `ok` with a `note`; do not press Continue. `note` holds
   every remark on the session, in the order they arose, separated by
   "; ". A session with no remark has no `note` key.
8. Write `results.json` in the run folder in the schema, with `python3`:
   `run_id` is the folder name, `started_at` and `repository_head` from
   step 2, `finished_at` from the moment of writing, `prompt` and
   `contract` the two texts as typed, `sizes` the two sizes, `note`
   with the labels step 1 resolved, and one entry per site, whose
   `response_path` is `<site>.md`. Read it back with `python3` and hold
   it to the schema file, at the top and in each session: every
   required key is there, no key is there that the schema does not
   list, and each value is inside the `type`, `enum`, `minimum`,
   `maximum`, and `format` the schema gives its key. A value outside
   them is corrected, never bent to fit.
9. Per site that sent the prompt, bring its score line into view, or,
   where there is none, the first line the page wrote back: an answer,
   a `refused` decline, an `errored` message, or what a `not-run`
   session wrote before its stop, else step 4's line. Take one picture
   of it into the run folder as `references/evidence-picture.md` says.
   These are the evidence a pull request carries.
10. Close the tabs `tabs_create_mcp` opened in step 3, and no other.
11. Only when the arguments have `compare=`: compare this run with the
    earlier ones as `references/compare.md` says.
12. Only when the working directory is a checkout of this repository:
    check the run in there, redacted, with its row, and run
    `make runs`, as `references/check-in.md` says. It opens no pull
    request.

## Output

In prose: the run folder path; what was measured, which is the
repository, its `repository_head`, the version each site says it read,
and the two sizes; per site, the model and effort labels, the score,
`read_version`, and the conversation URL; any site that was not signed
in, not run, refused, errored, or timed out, with the reason in the
page's own words where it gave one; which sites were read from
screenshots. When the sites read different versions, say so. A
`smaller-mode` session is named with the entry its size asked for and
the entry that ran, and it is left out of any comparison of like for
like, across sites or across runs. The table step 11 makes follows,
when it was asked for. Last, what step 12 checked in, or why it
checked nothing in.
