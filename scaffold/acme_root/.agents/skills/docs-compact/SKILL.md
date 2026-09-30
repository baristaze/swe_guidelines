---
name: docs-compact
description: "Compact the tree's documents to what holds at the head of the main branch: rewrite each ADR to its present decision with a status of one date, remove the ADRs that constrain nothing, cut the changelog to the latest release, clean the rows of specs/architecture.md, and rewrite the comments that tell what the code did before. With --migrations, fold each role's migration chain into one revision under the head's revision id, shown equal by a schema dump. Works on a branch, pushes nothing, and reports what it removed, rewrote, and kept."
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(git status:*), Bash(git fetch:*), Bash(git symbolic-ref:*), Bash(git switch:*), Bash(git log:*), Bash(git diff:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git ls-files:*), Bash(git rm:*), Bash(git mv:*), Bash(git add:*), Bash(git commit:*), Bash(git restore:*), Bash(git clean:*), Bash(git grep:*), Bash(diff:*), Bash(mkdir:*), Bash(gh release:*), Bash(gh run:*), Bash(gh api:*), Bash(docker info:*), Bash(docker compose:*), Bash(uv run:*), Bash(make setup), Bash(make check), Bash(make infra-up), Bash(make migrate), Bash(make migrate-check), Bash(make test-integration)
---

# docs-compact

A document an agent reads says what holds at the head of the main
branch, in the present tense: a README, an ADR, a spec, a comment. What
was stays in git, the pull request, and the release notes (the
guideline's Documentation as Code). An agent acts on what it reads, so
text that also carries what was makes it sort the adopted from the
rejected before it acts. This skill cuts the tree's documents back to
what holds.

One test decides a rejected option. Could a reader at the head reach it
without knowing the past? A simpler design, a library's default, the
obvious other way: yes, so it stays as one sentence in the Decision, as
the rule's near miss with its reason ("a lock bound, never a statement
deadline: a long backfill would fail for running"). A former value, or
an option only the history explains: no, so it goes.

## Input

`[--migrations]`

Without the flag the skill rewrites text only, and never opens
`om/migrations/`: a search lists lines of it, as step 6's does, and no
file there is read whole or changed. With it, it also folds each role's
migration chain into one revision, as step 7 says. It runs at the root
of the checkout.

## Role and credential

None of the platform's: it reads no environment, and logs in to no
database but the two the fold makes on the local stack and drops. It
reads the repository host through `gh`, under the person's own sign-in:
each release's notes, and for the fold each environment's last deploy.

## One-time preparations

Each is a person's to do, once. A part whose preparation is missing is
left as it is, and the report names it under Kept.

- **The changelog.** Every release the changelog lists below its latest
  has its notes on the repository host (`gh release view v<x.y.z>` exits
  0). A release with none keeps its section. The skill saves that
  section as `release-v<x.y.z>.md` in the evidence folder, and the
  report gives the command that publishes it: `gh release create
  v<x.y.z> --verify-tag --notes-file <that file>`.
- **The fold.** Every environment has deployed the commit that holds the
  newest migration. The local stack is up (`make infra-up`), with `make
  migrate` run once.

## Procedure

1. **Start clean, on a branch.** `git status --porcelain` prints
   nothing, or refuse. `git fetch origin`. The main branch is the one
   `git symbolic-ref --short refs/remotes/origin/HEAD` names. On it, cut
   the work branch, named for the day: `git switch --no-track -c
   docs-compact-<yyyy-mm-dd> origin/<main>`. The name is taken when
   `git rev-parse --verify --quiet <name>` or `git rev-parse --verify
   --quiet origin/<name>` prints a commit: add a suffix then (`-2`). On
   another branch, work there only when `git log
   --oneline origin/<main>..HEAD` prints nothing; otherwise refuse.
   Make the evidence folder, `mkdir -p
   ~/Downloads/acme_docs_compact_<yyyy-mm-dd>/`.
2. **Take the inventory**, and keep it for the report. Read nothing
   under `node_modules`, a lock file, a generated file
   (`clients/typescript/openapi.json` and the two schemas `make openapi`
   writes), or a skill folder that is a link.
   - Every `docs/adr/NNNN-*.md`: its status line, its `##` headings, and
     who cites its number (`git grep -n "NNNN"`: the code, the config,
     and the Markdown).
   - `CHANGELOG.md`: its release sections, when the tree has one.
   - The Substitutions and Deviations rows of `specs/architecture.md`.
   - The sentences that tell a past, in the Markdown and in the code's
     comments and docstrings. `references/phrases.txt`, in this skill's
     folder, lists the phrases that mark one, a pattern a line, each
     matched as whole words:

     ```bash
     git grep -nIiwE -f .agents/skills/docs-compact/references/phrases.txt -- . ':!.agents/skills/docs-compact'
     ```

     The list is the sweep: the skill reads each hit in its paragraph,
     and does not read every file.
3. **Compact the ADRs**, each once, then commit them alone.
   - **Rewrite in place** an ADR whose decision holds. Its status is
     `**Status**: accepted (<date>)` and nothing more, with the first
     date the old status names, the day it was accepted. Every later
     date in it goes. It keeps Context, Decision, and Consequences, in
     that order, and any section that states a present fact, such as a
     measurement. A section named for a past (Amended, Closed,
     Alternatives, History) goes: what still holds in it moves into the
     Decision or the Consequences, and a rejected option goes through
     the test above. The text states the decision as it stands, with no
     "since", "no longer", or former value, and names no ticket, pull
     request, or person.
   - **Remove** an ADR that constrains nothing at the head: one replaced
     whole, one that is closed, one that records what a release asked or
     that a pin moved. A decision in it that still stands moves first,
     into the ADR that records that decision. When no ADR does, the
     decision most cited stays in this file, rewritten, under its
     number. The file then takes that decision's name (`git mv`, to
     `NNNN-<the decision, as a slug>.md`), as any rewritten ADR does
     whose name says another subject: the number stays, and every link
     and every `adr =` path follows. Each other one gets a new ADR of
     its own, numbered in turn: the first one above the highest the tree
     held when the run began, the next one above that. A citation of the
     kept number that meant a moved decision names the ADR that now
     holds it.
   - **Fold** a substitution that has an ADR of its own into the one ADR
     that lists the substitutions, as one row and the sentences that row
     needs, and remove the ADR.
   - **Re-point every citation** of a removed number before its file
     goes (`git rm`): a link in Markdown, an `adr =` entry in
     `pyproject.toml`, an `ADR NNNN` in a comment. Each names the ADR
     that now holds the decision, or goes when what it explained is
     gone. `arch-check` fails on a number that code cites and no file
     has.
   - An ADR that only restates a rule of the guideline, and decides
     nothing of the tree's own, constrains nothing. It is removed when
     no code or config cites its number, its links in Markdown going
     with it. While one does, it is kept, and listed. An ADR the test
     leaves in doubt is kept and listed too.
   - A file under `docs/adr/` that is not an ADR, such as a picture,
     goes when no ADR links it. An ADR never goes for want of a link.
4. **Cut the changelog** to its latest release: the lines above the
   first release, that one section, and one line that says every
   release's notes stay on the repository host. Each older section goes
   when its preparation holds. Commit.
5. **Clean the rows** of `specs/architecture.md`: each says what the
   substitution or the deviation is, in the present tense, with its end
   condition when it has one. A date, a release, and "ended with" go. A
   row whose ADR step 3 removed goes, or names the ADR that holds it
   now. The pin line stays as it is. Commit.
6. **Rewrite the sentences** of step 2's sweep, each hit judged once.
   - A sentence about the code's past goes, or is rewritten to what the
     code does: what a former release did, what changed, what a value
     once held.
   - A sentence about a running system stays: "raises `LeaseLost` when
     the item is no longer this worker's" is state, not history. So does
     a rule's near miss, and a lens's or a skill's own words for a
     breach.
   - A comment on code that serves the release before, in an expand and
     contract still in flight, stays, in the present tense: what the
     code tolerates, and what ends it. The test reads the tree alone,
     and counts no release and no deploy. Name the piece the contract
     keeps for the release before. While the head holds it, the step
     that ends the contract has not landed, and it is in flight.
     - A piece of the schema (a column, a default, a constraint, a
       trigger, a function): the head holds it when an up file of the
       chain makes it and no file with a later stamp drops it. `git
       grep -nwF '<its name>' -- 'om/migrations/sql/*.up.sql'` lists
       the lines that say so.
     - Any other piece (a field of a response, a setting, a branch of
       the code) is the code the comment sits on: the head holds it.

     In doubt, it is in flight.
   - Code whose piece a later file dropped serves a release long gone.
     It is listed in the report and left as it is, its comment with it:
     the comment says why the code is there, and whoever removes the
     code removes both. Its ADR is rewritten as any other is, and what
     it says of the contract's steps goes, as a past.
   - Only text changes: a comment, a docstring, a document. A name in
     code is code.
   Commit.
7. **With `--migrations`, fold each chain.** Read `references/fold.md`
   first, and follow it: the precondition and how each part of it is
   read, the two dumps that prove the fold, what else a fold moves, and
   its commit. When the precondition does not hold, the chain stays as
   it is and the report says which part failed.
8. **Run the gates.** `make setup` first, which installs the tree and
   formats its Python; commit what it changes, apart. Then `make
   check`. When `docker info` exits 0, also
   `make infra-up`, `make migrate`, `make migrate-check`, and `make
   test-integration`; otherwise the report names each one skipped. A
   gate that fails is fixed in a commit of its own, and the gate runs
   again from its first command: the first run plus at most 3 reruns,
   then stop and say which gate fails and why.
9. **Write the report.**

## Bounds

- One pass: each document is rewritten once in a run. What a second
  reading would change is a second run's.
- The gates, and the fold's proof, each take the first run plus at most
  3 reruns.
- Its Next is the person's to run, never the session's: it pushes
  nothing.

## What it never does

- Never drops a deviation's end condition: "until an operator task
  needs a screen" is a trigger, not history.
- Never trims an expand and contract in flight, as step 6 tells one:
  its comments, the column, the default, or the trigger it keeps stay
  until the contract step lands. Its ADR is rewritten as any other is,
  to the present tense and one date, and keeps every fact of the
  contract: what the release before still reads or writes, the piece
  that serves it, and the step that ends it.
- Never renumbers an ADR, and never gives a removed ADR's number to
  another: a removed ADR leaves a gap.
- Never drops a reason that stops a plausible wrong change: it stays as
  a near miss.
- Never changes a statement of code. A test changes only with
  `--migrations`, and only one that pins a revision the fold removes,
  tests a backfill it removes, or steps down from the head.
- Never opens `om/migrations/` without `--migrations`, and never edits
  an applied migration file: a fold replaces a chain whole.
- Never writes to a shared database or to an environment: it logs in
  only to the two databases it made on the local stack, and drops them.
- Never pushes, never opens a pull request, never publishes or edits a
  release. It moves no pin.

## Output

The work branch, one commit per category, and
`~/Downloads/acme_docs_compact_<yyyy-mm-dd>.md`:

```markdown
# Acme: documents compacted to what holds

Branch <name> at <commit>, cut from <main> at <commit>. Migrations: <folded | not asked | kept: which part of the precondition failed>.

| category | removed | rewritten | kept |
|---|---|---|---|
| ADRs | | | |
| Changelog | | | |
| Table rows | | | |
| Comments and documents | | | |
| Migrations | | | |

## Removed

- <ADR NNNN, or a section, a row, a file>: <what held it, and where its live part went>

## Rewritten

- <file>: <what it says now, in a line>

## Kept

- <what, and why: a trigger, an expand and contract in flight, a near miss, a preparation still missing, a doubt, with the question>

## Code that serves a release long gone

- <file:line>: <what it does, and what would remove it>

## The fold (with `--migrations`)

| role | head id | revisions folded | backfills left out | dump of the chain against the fold's |
|---|---|---|---|---|

The last commit before the fold: <sha>. A snapshot older than the fold is restored at that commit's release, which migrates it to the head, and then moves to the current one.

## Gates

- <gate>: <result, or skipped and why>

## Next

Push the branch and open the pull request: a person's to run, never the session's.
```
