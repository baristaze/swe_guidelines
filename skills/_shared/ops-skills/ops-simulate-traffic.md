---
name: ops-simulate-traffic
description: "Drive realistic traffic at one environment of the platform through its edge with the platform's own traffic generator, at one of four profiles (light, regular, heavy, stress) for a duration, and report the table: requests by route and status, p50, p95, p99, and the error ratio. Use to warm an environment, to reproduce a load-shaped problem, or as the thirty-second wiring check. A run against a cloud environment is the platform developer's call."
allowed-tools: Read, Bash(aws:*), Bash(uv run:*), Bash(make traffic PROFILE=light DURATION=30)
---

# ops-simulate-traffic

One generator, four profiles. The generator rides `clients/python` and
the operator plane, drives the edge (never a manager), and plays
realistic sessions: sign in, list, add a handful of entities, edit,
complete, reopen, move, list again, delete one, read the events, one
socket that sees its own change, sign out. The profiles differ by
tenants, members, concurrency, and think time.

## Input

`--env local|staging|production --profile light|regular|heavy|stress [--duration 60] [--report <path>]`

`--env` and `--profile` are required; ask for them when missing.
`--duration` is in seconds, sixty by default. `--report` writes the
table as JSON beside printing it. `local` drives the API at
`http://127.0.0.1:8000`, started by `scripts/dev.sh` or `make up`, and
needs no cloud; its file is `~/.config/acme/ops/local.env`, which
`make seed` writes with a local provisioner, and when the file is
absent the seeded people of `.env` are used. `stress` is the top profile; a run at it with a target
is `stress-test-run`, not this skill.

## Role and credential

`--env local` needs the compose stack up (`make up`) and the env file
below. No cloud credential.

`--env staging` and `--env production` need two things. The
investigate profile of that environment, `acme-<env>-investigate`,
to read the signals back, verified with

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and refused under any other identity, `acme-admin` above all. And the
env file `~/.config/acme/ops/<env>.env`, owner-only and outside the
repository, whose provisioner identity (`ACME_PROVISIONER_EMAIL`,
`ACME_PROVISIONER_PASSWORD` against `ACME_API_URL`) creates the tenants
the sessions run in; that identity's allowlist entry is `write`, it is
the one write entry the file holds, and only this generator uses it.
The tenants it creates are the generator's own, named with the run
id, so no real tenant is touched. With `--orgs 0` the run drives the
seeded people and needs no provisioner. Never print the password or the
token.

## Procedure

1. Verify the credential as Role and credential states. Read the env
   file. Against `production`, ask before running anything above
   `light`; the generator's tenants are real rows in the real
   database, and the choice is the platform developer's.
2. Run the generator:

   ```bash
   uv run acme-ops traffic --env <env> --profile <profile> \
     --duration <seconds> [--report <path>]
   ```

   The thirty-second wiring check, which CI's integration job runs
   against the local stack, is `make traffic PROFILE=light DURATION=30`; it proves the
   edge, the client, and the signals are wired and is never a stress
   test.
3. Read the table the run prints: requests by route and status, p50,
   p95, p99 per route, and the error ratio, then its two notes: the
   profile's concurrency and think time, and one sample request id of
   the run. A 5xx during the run is a
   finding with its request id; a 4xx from the generator's own
   sessions (a conflict on a retried create, a 404 after the delete)
   is expected where the session shape explains it.
4. Read one signal back to prove the run was seen: the request
   counter moved by at least the number of requests the table shows
   over the run's window (`--since-minutes`, the run's duration rounded
   up; other traffic in the window adds to it), through
   `uv run acme-ops signals check --env <env> --request-id <the sample
   id> --since-minutes <n> [--log-file <the process's log>]`. Locally
   the log leg needs the file the API was started with, and the trace
   leg needs `ACME_OTEL_ENDPOINT` set on that process; report either as
   not read otherwise.
5. Write the report.

## What it never does

- No write outside the generator's own tenants; it never signs in as
  a real user.
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

**Total.** <n> requests, error ratio <ratio>

## Signal

- request counter moved by <n> (<request id> found in <signals>)

## Findings

- <a 5xx or a latency outlier, with its request id, or "none">
```
