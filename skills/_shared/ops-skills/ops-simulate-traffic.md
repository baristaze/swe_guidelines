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

`--env local|staging|production --profile light|regular|heavy|stress [--duration 60] [--orgs N] [--report <path>]`

`--env` and `--profile` are required; ask for them when missing.
`--duration` is in seconds, sixty by default. `--orgs` is how many
tenants the run provisions, the profile's number by default; `0`
drives the seeded people alone, so it is for `local` only and refused
against a cloud environment, which has no seeded people. `--report` writes the table as JSON
beside printing it. `local` drives the API at the `ACME_API_URL` of
`~/.config/acme/ops/local.env`, which `make seed` writes with a local
provisioner, started by `scripts/dev.sh` or `make up`, and needs no
cloud. `stress` is the top profile; a run at it with a target is
`stress-test-run`, not this skill.

## Role and credential

`--env local` needs the compose stack up (`make up`) and the env file
below. No cloud credential.

`--env staging` and `--env production` need two things. The
investigate profile of that environment, `acme-<env>-investigate`,
to read the signals back, verified with

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and refused under any other identity, an administrator profile above all. And the
env file `~/.config/acme/ops/<env>.env`, owner-only and outside the
repository, whose provisioner token (`ACME_PROVISIONER_TOKEN` against
`ACME_API_URL`) creates the tenants the sessions run in; the
provisioner's allowlist entry is `write`, its token is the one write
token the file holds, and only this generator uses it. The tenants it
creates are the generator's own, named with the run id, so no real
tenant is touched, and removed when the run ends. The file holds no
password and no TOTP secret: an agent never signs in with a password.
A cloud run always provisions its tenants, so it always needs the
provisioner's token.

Never read the env file, with `Read`, `cat`, or anything else: its
values stay out of this conversation. `acme-ops` reads the file
itself from `--env`, and a command that needs a value from it
sources the file and makes the call in the same command, because
shell state does not persist between calls. Never print a token.
The provisioner's token carries one permission and expires within
the hour. When the generator reports it refused or expired, stop and
ask the person to refresh it: locally with `uv run acme-ops token
--env local --identity provisioner`, and in the cloud by dispatching
`grant-operator.yml` with `mint_token: provisioner`, then running
`uv run acme-ops token --env <env> --identity provisioner` in their
own terminal, which copies the token the grant job wrote under their
identity-center sign-in.

In production the provisioner's allowlist entry is disabled between
runs, so no standing writing credential waits there. A run with
`--orgs` above `0` against production needs the person to enable it
first, by dispatching `grant-operator.yml` on `release` with the
provisioner's email, `write`, and `mint_token: provisioner`, then
copying the token into the env file with `uv run acme-ops token --env
production --identity provisioner` in their own terminal, and to
disable it after, by
dispatching it again with `disable`; this skill holds no role that
does either, and says which dispatch is due.

## Procedure

1. Verify the credential as Role and credential states. Against `production`, ask before running anything above
   `light`; the generator's tenants are real rows in the real
   database, and the choice is the platform developer's. Against
   `production` with `--orgs` above `0`, a generator whose
   provisioner the operator plane refuses stops before its first
   session; the skill then names the dispatch that enables it.
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
5. Check that the run removed its tenants: the generator deletes
   each through `DELETE /v1/admin/orgs/{org_id}` when it ends, and
   names any it could not remove, which the report lists. Against
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

**Total.** <n> requests, error ratio <ratio>
**Run tenants.** <n> created, <n> removed, <ids left behind, or none>
**Provisioner.** <local | staging | production: disable dispatch due>

## Signal

- request counter moved by <n> (<request id> found in <signals>)

## Findings

- <a 5xx or a latency outlier, with its request id, or "none">
```
