---
name: arch-review-delivery
description: "Architecture review through the Delivery lenses: apps, deployment, repo layout, client architecture, logs and telemetry, conventions, substitutions. For a change in this area, or as one leg of arch-review-full."
allowed-tools: Read, Grep, Glob, Bash(git diff:*), Bash(git show:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*), Bash(git ls-files:*)
---

# arch-review-delivery

Judge the code from one perspective only: the lenses in
`../../lenses/delivery.md`. Other perspectives have their own skills; do
not borrow their rules, and do not flag anything a lens in this file
does not name. The guideline itself is at `../../architecture.md` when a
lens needs its source read in full. A path that starts with `../` is
read from this skill's folder as `realpath` resolves it. If either file
is missing, stop and say the installation is incomplete.

This pass covers Apps, Deployment, Monorepo Folder Structure, Client App Architecture, Telemetry, Cross-Cutting Conventions, Technology Choices: apps, deployment, repo layout, client architecture, logs and telemetry, conventions, substitutions.

## Input

The arguments name what to review. Read them as the first of these
that matches:

1. Empty: the current branch's change. The default branch is
   `origin/HEAD` when set, else `main`, else `master`. The scope is
   every file changed since the merge base of `HEAD` and the default
   branch (`git diff --name-only <base>`, committed and uncommitted
   alike), plus every untracked file git does not ignore
   (`git status --porcelain --untracked-files=all`). When there is no
   merge base (no default branch, a shallow clone, or unrelated
   histories), the scope is the uncommitted change against `HEAD` plus
   the untracked files, and the Scope line says the merge base was not
   found. In a repository with no commit, it is every file not
   ignored, listed as `all` lists them.
2. The word `all`: every file in the repository that git does not
   ignore, tracked or untracked
   (`git ls-files --cached --others --exclude-standard`). Expect this
   to take a while.
3. A range (`main..HEAD`, `main...HEAD`): the files that range
   changes, read as they are at its end.
4. A commit that `git rev-parse --verify --quiet "<arg>^{commit}"`
   resolves (a SHA, a tag, a branch): the change that commit made,
   against its first parent.
5. A path or a glob: every file under it as it is now, untracked files
   included
   (`git ls-files --cached --others --exclude-standard -- <path>`).
   A path that does not exist is an error; say so and stop.
   A name that is both a commit and a path reads as the commit; write
   `./<name>` for the path.

The empty scope, `all`, and a path read the working tree. A range and
a commit read history, which may not be checked out: read each file at
the range's end or the commit with `git show <ref>:<path>`, never from
the working tree. When the scope resolves to no files, report "nothing
to review" in the report's Scope line and stop. Run each git command
here with the root of the repository under review as the working
directory, never with git's `-C` option: the pre-approved commands
match only as written, so a `-C` makes a stricter host ask first.
Read the files by their absolute paths with Read and Grep.

Read changed files in full, not only the changed lines. Rules break in
the interaction between the new code and its neighbors, so pull in the
interface a class implements, the root that wires it, and the callers
of a changed signature.

## Procedure

1. Read the lens file end to end before looking at any code.
2. Establish the scope and list the files in it.
3. When the scope reads the working tree, run the checker that
   shipped with this lens file, `../../checkers/arch_check.py`, by its
   absolute path, and name the root of the repository under review,
   an absolute path too, with `--root`:
   `python3 <arch_check.py> --no-local --root <root> --group delivery --format json`.
   `--root` says which repository is read, whatever folder the shell
   is in, so no `cd` goes before the command.
   Run it alone, with no other command chained to it. This skill
   pre-approves no Python command, so the host may ask the person
   first, and a person who allowed this one command is asked about no
   other. Never leave `--no-local` out: without it the checker runs
   the project's own rules, which are files of the repository under
   review, and a review runs none of them.
   The checker reads the working tree only. For a range or a commit it
   is not run: say so in the Scope line and judge every lens in step 4.
   Otherwise read its output:
   - Exit 0 means no findings and exit 1 means findings. Both are a
     run.
   - Drop its findings on files outside the scope. An entry under
     `exceptions_applied` in scope is a deviation the project recorded
     with an ADR: a documented exception, never a finding. It goes on
     one line under Deviations.
   - A finding whose `group` is `framework` is about the checker's own
     input: `PARSE`, a file no rule could read, or `IGNORE`, an inline
     ignore that does not resolve. One in scope is a finding under its
     own id, at `high`. No lens passes on the checker's evidence for a
     file that does not parse.
   - `rules_run` says which lenses it covered, each with a coverage
     and a summary. They are the rules this guideline ships. The
     project's own rules did not run, so a lens only they decide is
     absent.
   - A lens covered `full` is decided here. Each of its findings in
     scope is a finding, with the checker's file and line. No finding
     is a pass whose evidence is the checker, which read every file.
   - A lens covered `partial` is decided in step 4, and the rule's
     summary says which part the checker holds. A checker finding in
     scope makes the lens a finding whatever the rest shows. With no
     checker finding, the rest is judged, and the lens passes only
     when that rest passes too.
   - An entry under a rule's `to_judge` is a place the checker names
     and leaves to step 4. Judge each one in scope there, whether or
     not the lens has a checker finding. The entry is no finding by
     itself: what it names is a finding when it meets the lens's
     Violation, and no breach when it does not, which the report says
     on a line under Passed. Drop an entry on a file outside the
     scope.
   - A lens absent from `rules_run` is judged whole in step 4.
   - When the checker cannot run (the command is refused, a Python
     older than 3.11, exit code 2, a project pinned to a newer Python
     than `python3`), say so in the report's Scope line and judge every
     lens in step 4, the ones it would have decided included. The
     review is the checker's fallback.
4. For every lens the checker did not decide, in id order, decide one
   of: **finding** (evidence of a breach, with a file and line),
   **pass** (the lens applies and the code satisfies it), **not
   applicable** (nothing in scope touches what the lens judges), or
   **unverified** (the lens applies, and what would decide it lies
   outside the scope and the neighbors the Input section says to read;
   name what would decide it). A partial lens whose judged part touches
   nothing in scope is not applicable, whatever the checker read. Keep
   the "Look for" and "Violation" text of the lens in front of you
   while deciding. When the lens has a `Shape` line, open the scaffold
   file or folder it names, `../../<path>`, and compare the code with
   it: it is the rule as code, so a difference shows where to look. The decision still rests on the lens's
   Violation, never on a difference alone.
5. Verify every finding against the real source: open the file, confirm
   the line, confirm the surrounding code does not already handle it.
   Drop a finding you cannot point at. Verify a **pass** on a `high`
   lens the same way: open the file that would breach it and name that
   file in the report; a high lens passes on evidence, never on the
   absence of a finding, and a high lens whose evidence is out of
   reach is unverified, never passed. A clean run of the checker is
   evidence for a lens it decides whole; name `arch-check` as the proof.
   For a partial high lens it is evidence for the checker's part only,
   and the rest needs a file of its own.
6. Assign severity from the lens. Lower it only when the breach is
   contained: in a test double, or under an exception the guideline
   itself names. A breach whose ADR quotes the rule and is cited next
   to the code is a documented exception, not a finding. It goes on
   one line under Deviations, and the lens is decided on the rest of
   the scope. An ADR never lowers a severity.
7. Write the report in the format below. Nothing else; no preamble.

Never edit, stage, or commit, and never run a file of the repository
under review. This skill reads and reports.

## Output

```markdown
# Architecture review: Delivery

**Scope.** <what was reviewed, in one line>
**Lenses.** <n> applied, <p> passed, <f> findings, <u> unverified, <x> not applicable

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule, one sentence>. Fix: <one sentence>.

## Deviations

- **<LENS-ID>** `<path>:<line>` ADR-NNNN <what the ADR accepts, a few words>.

## Passed

<LENS-ID>, <LENS-ID> (`<path>`), ...

- **<LENS-ID>** `<path>:<line>` <a place read for a judgment and judged no breach: what it is, and why>.

## Unverified

<LENS-ID> (<what would decide it, a few words>), ...

## Not applicable

<LENS-ID> (<why, a few words>), ...
```

Findings are ordered most severe first, then by file. When there are
no findings, the section reads `No findings.`; an empty Deviations or
Unverified section reads `None.` A `high` lens in Passed names the
file that proved it. Every `<path>` is relative to the root of the
repository under review, with forward slashes and no leading `./`.

The counts on the Lenses line count lenses, never lines. Findings has
one line per breach, so a lens with two breaches has two lines and
counts once in `<f>`. Every lens id in the lens file is decided once:
it counts in exactly one of Findings, Passed, Unverified, and Not
applicable. Deviations lines are not a decision and count nowhere.
Neither is the line under Passed for a place step 4 read for a
judgment and judged no breach: one the checker named under `to_judge`,
or one the lens has a review find where the checker did not run. It is
written whether its lens passes or has a finding, below the lens ids,
and there is no such line when there is no such place.
Applied is passed plus findings plus unverified, so applied plus not
applicable is the number of lenses in the file.
