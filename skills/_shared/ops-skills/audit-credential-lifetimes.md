---
name: audit-credential-lifetimes
description: "Audit how long a credential keeps working after it should not: for every credential kind on every channel it opens (a request, a socket, a worker item, the operator plane), where it is checked, how often, what a check costs, the worst case a revoked credential keeps working, how a role change reaches it, its rate limit, and whether its check fails open or closed. Read from the code and the settings of the checkout. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(git:*), Bash(mkdir:*)
---

# audit-credential-lifetimes

For every credential and every channel it opens: how long a credential
that was revoked, expired, or demoted keeps working there, and why. The
answer comes from the code and the settings, so it is what the code
does, not what the design meant.

## Input

`[--focus <kind,...>]`

`--focus` narrows the audit to some credential kinds, the members of
`CredentialKind`; every kind by default. The audit reads the checkout;
to audit another commit, run it from a checkout of that commit. Every
command runs from the repository root.

## Role and credential

None, local only. The skill reads the checkout and nothing else: it
holds no cloud credential, logs in to no database, and reads no
environment.

## Procedure

1. Make the evidence folder
   `~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>/`, today's date in
   UTC, and say which commit the report read.
2. List the credential kinds (`CredentialKind` in
   `om/src/acme/om/opcontext.py`) and the channels each can open: a
   REST request, a realtime socket, a work item that runs later on the
   enqueuer's authority, and the operator plane. A pair that cannot
   occur is listed as such, with the line that refuses it.
3. For each pair, from the gateway, the tenancy manager, the socket
   handler, and the worker's claim, and from the settings with their
   defaults:
   - where the credential is checked, by file and function;
   - how often: every request, once per socket with a recheck interval,
     once per claim, or once at issue;
   - what a check costs: the reads it makes, and whether it writes
     (`last_seen_at` moved, and at most how often);
   - the worst case: the longest a revoked credential keeps working on
     that channel, as a formula over the settings and its value at the
     defaults (the expiry, the recheck interval, a lease, a cache TTL);
   - how a role change reaches it: on the next request, by push, by
     recheck, or not until the credential ends;
   - its rate limit, the key the limiter counts on, and the setting;
   - whether the check fails open or closed when its store is down, and
     where that is declared.
4. Mark each worst case that rests on a push alone: the bus delivers at
   most once, so a pushed revocation with no pull behind it is bounded
   only by the credential's expiry.
5. Write the report, with the proposed tickets in it. The skill files
   none.

## What it never does

- Never runs the platform, logs in to a database, or reads an
  environment: the answer is read from the checkout.
- Never modifies a tracked file, never commits, never opens a pull
  request: a fix is a proposal with its bound.
- Never gives a bound it did not trace to a setting or a line as
  traced: a bound it inferred is marked as one.

## Output

`~/Downloads/acme_credential_lifetimes_<yyyy-mm-dd>.md`:

```markdown
# Acme: credential lifetimes per channel

<commit>, read from the code and the settings' defaults.

## Short answer

## Per credential and channel

| Credential | Channel | Checked where | How often | Cost of a check | Worst case after revocation | Role change reaches it | Rate limit | Fails | Verdict |

## Findings, by impact

1. Finding. Where. The window it leaves. The fix. Effort S/M/L. Proposed ticket.

## What I could not verify
```
