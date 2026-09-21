---
name: arch-deviate
description: "Record a deliberate deviation from the Software Design and Architecture Guidelines as an architecture decision record (ADR) in the current repository, quoting the rule, stating the decision, and naming the consequences. Use when a review finding is accepted as intentional or when a project needs to diverge from a section."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(date:*)
---

# arch-deviate

A project that follows the guideline may still need to diverge from a
rule. The divergence is recorded, not argued about in review threads:
one ADR quotes the rule, states the decision, and names what the
project accepts in exchange. Reviews then treat the deviation as a
documented exception when the ADR is cited next to the code.

## Input

`$ARGUMENTS` names the rule being deviated from, as a lens id
(`STO-02`), a section by title (`The Storage Layer, Storage
Principles`), or a sentence describing it, optionally followed by a
one-line reason. A technology substitution (an equivalent in place of
a technology the guideline names) is not a deviation and is recorded
in the project's technology-choices ADR instead, as the guideline's
"Technology Choices and How to Override Them" section states; when
`$ARGUMENTS` describes one, say so and stop. Ask in one
message for what is missing: the reason, the scope of the deviation
(which namespace, service, or table), and whether it is permanent or
has a condition for ending.

## Procedure

1. Resolve the rule: find the lens in `${CLAUDE_SKILL_DIR}/../../lenses/`
   and the section in `${CLAUDE_SKILL_DIR}/../../architecture.md`. Quote
   the principle verbatim.
2. Find the project's ADR folder: `docs/adr/` when it exists; otherwise
   ask where ADRs live and write nothing until answered. Number the new
   record as one more than the highest numeric prefix present
   (`NNNN-<slug>.md`). Refuse to write a path that already exists.
3. Take the date from `date +%F`: the ADR records the day the decision
   is made, which is today, not the day of the last commit.
4. Write the ADR with the template below, under the ADR folder only.
   Keep it under one page.
5. When `specs/architecture.md` exists and has a `## Deviations` table,
   append one row: the ADR number, the rule, and a one-line summary.
6. When the lens has a `Check` line naming `arch-check`, the ADR alone
   does not pass the gate: the checker still fails on the code. Give
   it the entry that names the ADR, in the shape `checkers/README.md`
   in this plugin shows (`${CLAUDE_SKILL_DIR}/../../checkers/README.md`,
   Exceptions); the rule id is the lens id. A whole rule turned off
   is a `[[tool.arch-check.disable]]` entry with `rule`, `adr` (the
   ADR's path), and `reason`. A rule
   broken in some files is a `[[tool.arch-check.exception]]` entry
   with `rule`, `path` (a glob), `adr`, and `reason`. One line is the
   inline comment `# arch-check: ignore[<LENS-ID>] ADR-NNNN` at the end
   of that line. Append a table entry to the root `pyproject.toml` when
   it has a `[tool.arch-check]` table, and print it otherwise. Print
   the inline comment and never place it: which line it goes on is the
   person's call.
7. Whatever the lens, tell the person to cite `ADR-NNNN` in a comment
   beside the code that deviates. A review treats the code as an
   exception only when the ADR is cited there.

Do not commit. Do not edit the guideline or the lenses; a deviation
belongs to the project, not to the rule. Edit nothing but the ADR
folder, the deviations table, and the `[tool.arch-check]` entry.

## Output

The path of the new ADR, the row appended to the deviations table (or
"no deviations table"), the `arch-check` entry written or printed (or
"judged by review only" when the lens has no `Check` line), the
reminder to cite `ADR-NNNN` beside the code, and the one-line summary
for the reviewer. Nothing else.

## ADR template

```markdown
# ADR NNNN: <title that names the deviation>

**Status**: accepted (<date>)

## Context

<The rule: lens id, section, and the principle quoted verbatim. Why it
does not fit here. Facts, not preferences.>

## Decision

<What the project does instead, in the present tense. Where it applies.
Whether it is permanent or ends when a named condition holds.>

## Consequences

<What the project accepts: the guarantee it gives up, the test or check
that stands in for it, the reviews that must treat the cited code as an
exception.>
```
