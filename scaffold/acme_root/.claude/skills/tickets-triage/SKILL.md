---
name: tickets-triage
description: "Triage the open tickets whose subject is this repository: for each, read it and check it against the code on main, the merged and open pull requests, and the history, then give it a verdict (done, moot, partly done, still relevant, in flight, out of scope, unsure) with the evidence. Writes a report; with --apply, and the person's word in the session, it comments the evidence and closes the done and the moot. Never changes code."
allowed-tools: Read, Grep, Glob, Write, Bash(git:*), Bash(gh pr:*), mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__get_issue, mcp__claude_ai_Linear__list_comments, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_issue_statuses
---

# tickets-triage

Which open tickets are stale. A ticket is judged against the code as it
is on `main`, not against its own text: a fix may have landed under
another ticket's name, and a ticket's premise may no longer hold.

## Input

`[--apply] [--team <name>] [--project <name>] [--limit <n>]`

The tracker is Linear, the team `Acme` (the tools take the team's name,
not its key), whose ticket ids read `ACME-<n>`, by default. The scope is
every open ticket whose subject is this repository: the `Acme`
project, and tickets in no project or in another project whose title or
text names this repository or a path, a table, a route, or a skill of
it. `--limit` triages that many of the scope, newest first, and the
report says so. `--apply` closes what the verdicts allow, as step 6
says; without it the skill only reports.

## Role and credential

None of the platform's: the skill reads no environment and no database.
It reads the tracker through the session's Linear connector, under the
person's own sign-in, and the repository through `git` and `gh`. The
connector's writes (a comment, a status) are not pre-approved: each one
is the person's to allow.

## Procedure

1. Fetch `origin` and read `main` as it is: `git fetch origin`, then
   every check below against `origin/main` (`git show
   origin/main:<path>`, `git log origin/main`), never the checkout's own
   branch. Note the commit.
2. List the open tickets of the scope: every status of type `triage`,
   `backlog`, `unstarted`, or `started` (a `Duplicate` status is closed).
   `list_issues` has no filter for "no project": list the team and keep
   the rows by their `project` field. Read each candidate in full with
   `get_issue` and `includeRelations: true` (a duplicate shows only
   there), and its comments with `list_comments`; the scope is decided on
   the text, not the title.
3. Find the evidence for each. What the ticket asks for, looked up in
   the code on `main` (the file and line that does it, or still does not).
   The pull requests that name it: the ids live in pull request bodies
   (`Closes ACME-<n>`), not in the squashed commits, and GitHub's search
   is fuzzy, so match exactly:
   `gh pr list --state all --limit 300 --json number,title,state,body --jq
   '.[] | select(.title + .body | test("ACME-<n>\\b")) | [.number, .state, .title]'`.
   When no pull request names it, search its subject
   (`gh pr list --state merged --search "<words>"`). Work in flight: an
   open pull request whose files or diff touch the subject (`gh pr view
   <n> --json files`, `gh pr diff <n>`), since an open one rarely names a
   ticket it only touches; a branch counts only when no merged pull
   request has it as its head (`gh pr list --state merged --head
   <branch>` answers nothing), because a squash never marks a branch
   merged.
4. Give each ticket one verdict:
   - `done`: the code on `main` does what it asks; name the pull request
     and the file. A merged pull request that says it closes the ticket
     while the tracker left it open is `done`. A part the pull request
     left out on purpose, and said so, is a remainder the report proposes
     as a ticket of its own; the verdict stays `done`.
   - `moot`: its premise no longer holds (the component is gone, a
     decision replaced it); name what changed.
   - `partly done`: some of it landed; list what is left.
   - `still relevant`: the code still has the problem; name the file and
     line that shows it. A ticket waiting on a precondition by design is
     `still relevant`, with the precondition named.
   - `in flight`: an open pull request or a branch works on it now.
   - `out of scope`: a ticket filed in the scope whose subject is
     another repository or product.
   - `unsure`: the evidence does not decide it; say what would.
   A verdict without evidence is `unsure`.
5. Note what the triage found beside the verdicts: two tickets that are
   one, a ticket whose facts are wrong, a done ticket whose remainder
   needs a ticket of its own. Each is a proposal in the report.
6. With `--apply`, and only after the person says so in this session:
   comment the evidence on each `done` and `moot` ticket and move it to
   `Done` or `Canceled`; comment the remaining list on a `partly done`
   one and leave it open; comment a correction on a `still relevant` one
   whose facts were wrong, unless a comment already corrects them.
   Nothing else is written: no `in flight`, `out
   of scope`, or `unsure` ticket is touched, and no ticket is made.
7. Write the report.

## What it never does

- Never modifies a tracked file, never commits, never opens a pull
  request: it writes a report.
- Never closes a ticket without `--apply` and the person's word in this
  session, and never one whose verdict is not `done` or `moot`.
- Never deletes a ticket, never edits a ticket's title or text, never
  creates one.
- Never judges against a branch: `main` is what shipped.

## Output

`~/Downloads/acme_ticket_triage_<yyyy-mm-dd>.md`:

```markdown
# Acme ticket triage (<team>, <date>)

Scope: <what was read, and what was left out and why>. Main at <commit>.

| ticket | title | verdict | evidence | action taken (none, report only, without `--apply`) |
|---|---|---|---|---|

Counts: <n> done, <n> moot, <n> partly done, <n> still relevant, <n> in flight, <n> out of scope, <n> unsure; <n> triaged of <n> in scope.

## Proposals

- <duplicates, corrected facts, remainders that need a ticket>
```
