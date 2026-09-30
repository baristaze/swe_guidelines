---
name: arch-review-full
description: "Full architecture review: the eight lens groups of the guideline run in parallel and merge into one report. Use before a pull request, or when a change crosses layers."
allowed-tools: Read, Grep, Glob, Agent, Bash(python3 */checkers/arch_check.py --no-local *), Bash(git diff:*), Bash(git show:*), Bash(git log:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git merge-base:*), Bash(git symbolic-ref:*)
---

# arch-review-full

Run every lens group over the same scope, in parallel, and merge the
eight reports into one. Each group is judged by its own reviewer so
that no perspective is diluted by another; this skill only fans out,
collects, and merges.

## Input

The arguments name what to review, exactly as `arch-review-<group>`
reads them (see `../arch-review-om/SKILL.md`, Input; a path that starts
with `../` is read from this skill's folder as `realpath` resolves it).
Resolve the scope once, here, into a concrete description (the list of
files, or the range or commit) and hand the same description to every
reviewer so the eight reports cover the same ground. A range or a commit
is handed over as the ref, with its list of files, and the reviewer
reads each file at that ref, never from the working tree. An empty scope
is reported as "nothing to review" and the skill stops. `all` costs
eight full reads of the repository, one per reviewer, and on a large
tree takes minutes and a large share of each reviewer's context; a path
or a range is the cheaper question whenever the change is narrower than
the tree.

## Procedure

1. Resolve the scope and write it down in one line.
2. Resolve this skill's folder with `realpath`, and the paths from it to
   absolute ones: the lens catalog is `../../lenses/` and the guideline
   is `../../architecture.md`. Reviewers do not see this skill's text,
   so pass them absolute paths.
3. When the scope reads the working tree (empty, `all`, or a path),
   run the checker once, for every group, from the root of the
   repository under review:
   `python3 <arch_check.py> --no-local --format json`, where
   `<arch_check.py>` is the absolute path of
   `../../checkers/arch_check.py`. Never leave `--no-local` out:
   without it the checker runs the project's own rules, which are files
   of the repository under review, and a review runs none of them.
   The checker reads the working tree only, so for a range or a commit
   it is not run: note that, and every reviewer judges every lens of
   its group. Otherwise keep its output. Each reviewer gets the part of it that belongs to
   its group (the rules run, the findings, and the `exceptions_applied`
   entries whose rule is one of its lenses), so no reviewer runs it
   again. The findings whose `group` is
   `framework` (`PARSE`, `IGNORE`) go to every reviewer, because a file
   that does not parse was read by no rule of any group; the merge
   keeps one copy of each. When the checker cannot run,
   note why; every reviewer then judges every lens of its group.
4. Where the agent can start subagents, launch eight reviewers at once,
   one per group, each with the scope line, the group name, the absolute
   path of its lens file, the absolute path of the guideline, and its
   part of the checker's output (or the note that it did not run). Use
   the `arch-reviewer` agent that `../../agents/arch-reviewer.md`
   defines for Claude Code (`swe-guidelines:arch-reviewer` when
   installed as the plugin). When no such agent is installed, give a
   general subagent the text of `../arch-review-<group>/SKILL.md` with
   every path in it that starts with `../` made absolute from that
   skill's folder as `realpath` resolves it, first, since the subagent
   reads it from elsewhere. Tell that subagent to skip the procedure's
   checker step and to use the part of the output passed to it instead,
   so the checker runs once. Where the agent has no subagents, run the
   eight group procedures one after another in this session, each on its
   part of the same output. The groups:
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
   - Concatenate every group's Deviations lines under Deviations, in
     lens id order, or `None.` when there are none. They are not
     findings and count nowhere.
   - Count applied, passed, findings, unverified, and not-applicable
     lenses across groups. Applied is passed plus findings plus
     unverified; applied plus not applicable is the size of the
     catalog, so every lens is counted once.
7. Write the merged report below. Then, if the report has three or
   more `high` findings, say so in one sentence after the report,
   with the count. Nothing else.

Never edit, stage, or commit, and never run a file of the repository
under review. This skill reads and reports.

## Output

The group report shape, plus a `Groups` line and a per-group table:

```markdown
# Architecture review

**Scope.** <the scope line>
**Groups.** om, contracts, context, storage, async, network, delivery, ops
**Lenses.** <n> applied, <p> passed, <f> findings, <u> unverified, <x> not applicable

## Findings

- **<LENS-ID>[, <LENS-ID>] <severity>** `<path>:<line>` <what breaks the rule>. Fix: <one sentence>.

## Deviations

- **<LENS-ID>** `<path>:<line>` ADR-NNNN <what the ADR accepts, a few words>.

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
