---
name: arch-review-full
description: "Full architecture review of code or a change against every lens group of the Software Design and Architecture Guidelines: eight parallel reviews (om, contracts, context, storage, async, network, delivery, ops) merged into one report. Use before a pull request or when a change crosses layers."
allowed-tools: Read, Grep, Glob, Agent, Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# arch-review-full

Run every lens group over the same scope, in parallel, and merge the
eight reports into one. Each group is judged by its own reviewer so
that no perspective is diluted by another; this skill only fans out,
collects, and merges.

## Input

`$ARGUMENTS` names what to review, exactly as `arch-review-<group>`
reads it (see `${CLAUDE_SKILL_DIR}/../arch-review-om/SKILL.md`, Input).
Resolve it once, here, into a concrete description (the list of files,
or the range) and hand the same description to every reviewer so the
eight reports cover the same ground. An empty scope is reported as
"nothing to review" and the skill stops. `all` costs eight full reads
of the repository, one per reviewer, and on a large tree takes minutes
and a large share of each reviewer's context; a path or a range is the
cheaper question whenever the change is narrower than the tree.

## Procedure

1. Resolve the scope and write it down in one line.
2. Resolve the paths. `${CLAUDE_SKILL_DIR}` is already an absolute
   path; the lens catalog is `${CLAUDE_SKILL_DIR}/../../lenses/` and
   the guideline is `${CLAUDE_SKILL_DIR}/../../architecture.md`.
   Reviewers do not see this skill's text, so pass them absolute paths.
3. Run the checker once, for every group, from the root of the
   repository under review:
   `python3 "${CLAUDE_SKILL_DIR}/../../checkers/arch_check.py" --format json`.
   Keep its output. Each reviewer gets the part of it that belongs to
   its group (the rules run and the findings whose `group` is its
   own), so no reviewer runs it again. When the checker cannot run,
   note why; every reviewer then judges every lens of its group.
4. Launch eight reviewers at once, one per group, each with the scope
   line, the group name, the absolute path of its lens file, the
   absolute path of the guideline, and its part of the checker's
   output (or the note that it did not run). Use the `arch-reviewer` agent
   (`swe-guidelines:arch-reviewer` when installed as the plugin). When
   no such agent exists, use a general-purpose agent and give it the
   text of `${CLAUDE_SKILL_DIR}/../arch-review-<group>/SKILL.md` with
   every `${CLAUDE_SKILL_DIR}` in it substituted by the absolute path
   first, since the agent has no such variable. When
   subagents are not available at all, run the eight group procedures
   one after another in this session. The groups:
   - `arch-review-om`
   - `arch-review-contracts`
   - `arch-review-context`
   - `arch-review-storage`
   - `arch-review-async`
   - `arch-review-network`
   - `arch-review-delivery`
   - `arch-review-ops`
5. Wait for all eight. A reviewer that fails, or returns a report that
   does not follow the group format, is re-run once; if it fails again,
   its group is reported as "not reviewed" with the error.
6. Merge:
   - Concatenate all findings and sort by severity (high, medium, low),
     then by file and line.
   - When two groups flag the same `path:line`, keep both lens ids on
     one line; the fix text comes from the higher-severity one. At
     equal severity the group whose header partition (the opening
     paragraphs of its lens file) owns the rule wins the fix text, and
     the other id stays on the line.
   - Two findings whose fix names the same symbol (the same class,
     method, or setting) merge into one line the same way, whatever
     their `path:line`; the line named is the higher-severity one's.
   - Count applied, passed, findings, unverified, and not-applicable
     lenses across groups. Applied is passed plus findings plus
     unverified; applied plus not applicable is the size of the
     catalog, so every lens is counted once.
7. Write the merged report below. Then, if the report has three or
   more `high` findings, say so in one sentence after the report,
   with the count. Nothing else.

Never edit, stage, or commit. This skill reads and reports.

## Output

The group report shape, plus a `Groups` line and a per-group table:

```markdown
# Architecture review

**Scope.** <the scope line>
**Groups.** om, contracts, context, storage, async, network, delivery, ops
**Lenses.** <n> applied, <p> passed, <f> findings, <u> unverified, <x> not applicable

## Findings

- **<LENS-ID>[, <LENS-ID>] <severity>** `<path>:<line>` <what breaks the rule>. Fix: <one sentence>.

## By group

| Group     | Applied | Passed | Findings | Unverified | Not applicable |
|-----------|---------|--------|----------|------------|----------------|
| om        |         |        |          |            |                |
| contracts |         |        |          |            |                |
| context   |         |        |          |            |                |
| storage   |         |        |          |            |                |
| async     |         |        |          |            |                |
| network   |         |        |          |            |                |
| delivery  |         |        |          |            |                |
| ops       |         |        |          |            |                |

## Passed

<LENS-ID>, <LENS-ID> (`<path>`), ... (all groups, in id order; a `high` lens names the file that proved it)

## Unverified

<LENS-ID> (<what would decide it>), ...

## Not applicable

<LENS-ID> (<why>), ...
```
