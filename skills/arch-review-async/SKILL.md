---
name: arch-review-async
description: "Review code or a change through the Async lenses of the Software Design and Architecture Guidelines. Covers Infrastructure, Worker Roles, The Network Layer (Idempotency on the Consumer Side, Long-Running Orchestrations): infra, queues, workers, park vs fail. Use for a change that touches this area, or as one leg of arch-review-full."
allowed-tools: Read, Grep, Glob, Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# arch-review-async

Judge the code from one perspective only: the lenses in
`${CLAUDE_SKILL_DIR}/../../lenses/async.md`. Other perspectives have
their own skills; do not borrow their rules, and do not flag anything
a lens in this file does not name. The guideline itself is at
`${CLAUDE_SKILL_DIR}/../../architecture.md` when a lens needs its
source read in full. If either file is missing, stop and say the
installation is incomplete.

## Input

`$ARGUMENTS` names what to review. Interpret it as follows, in order:

1. Empty: the current branch's changes against the repository's default
   branch (committed since the merge base, plus the working tree). The
   default branch is `origin/HEAD` when set, else `main`, else
   `master`. When the scope resolves to no files, report "nothing to
   review" in the report's Scope line and stop.
2. A path or glob: every file under it, as it is now. A path that does
   not exist is an error; say so and stop.
3. A git ref or range (`abc123`, `main..HEAD`): the diff of that range.
4. The word `all`: the whole repository. Expect this to take a while.

Read changed files in full, not only the changed lines. Rules break in
the interaction between the new code and its neighbors, so pull in the
interface a class implements, the root that wires it, and the callers
of a changed signature.

## Procedure

1. Read the lens file end to end before looking at any code.
2. Establish the scope and list the files in it.
3. Run the checker that shipped with this lens file, from the root of
   the repository under review:
   `python3 "${CLAUDE_SKILL_DIR}/../../checkers/arch_check.py" --group async --format json`.
   Exit 0 means no findings and exit 1 means findings; both are a
   run. Its findings on files outside the scope are dropped, and so is
   anything under `exceptions_applied`: a deviation the project
   recorded with an ADR, which is not a finding. The output's
   `rules_run` says which lenses it covered, the rules this guideline
   ships and the project's own alike, each with a coverage and a
   summary. A lens covered `full` is decided here: each of its
   findings in scope is a finding, with the checker's file and line,
   and no finding is a pass whose evidence is the checker, which read
   every file. A lens covered `partial` is decided in step 4, and the
   rule's summary says which part the checker holds: a checker finding
   in scope makes the lens a finding whatever the rest shows, and no
   checker finding leaves the rest to judge; the lens passes only when
   that rest passes too. A lens absent from `rules_run` is judged whole
   in step 4. When the checker cannot run (a Python older than 3.11,
   exit code 2, a project pinned to a newer Python than `python3`),
   say so in the report's Scope line and judge every lens in step 4,
   the ones it would have decided included. The review is the
   checker's fallback.
4. For every lens the checker did not decide, in id order, decide one
   of: **finding** (evidence of a breach, with a file and line),
   **pass** (the lens applies and the code satisfies it), **not
   applicable** (nothing in scope touches what the lens judges), or
   **unverified** (the lens applies, and what would decide it lies
   outside the scope and the neighbors the Input section says to read;
   name what would decide it). A partial lens whose judged part touches
   nothing in scope is not applicable, whatever the checker read. Keep
   the "Look for" and "Violation" text of the lens in front of you
   while deciding.
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
6. Assign severity from the lens, adjusted only downward when the
   breach is contained (a test double, a documented exception the
   guideline names, an ADR cited next to the code).
7. Write the report in the format below. Nothing else; no preamble.

Never edit, stage, or commit. This skill reads and reports.

## Output

```markdown
# Architecture review: Async

**Scope.** <what was reviewed, in one line>
**Lenses.** <n> applied, <p> passed, <f> findings, <u> unverified, <x> not applicable

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule, one sentence>. Fix: <one sentence>.

## Passed

<LENS-ID>, <LENS-ID> (`<path>`), ...

## Unverified

<LENS-ID> (<what would decide it, a few words>), ...

## Not applicable

<LENS-ID> (<why, a few words>), ...
```

Findings are ordered most severe first, then by file. When there are
no findings, the section reads `No findings.`; an empty Unverified
section reads `None.` A `high` lens in Passed names the file that
proved it. Every lens id in the lens file appears in exactly one of
the four sections. Applied is passed plus findings plus unverified,
so applied plus not applicable is the number of lenses in the file.
