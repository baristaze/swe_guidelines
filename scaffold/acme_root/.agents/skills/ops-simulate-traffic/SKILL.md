---
name: ops-simulate-traffic
description: "Drive realistic traffic at one environment of the platform through its edge with the platform's own traffic generator, at one of four profiles (light, regular, heavy, stress) for a duration, and report the table: requests by route and status, p50, p95, p99, and the error ratio. Use to warm an environment, to reproduce a load-shaped problem, or as the thirty-second wiring check. A run against a cloud environment is the platform developer's call."
allowed-tools: Read, Bash(aws:*), Bash(uv run acme-ops:*), Bash(make traffic PROFILE=light DURATION=30)
---

# ops-simulate-traffic

One generator, four profiles. The generator rides `clients/python` and
the operator plane, drives the edge (never a manager), and plays
realistic sessions: sign in, open the socket, read the person, list
the members, rename the person, create an API key and revoke it, read
the events, see one of its own changes arrive on the socket, sign out.
The profiles differ by tenants, members, concurrency, and think time.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env local|staging|production --profile light|regular|heavy|stress [--duration <seconds>] [--orgs <n>] [--report <path>]`

`--env` and `--profile` are required; ask for them when missing.
`--duration` is in seconds, the profile's own by default (60, 300,
600, or 900 from light to stress). `--report` writes the table as JSON
beside printing it. `--orgs` is how many tenants the run provisions,
the profile's number by default; `--orgs 0` drives the seeded people
of `.env` instead and needs no provisioner, so it is for `local` only
and refused against a cloud environment, which has no seeded people.
`local` drives the API at `http://127.0.0.1:8000`, started by
`scripts/dev.sh` or `make up`, and needs no cloud; its file is
`~/.config/acme/ops/local.env`, which `make seed` writes with the local
provisioner's token, and without a provisioner token in it a local run
takes `--orgs 0`. `stress` is the
top profile; a run at it with a target is `stress-test-run`, not this
skill.

## Role and credential

Two identities, both named here: the provisioner, an operator identity
whose allowlist entry writes and that only this skill and
`stress-test-run` use, and in the cloud the investigator's profile for
any signal it reads back.

`--env local` needs the compose stack up (`make up`) and the env file
below. No cloud credential.

`--env staging` and `--env production` need two things. The
investigate profile of that environment, `acme-<env>-investigate`, to
read the signals back, checked with `sts get-caller-identity` as the
preamble states. Refuse any profile wider than the investigate role.
And the env file's provisioner token (`ACME_PROVISIONER_TOKEN`
against `ACME_API_URL`), which creates the tenants the sessions run
in: the provisioner's allowlist entry is `write`, its token is the one
write token the file holds, and only this generator uses it. The
tenants it creates are the generator's own, named `ops-<run id>-<n>`,
so no real tenant is touched, and removed when the run ends. A cloud
run always provisions its tenants, so it always needs the
provisioner's token.

Never read the env file; a command that needs a value sources it in
the same command, and `acme-ops` reads it itself from `--env`. Never
print a token. When the generator reports the provisioner's token
refused or expired, stop, and name the refresh the preamble gives.

In production the provisioner's allowlist entry is disabled between
runs, so no standing writing credential waits there. A run with
`--orgs` above `0` against production needs the person to enable it
first, by dispatching `grant-operator.yml` on `release` with the
provisioner's email, `write`, and `mint_token: provisioner`, then
copying the token into the env file with `uv run acme-ops token --env
production --identity provisioner --profile acme-prod-power` in their
own terminal, and to disable it after, by dispatching it again with
`disable`; this skill holds no role that does either, and says which
dispatch is due.

## Procedure

1. Verify the credential as Role and credential states. Against
   `production`, ask before running anything above
   `light`; the generator's tenants are real rows in the real
   database, and the choice is the platform developer's. Against
   `production` with `--orgs` above `0`, a generator whose
   provisioner the operator plane refuses stops before its first
   session; the skill then names the dispatch that enables it.
2. Run the generator:

   ```bash
   uv run acme-ops traffic --env <env> --profile <profile> \
     [--duration <seconds>] [--orgs 0] [--report <path>]
   ```

   The thirty-second wiring check, which CI runs against the local
   stack, is `make traffic PROFILE=light DURATION=30`; it proves the
   edge, the client, and the signals are wired and is never a stress
   test.
3. Read the table the run prints: requests by route and status, p50,
   p95, p99 per route, and the error ratio, then the two totals, the
   working requests and the sign-in and sign-out beside them (the run
   signs each person in once, at the start, and out once at the end),
   and the notes: how many people signed in, the profile's concurrency
   and think time, and one sample request id of the run. A 5xx during
   the run is a
   finding with its request id; a 4xx from the generator's own
   sessions is expected only where the session shape explains it (a
   `409` `idempotency_in_progress` on an API key create the client sent
   again while the first was still running).
4. Read one signal back to prove the run was seen: the request
   counter moved by at least the number of requests the table shows
   over the run's window (`--since-minutes`, the run's duration rounded
   up; other traffic in the window adds to it), through
   `uv run acme-ops signals check --env <env> --request-id <the sample
   id> --since-minutes <n> [--log-file <the process's log>]`. Locally
   the log leg needs the file the API was started with, and the trace
   leg needs `ACME_OTEL_ENDPOINT` set on that process; report either as
   not read otherwise.
5. Check that the run removed its tenants: the generator deletes
   each through `DELETE /v1/admin/orgs/{org_id}` when it ends, and
   names any it could not remove, which the report lists. A removed
   org is closed at once; the worker deletes it once its `DELETE_ORG`
   item has run, so the org list shows it live and empty until then. Against
   `production`, name the dispatch that disables the provisioner
   again.
6. Write the report.

## What it never does

- No write outside the generator's own tenants, and no tenant of its
  own left behind; it never signs in as a real user.
- No provisioner left enabled in production after a run: the report
  names the dispatch that disables it.
- No write to the cloud's resources, no scaling, no apply.
- No secret value printed.
- No run above `light` against production without the person saying
  so in this session.
- No target and no verdict: the numbers are reported, not judged;
  judging is `stress-test-run`.

## Output

```markdown
# Traffic: <env>, <profile>, <duration>s

**Credential.** <profile and Arn, or local>
**Sessions.** <n> tenants, <n> members, concurrency <n>, think time <ms>

## Requests

| Route | Status | Count | p50 ms | p95 ms | p99 ms |
|-------|--------|-------|--------|--------|--------|
| <route> | <status> | <n> | <ms> | <ms> | <ms> |

**Total.** <n> requests, error ratio <ratio>; working <n> requests,
p95 <ms> ms; sign-in and sign-out <n> requests, p95 <ms> ms
**Run tenants.** <n> created, <n> removed, <ids left behind, or none>
**Provisioner.** <local | staging | production: disable dispatch due>

## Signal

- request counter moved by <n> (<request id> found in <signals>)

## Findings

- <a 5xx or a latency outlier, with its request id, or "none">
```
