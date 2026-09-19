---
name: arch-review-full
description: "Full architecture review of code or a change against every lens group of the Software Design and Architecture Guidelines: seven parallel reviews (om, contracts, context, storage, async, network, delivery) merged into one report. Use before a pull request or when a change crosses layers."
allowed-tools: Read, Grep, Glob, Agent, Bash(git diff:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# arch-review-full

Run every lens group over the same scope, in parallel, and merge the
seven reports into one. Each group is judged by its own reviewer so
that no perspective is diluted by another; this skill only fans out,
collects, and merges.

## Input

`$ARGUMENTS` names what to review, exactly as `arch-review-<group>`
reads it (see `${CLAUDE_SKILL_DIR}/../arch-review-om/SKILL.md`, Input).
Resolve it once, here, into a concrete description (the list of files,
or the range) and hand the same description to every reviewer so the
seven reports cover the same ground. An empty scope is reported as
"nothing to review" and the skill stops.

## Procedure

1. Resolve the scope and write it down in one line.
2. Resolve the paths. `${CLAUDE_SKILL_DIR}` is already an absolute
   path; the lens catalog is `${CLAUDE_SKILL_DIR}/../../lenses/` and
   the guideline is `${CLAUDE_SKILL_DIR}/../../architecture.md`.
   Reviewers do not see this skill's text, so pass them absolute paths.
3. Launch seven reviewers at once, one per group, each with the scope
   line, the group name, the absolute path of its lens file, and the
   absolute path of the guideline. Use the `arch-reviewer` agent
   (`swe-guidelines:arch-reviewer` when installed as the plugin). When
   no such agent exists, use a general-purpose agent and give it the
   text of `${CLAUDE_SKILL_DIR}/../arch-review-<group>/SKILL.md`. When
   subagents are not available at all, run the seven group procedures
   one after another in this session. The groups:
   - `arch-review-om`
   - `arch-review-contracts`
   - `arch-review-context`
   - `arch-review-storage`
   - `arch-review-async`
   - `arch-review-network`
   - `arch-review-delivery`
4. Wait for all seven. A reviewer that fails, or returns a report that
   does not follow the group format, is re-run once; if it fails again,
   its group is reported as "not reviewed" with the error.
5. Merge:
   - Concatenate all findings and sort by severity (high, medium, low),
     then by file and line.
   - When two groups flag the same `path:line`, keep both lens ids on
     one line; the fix text comes from the higher-severity one.
   - Count applied, passed, findings, unverified, and not-applicable
     lenses across groups.
6. Write the merged report below. Then, if the report has three or
   more `high` findings, say so in one sentence after the report,
   with the count. Nothing else.

Never edit, stage, or commit. This skill reads and reports.

## Output

The group report shape, plus a `Groups` line and a per-group table:

```markdown
# Architecture review

**Scope.** <the scope line>
**Groups.** om, contracts, context, storage, async, network, delivery
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

## Passed

<LENS-ID>, <LENS-ID> (`<path>`), ... (all groups, in id order; a `high` lens names the file that proved it)

## Unverified

<LENS-ID> (<what would decide it>), ...

## Not applicable

<LENS-ID> (<why>), ...
```
