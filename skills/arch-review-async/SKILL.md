---
name: arch-review-async
description: "Review code or a change through the Async lenses of the Software Design and Architecture Guidelines. Covers Infrastructure, Worker Roles, The Network Layer (Idempotency on the Consumer Side, Long-Running Orchestrations): infra, queues, workers, park vs fail. Use for a change that touches this area, or as one leg of arch-review-full."
allowed-tools: Read, Grep, Glob, Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
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
3. For every lens, in id order, decide one of: **finding** (evidence of
   a breach, with a file and line), **pass** (the lens applies and the
   code satisfies it), **not applicable** (nothing in scope touches
   what the lens judges), or **unverified** (the lens applies, and
   what would decide it lies outside the scope and the files step 2
   pulled in; name what would decide it). Keep the "Look for" and
   "Violation" text of the lens in front of you while deciding.
4. Verify every finding against the real source: open the file, confirm
   the line, confirm the surrounding code does not already handle it.
   Drop a finding you cannot point at. Verify a **pass** on a `high`
   lens the same way: open the file that would breach it and name that
   file in the report; a high lens passes on evidence, never on the
   absence of a finding, and a high lens whose evidence is out of
   reach is unverified, never passed.
5. Assign severity from the lens, adjusted only downward when the
   breach is contained (a test double, a documented exception the
   guideline names, an ADR cited next to the code).
6. Write the report in the format below. Nothing else; no preamble.

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
