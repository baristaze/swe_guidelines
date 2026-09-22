---
name: stress-test-run
description: "Run one stress test scenario of the platform against one environment with the platform's own generator, read the signals back through their own APIs, and report pass or fail against the target the scenario states (p95 and error ratio). A real run is the platform developer's choice; the CI sanity run is thirty seconds at the light profile and is never a stress test. Needs the read-only investigate profile to read the signals back."
allowed-tools: Read, Bash(aws:*), Bash(uv run:*)
---

# stress-test-run

The generator at the scenario's profile, for the scenario's duration,
then the signals, then a verdict. The scenario holds the target; this
skill holds it to it.

## Input

`[--scenario] <name> --env local|staging|production [--report <path>]`

`<name>` names `ops/stress/<name>.yaml` and is required; `--env` is
required; ask for either when missing. `--report` writes the run's
table and the verdict as JSON beside printing them. `local` runs
against the compose stack and needs no cloud; it proves the scenario
and the wiring, and its numbers are the developer's machine's, not
the platform's.

## Role and credential

`--env local` needs the compose stack with the `devx` profile up
(`make devx-up`) and the env file below. No cloud credential.

`--env staging` and `--env production` need the investigate profile
of that environment, `acme-<env>-investigate`, to read the signals
back, verified before anything else with

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and refused under any other identity, an administrator profile above all. The env
file `~/.config/acme/ops/<env>.env`, owner-only and outside the
repository, gives the generator its provisioner identity
(`ACME_PROVISIONER_EMAIL`, `ACME_PROVISIONER_PASSWORD` against
`ACME_API_URL`, the file's one `write` entry, which creates the run's
own tenants),
and the signals their URLs and token; `local.env` is the one
`make seed` writes. The provisioner signs in with its password and a
TOTP code, which `acme-ops` derives from
`ACME_PROVISIONER_TOTP_SECRET`, the secret `acme-ops enrol` wrote
into the same file. In production the provisioner's entry is
disabled between runs; the person enables it and disables it again
by dispatching `grant-operator.yml`, as `ops-simulate-traffic`
states. Never print the password, the secret, a code, or the token.

## Procedure

1. Read the scenario. Refuse one without a target; that is
   `stress-test-create-or-update`'s job. `--scenario <name>` and a bare
   `<name>` mean the same file. Verify the credential as Role and
   credential states. Check the env file exists, is owner-only, and
   holds the keys; never print it.
2. Say what is about to happen and wait for the person: a real run
   is the platform developer's choice, because it costs money in the
   cloud, writes rows, and can trip the alarms it is meant to test.
   The CI sanity run is `make traffic PROFILE=light DURATION=30`,
   `acme-ops traffic --profile light` for thirty seconds against the
   local stack in CI's integration job, and it is a wiring check, never a
   stress test. Against `production`, refuse unless the person says
   so in this session. Against `local`, the invoking prompt's word is
   enough, and an unattended run does not wait.
3. Note the start time and the size of the platform before the run
   (`uv run acme-ops size --env <env>`), so the report can say what
   the run added. Run:

   ```bash
   uv run acme-ops stress --scenario ops/stress/<name>.yaml \
     --env <env> [--report <path>]
   ```

   The generator ramps, soaks, and prints the table: requests by
   route and status, p50, p95, p99, and the error ratio, then two
   notes: the profile's concurrency and think time, and one sample
   request id of the run, which step 4 follows. A 4xx the session
   shape explains (a rate-limited sign-in, a conflict on a retried
   create) is counted by status and not as an error; a session that
   fails is counted under sessions.
4. Read the signals back for the run's window, through the same
   interface every other skill reads: the request counter's delta,
   the p95 the platform measured (not the generator's), the worker
   outcomes, the error count, and one request id of the run followed
   across the log, the trace, and the tracker:

   ```bash
   uv run acme-ops signals check --env <env> --request-id <id> \
     [--log-file <the process's log>] [--since-minutes <n>]
   ```

   The worker outcomes are `sum by (subsystem, outcome)
   (increase(acme_outcomes_total[<window>]))`, and the queue's age is
   `max_over_time(acme_queue_oldest_age_seconds[<window>])`, the
   gauge the worker's sweep sets. Locally the log leg needs `--log-file`, and an empty trace
   store means the process ran with no `ACME_OTEL_ENDPOINT`: report
   the leg as not read, and do not fail the run on it.

   Cloud: the alarms that fired during the window,
   `aws cloudwatch describe-alarms --alarm-name-prefix acme-<env>-
   --profile acme-<env>-investigate`, are part of the result.
5. Decide. Pass when the platform's p95 is at or under the target and
   the error ratio is at or under the target, both over the run plus
   one scrape interval (the ramp cannot be cut out at a fifteen-second
   scrape). Fail otherwise, naming the first route that broke
   the target and the request id that shows it. An alarm that fired
   is reported either way.
6. Write the report. A fail names the next skill: `ops-investigate`
   with the window, or `ops-infra-as-code` when the numbers say a
   lever.

## What it never does

- No run without the person's word, and none against production
  without it in this session.
- No write outside the generator's own tenants; no scaling, no apply,
  no change to the scenario.
- No secret value printed.
- No verdict from the generator's numbers alone: the platform's own
  signals decide.
- No target moved to fit the result.

## Output

```markdown
# Stress test: <name>, <env>, <profile>, <duration>s

**Credential.** <profile and Arn, or local>
**Target.** p95 <ms> ms, error ratio <ratio>
**Verdict.** <PASS | FAIL: <route>, <p95 or ratio>, request id <id>>

## Requests

| Route | Status | Count | p50 ms | p95 ms | p99 ms |
|-------|--------|-------|--------|--------|--------|
| <route> | <status> | <n> | <ms> | <ms> | <ms> |

## Signals over the soak

- Platform p95: <ms> ms, error ratio <ratio>, requests <n>
- Workers: <outcomes per kind>, queue oldest <age>
- Alarms during the window: <names, or none>
- Request <id>: log <found>, trace <found>, error event <found | none>

## Next

<the skill to run next, with its arguments, or "nothing">
```
