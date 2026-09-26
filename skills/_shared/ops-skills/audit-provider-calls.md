---
name: audit-provider-calls
description: "Audit the calls each flow makes to external providers: which provider, how many times, whether a call repeats within the flow, whether calls are independent of each other, whether they sit in the request path, whether the client is reused, the timeout times the retries, and the request's own deadline. Findings are ranked remove, fold, defer, cache, and parallelize last. Read from the code and the settings of the checkout. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(git:*), Bash(mkdir:*)
---

# audit-provider-calls

For every route and every worker flow: which external providers it
calls, how often, and what those calls cost the flow in time and in
failure. The answer comes from the code and the settings, so it is what
the code does, not what it looks like it does.

## Input

`[--focus <provider,...>]`

`--focus` narrows the audit to some providers; every provider the
settings name by default. The audit reads the checkout; to audit
another commit, run it from a checkout of that commit. Every command
runs from the repository root.

## Role and credential

None, local only. The skill reads the checkout and nothing else: it
holds no cloud credential, calls no provider, and reads no environment.

## Procedure

1. Make the evidence folder
   `~/Downloads/acme_provider_calls_<yyyy-mm-dd>/`, today's date in UTC,
   and say which commit the report read.
2. List the providers: every outbound client the settings configure and
   the twin that stands in for each. Then list every route, every worker
   handler, and the sweep's steps, and for each the provider calls it
   reaches, through the manager and the client.
3. For each flow and each provider call:
   - how many times it runs, at least and at most, and what grows it;
   - whether it repeats within the flow with the same inputs;
   - whether it depends on another call's answer or is independent;
   - whether it sits in the request path or runs later, on a work item;
   - whether the client is built once and reused, or built per call;
   - the timeout and the retries from the settings, and their product,
     the longest the call can take;
   - the request's own deadline, and whether the sum of the flow's
     worst cases fits inside it.
4. Rank each fix in this order, and propose the first that applies:
   remove the call; fold it into another; defer it off the request path
   onto a work item; cache its answer; and only then run independent
   calls in parallel. Parallel calls still spend the provider's rate
   limit and the flow's deadline, so parallel is the last resort.
5. Write the report, with the proposed tickets in it. The skill files
   none.

## What it never does

- Never calls a real provider, runs the platform, or reads an
  environment: the answer is read from the checkout.
- Never modifies a tracked file, never commits, never opens a pull
  request: a fix is a proposal with its worst case before and after.
- Never gives a count or a time it did not trace to the code or a
  setting as traced: a figure it inferred is marked as one.

## Output

`~/Downloads/acme_provider_calls_<yyyy-mm-dd>.md`:

```markdown
# Acme: external provider calls per flow

<commit>, read from the code and the settings' defaults.

## Short answer

## Per flow

| Flow | Provider | Calls (min, max) | Repeats | Independent | Request path | Client reused | Timeout x retries | Deadline | Verdict |

## Findings, by impact

1. Finding. Where. The cost. The fix (remove, fold, defer, cache, parallelize). Effort S/M/L. Proposed ticket.

## What I could not verify
```
