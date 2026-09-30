# Check the run in

Step 12 reads this. It writes the run into a checkout of this
repository, as the benchmark's page shows it: text only, and redacted.
Every path below is the checkout's, from its root, which is the working
directory.

## When it runs

The working directory is a checkout when `architecture.md`,
`.claude-plugin/plugin.json`, `benchmark/runs/README.md`,
`benchmark/runs/browser-judge-swe/README.md`, and
`benchmark/schema/browser-session.schema.json` are all files there,
checked with `python3`. The working directory decides, never the
folder this skill was read from. When it is not a checkout, write
nothing, and the output says the run was not checked in and why.

Write nothing either, and say so, when no session of the run has a
score: there is nothing to measure. When
`benchmark/runs/browser-judge-swe/<run_id>/` is already there, stop and say so:
a checked-in run is never written over.

## The folder

1. With `python3`, make `benchmark/runs/browser-judge-swe/<run_id>/` and copy
   into it the run folder's `results.json` and each session's
   `response_path`, and nothing else: the `.jpg` files stay where they
   are.
2. With `python3`, on the copies, replace with `[redacted]`:
   - every session's `url` in `results.json`, whatever it holds, and
     the whole value of the `- URL:` line in each answer's header;
   - every other address of a conversation, in a `note` or an answer,
     which is what matches
     `https?://(?:chatgpt\.com/(?:c|share)/|claude\.ai/(?:chat|share)/|gemini\.google\.com/(?:app|share)/\w|grok\.com/(?:c|share)/)[^\s"')\]]*`.
3. Read every copy in full. Replace with `[redacted]`, with `python3`
   and by exact string, each of these where it stands, the words alone
   and not the sentence around them (`"[redacted], Connected"`):
   - a device's name or kind, such as the computer a page showed as
     connected, or "this Mac";
   - a person's name, handle, account, or email, such as a greeting
     that names the person;
   - a conversation title from a page's sidebar;
   - the name of the guideline's reference implementation, alone or as
     `<owner>/<name>`, in any case: it is the repository the closing
     Next section of `architecture.md` links.

   The prompt and the contract stay as typed, the repository URL in
   the prompt included: two runs compare only when both are the same
   text. Nothing else changes, so the diff against the run folder is
   the redactions alone. Read each copy back after the replacements.

## The row

Insert the run's row into the table of `benchmark/runs/browser-judge-swe/README.md`
with `Edit`, directly under its delimiter row, so the newest run is on
top. The cells, in the header's order, from the copied `results.json`:

- Run: the run id, linking `<run_id>/results.json`.
- Started (UTC): `started_at` as `YYYY-MM-DD HH:MM`.
- Head: the first seven characters of `repository_head`, in
  backticks, or `unknown` as it stands.
- Sizes: the model size, a comma, and the effort size: `m, m`.
- One cell per site: the score, linking the session's answer, or,
  where `score` is null, the status in its place, linking the same.
  Then a space and `model_label` and `effort_label`, joined by a comma
  and a space, each left out where it is `none` or `not set`. Then,
  when the status is neither `ok` nor already in the link, a semicolon,
  a space, and the status. A site the run has no session for is `—`.
  So, with the run id for `<run>`:

  ```text
  [<run>](<run>/results.json)
  [94](<run>/chatgpt.com.md) Latest, High
  [85](<run>/gemini.google.com.md) 3.1 Pro
  [88](<run>/grok.com.md) Fast; smaller-mode
  [refused](<run>/claude.ai.md) Opus 5.5, High
  ```

- Set: compare this run's `sizes`, `prompt`, and `contract` with those
  of each run a row names, in its `results.json` in the checkout, with
  `python3`. When one has all three the same, the row takes that row's
  letter; otherwise the next letter no row uses, `A` when there is none.
- Note: for each session that is not `ok`, its site, a colon, and the
  remark of its `note` that says why, cut at its first `:` or `;`, such
  as `grok.com: Expert needs a SuperGrok plan`, joined by a semicolon
  and a space. When the letter is new and other rows exist, the note
  first says which of the sizes, the prompt, and the contract differ
  from every other run's.
  `—` when there is nothing to say.

## The check

Run `make runs`. When it refuses, fix what it names in the files this
step wrote and run `make runs` again: the first run plus at most 3
reruns, then stop, leave the files as they are, and say which check
fails and why.

Stage nothing, commit nothing, and open no pull request: the person
reads the diff and does. The output names the folder written, the row,
and what `make runs` printed last.
