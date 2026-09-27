---
name: arch-benchmark-browser
description: "Run the benchmark prompt in the chatgpt.com, claude.ai, gemini.google.com, and grok.com products, signed in, and save each answer with its conversation URL as proof."
allowed-tools: Read, Write, Bash(mkdir:*), Bash(date:*), Bash(python3:*), mcp__claude-in-chrome__tabs_context_mcp, mcp__claude-in-chrome__tabs_create_mcp, mcp__claude-in-chrome__tabs_close_mcp, mcp__claude-in-chrome__navigate, mcp__claude-in-chrome__computer, mcp__claude-in-chrome__read_page, mcp__claude-in-chrome__find, mcp__claude-in-chrome__get_page_text, mcp__claude-in-chrome__browser_batch
disable-model-invocation: true
---

# arch-benchmark-browser

Ask four products the same question and keep the answers with their
proof. The prompt, the contract, the size map, and the schema live at
`${CLAUDE_SKILL_DIR}/../../benchmark/browser/` and
`${CLAUDE_SKILL_DIR}/../../benchmark/schema/browser-session.schema.json`.
If any is missing, stop and say the installation is incomplete.

## Input

`$ARGUMENTS` names two sizes, `model=<size> effort=<size>`, each one of
`xs`, `s`, `m`, `l`, `xl`. When one is missing, it is `m`. It may also
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
- An account can lock a model or a mode: clicking it opens a plan page
  or an upgrade prompt, or the entry does not stay checked. Never click
  Upgrade, or anything that buys.
- chatgpt.com: the control at the right of the composer opens a
  "Thinking effort" popover with a five-stop slider. The label above
  the slider names the stop the handle is on: Instant, Medium, High,
  Extra High, and 6 Pro, left to right. A chevron beside that label
  opens the model list ("Latest" and older models). After a pick, the
  chip shows the stop's label, not the model.
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
  polls a minute apart. The button at the
  right of the composer is a check, not the signal. Before sending,
  with the composer empty, it is "Enter voice mode", drawn as a blue
  waveform in a circle. A stop control in its place means the answer
  is still coming.
- The page text before and after an answer is chrome: the time worked,
  the echoed prompt, the speaker label ("ChatGPT said:", "Claude
  responded:", "Gemini said"), a tool count, and after the answer the
  chip's label ("Fast") or a hover line ("Add to chat"). The score is
  the line that matches `Score: NN/100`, wherever it is.
  gemini.google.com's page text also holds the sidebar's conversation
  titles. They are the person's: never copy them.

## Procedure

1. Read `prompt.md`, which holds both the prompt and the contract, and
   `sizes.yaml`. The browser README is for people and is not an input.
   Resolve, per site, the model and effort labels the sizes ask for.
   Say them before touching the browser; in a run nobody watches, they
   go into the run's `note` in `results.json`.
2. Note the run's start with `date -u +%Y-%m-%dT%H:%M:%SZ`; make the
   run folder `~/Downloads/benchmark_browser/<YYYYMMDD-HHMMSS>/` from
   the same moment, in UTC. Every file of the run goes there.
3. Call `tabs_context_mcp` with `createIfEmpty`, then `tabs_create_mcp`
   one tab per site, and navigate each to its new-chat URL:
   `https://chatgpt.com/`, `https://claude.ai/new`,
   `https://gemini.google.com/app`, `https://grok.com/`. Take a
   screenshot of each. A page that shows a sign-in button, a login
   form, or no composer is `not-signed-in`: record it, tell the person
   which site to sign in to, and go on with the sites that are. Never
   type an email or a password, ever.
4. Per site, set the model and the effort, then verify with a
   screenshot of the chip. `model_label` is the model picker's checked
   entry as the list writes it; `effort_label` is the checked effort
   entry or the stop's label. The chip is the check.
   - chatgpt.com: open the popover, open the chevron, click the model
     the size names; then click the slider stop whose label the size
     names.
   - claude.ai: first look for "Computer actions available" with `find`
     or a screenshot. When it shows, set nothing and send nothing: the
     session is `not-run`, and its `note` asks the person to turn
     computer actions off. Never change that setting yourself.
     Otherwise open the menu and click the model, under "More models"
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

   A locked model or mode is the exception, on every site. Close what
   opened and pick the next smaller entry that stays checked (Fast on
   grok.com). Record that entry, name the locked one in `note`
   ("Expert needs a SuperGrok plan"), and step 7 records the session
   `smaller-mode`. A locked entry is not tried again.
5. Click the composer and type the prompt from `prompt.md`, a blank
   line, and the contract, line by line with `shift+Return` between
   lines. Type each of the contract's items without its leading dash:
   this is the one place that rule lives, and `contract` records the
   text as typed. Send with `Return`. The send time, from `date -u`, is
   the session's `started_at`. Take one screenshot showing the sent
   message and the chip; it is a check and is not saved.
6. Do step 4 and step 5 for every site first, then poll each site at
   most thirty times, no more often than once a minute, and count its
   polls into `polls`. A poll is one batch: first a scaled (0.4)
   screenshot, which brings the tab to the front, then up to five
   ten-second waits. On claude.ai and grok.com the signal is in the
   page text: end the batch with one `get_page_text` and keep it to
   four waits, so the batch stays inside its deadline. Where
   `get_page_text` is refused, on either site, compare the poll's
   screenshot with the previous one instead: the same last line of the
   answer, and no stop control.
   - grok.com: the text is the same when the answer's length and its
     own last line match the previous poll's; the chrome after the
     answer is ignored. When they match, check the button beside the
     composer with `find` or `read_page`, or with a `zoom` where both
     are refused. "Enter voice mode" confirms the answer is done. A
     stop control means it is not, whatever the text did: keep
     polling. A tab that was not in front for its waits is never called
     done.

   Done is the signal named above. Record the finish time from `date -u`
   when the signal is seen. On grok.com, run `date -u` at every poll:
   the finish time is the first of the two polls that matched. When an
   answer is already done at the first poll, the finish time is the send
   time plus the site's own "Worked for" figure where it shows one,
   except on grok.com, or else the first poll's time, and `note` says
   which. After thirty polls or thirty minutes, record `timed-out` with
   what the page shows so far.
7. When a site is done, read the conversation URL from
   `tabs_context_mcp` and drop its query string. Read the answer with
   `get_page_text`, or from screenshots where that is refused. Find the
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

   The answer starts after the speaker label and ends at its own last
   line. The chrome on either side is left out; tool steps, citation
   chips ("GitHub", "10 sources"), and image captions stay as the page
   gave them. The statuses:
   - `ok`: a score was found.
   - `smaller-mode`: in place of `ok`, when step 4 fell back from a
     locked model or mode.
   - `no-score`: an answer and no score line; the number the answer
     gives elsewhere goes in `note`.
   - `refused`: the product declined.
   - `errored`: the product printed its own error in place of an answer
     ("I seem to be encountering an error"). Start a new chat on that
     site, do step 4 again without clicking a locked entry, and send
     once more before recording it.
   - `timed-out`: as in step 6.
   - `not-signed-in` and `not-run`: as in steps 3 and 4. There is no
     answer: `score` is null, `polls` is 0, both times are the moment
     of recording, and `<site>.md` holds the header only.

   An answer cut short by a tool-use limit that still satisfies the
   contract is `ok` with a `note`; do not press Continue. `note` holds
   every remark on the session, in the order they arose, separated by
   "; ".
8. Write `results.json` in the run folder in the schema, with `python3`:
   `run_id` is the folder name, `started_at` from step 2 and
   `finished_at` from the moment of writing, `prompt` and `contract`
   the two texts as typed, `sizes` the two sizes, and one entry per
   site. Read it back and check every required key of the schema is
   there and no other.
9. Per site, bring the score line into view: click an empty margin of
   the page to clear a stray selection or menu, `find` the score line,
   and `scroll_to` its reference. Take one `zoom` of the conversation
   column, right of the sidebar, with `save_to_disk`: it shows the
   score line and the chip, and no conversation title. Copy it into the
   run folder as `<site>.jpg` with `python3` (`shutil.copyfile` from
   the path the tool reports); the tool saves JPEG. These are the
   evidence a pull request carries.
10. Close the tabs you created, and no other.
11. Only when `$ARGUMENTS` has `compare=`. List the earlier runs with
    `python3`:
    `sorted((Path.home() / "Downloads" / "benchmark_browser").glob("*/results.json"))`,
    since the tools have no `ls` and `glob` does not expand `~`. With
    run ids named, read those only, and name a named run that has no
    `results.json` as missing. Keep the runs whose `prompt` and
    `contract` are both the same text as this run's, and name each run
    left out and why. Runs with other `sizes` are kept apart, in a table
    of their own. The comparison is a table in the output: a row per
    site, a column per run, and in each cell the score, the model and
    effort labels, and `read_version`. Only `ok` sessions are compared;
    any other shows its status.

## Output

In prose: the run folder path; what was measured, which is the
repository, the version each site says it read, and the two sizes; per
site, the model and effort labels, the score, `read_version`, and the
conversation URL; any site that was not signed in, not run, refused,
errored, or timed out, with the reason in the page's own words where it
gave one; which sites were read from screenshots. When the sites read
different versions, say so. A `smaller-mode` session is named with the
mode its size asked for and the mode that ran, and it is left out of any
comparison of like for like, across sites or across runs. The table
step 11 makes follows, when it was asked for.
