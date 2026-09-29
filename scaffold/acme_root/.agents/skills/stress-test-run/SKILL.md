---
name: stress-test-run
description: "Run one stress test scenario of the platform against one environment with the platform's own generator, read the signals back through their own APIs, and report pass or fail against a target stated before the run: the scenario's p95 and error ratio, or the one the run states instead. A real run is the platform developer's choice; the CI sanity run is thirty seconds at the light profile and is never a stress test. Needs the read-only investigate profile to read the signals back."
allowed-tools: Read, Bash(aws:*), Bash(uv run:*)
---

# stress-test-run

The generator at the scenario's profile, for the scenario's duration,
then the signals, then a verdict. The target is the scenario's unless
the run states its own; either way it is stated before the run, and
the verdict names it and where it came from.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`<name> --env local|staging|production [--p95-ms <ms>] [--error-ratio <ratio>] [--report <path>]`

`<name>` names `ops/stress/<name>.yaml` and is required; `--env` is
required; ask for either when missing. `--p95-ms` and `--error-ratio`
state the pass mark for this one run, one number or both; without them
the scenario's own is judged. They are given before the run, never
after it: a target picked once the numbers are in is not a target. A
generous one with a short `--duration` is a wiring check; a strict one
on the same scenario is the run worth reading. `--report` writes the
run's table as JSON beside printing it; the verdict is printed, not
written to the file. `local` runs
against the compose stack and needs no cloud; it proves the scenario
and the wiring, and its numbers are the developer's machine's, not
the platform's.

## Role and credential

Two identities, both named here: the provisioner, the writing operator
identity the traffic generator creates its run's tenants under, and the
investigator's profile, under which the run reads its signals back in
the cloud.

`--env local` needs the compose stack with the `devx` profile up
(`make devx-up`) and the env file below. No cloud credential.

`--env staging` and `--env production` run under the investigate
profile of that environment, `acme-<env>-investigate`, to read the
signals back, checked with `sts get-caller-identity` before anything
else as the preamble states. Refuse any profile wider than the
investigate role. The env file gives the generator the provisioner's
token (`ACME_PROVISIONER_TOKEN` against `ACME_API_URL`, the file's
one `write` token, which creates the run's own tenants and removes
them when the run ends), and the signals their URLs and token. In
production the provisioner's entry is disabled between runs; the
person enables it, with its token, and disables it again by
dispatching `grant-operator.yml`, as `ops-simulate-traffic` states.

Never read the env file; a command that needs a value sources it in
the same command, and `acme-ops` reads it itself from `--env`. Never
print a token. When the generator reports the provisioner's token
refused or expired, stop, and name the refresh the preamble gives.

## Procedure

1. Read the scenario. Refuse one without a target; that is
   `stress-test-create-or-update`'s job. `--scenario <name>` and a bare
   `<name>` mean the same file. Say which target this run will judge,
   the scenario's or the one the invocation gave, before running.
   Verify the credential as Role and
   credential states. Check the env file exists and is owner-only,
   through `acme-ops`, which refuses a file that is not; never read
   or print it.
2. Say what is about to happen and wait for the person: a real run
   is the platform developer's choice, because it costs money in the
   cloud, writes rows, and can trip the alarms it is meant to test.
   The CI sanity run is `acme-ops traffic --profile light` for thirty
   seconds against the local stack, and it is a wiring check, never a
   stress test. Against `production`, refuse unless the person says
   so in this session. Against `local`, the invoking prompt's word is
   enough, and an unattended run does not wait.
3. Note the start time and the size of the platform before the run
   (`uv run acme-ops size --env <env>`), so the report can say what
   the run added. The size is the worker's count every five minutes:
   the one that says what the run added is the first whose
   `counted_at` is past the run's end. Run:

   ```bash
   uv run acme-ops stress --scenario ops/stress/<name>.yaml \
     --env <env> [--orgs 0] [--p95-ms <ms>] [--error-ratio <ratio>] \
     [--report <path>]
   ```

   `--orgs 0` drives the seeded org; without it the run provisions the
   profile's tenants and needs a provisioner in the env file. The
   target flags go in only when the invocation gave them; the run
   prints the target it judged and where each half of it came from.

   The generator ramps, soaks, and prints the table: requests by
   route and status, p50, p95, p99, and the error ratio, then two
   totals and the notes. The totals are the two groups the target
   reads: the working requests, the person, member, and API key
   routes, the event stream, and the socket's ticket, whose p95 the
   target judges, and the
   sign-in and sign-out beside them, one of each per person for the
   whole run, reported with their own p95 and judged by nothing. The
   notes say how many people signed in, the profile's concurrency and
   think time, and one sample request id of the run, which step 4
   follows. A 4xx the session shape explains (a `409` on an API key
   create the client sent again while the first was still running) is
   counted by status and not as an error; a session that
   fails is counted under sessions, and a person the API refused at
   sign-in is a note.
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
   (increase(acme_outcomes_total[<window>]))`; queue age has no metric
   locally. Locally the log leg needs `--log-file`, and an empty trace
   store means the process ran with no `ACME_OTEL_ENDPOINT`: report
   the leg as not read, and do not fail the run on it.

   Cloud: the alarms that fired during the window,
   `aws cloudwatch describe-alarms --alarm-name-prefix acme-<env>-
   --profile acme-<env>-investigate`, are part of the result.
5. Decide. Pass when the platform's p95 is at or under the target and
   the error ratio is at or under the target, both over the run plus
   one scrape interval (the ramp cannot be cut out at a fifteen-second
   scrape). The p95 is the working requests'; the sign-in and the
   sign-out are reported beside the verdict with their own p95 and
   never held to the target. Fail otherwise, naming the first route
   that broke the target and the request id that shows it. An alarm
   that fired is reported either way.
6. Write the report. A fail names the next skill: `ops-investigate`
   with the window, or `ops-infra-as-code` when the numbers say a
   lever. A session follows at most 2 hops of Next. The skill it
   starts with is hop zero; the report of the second hop still names
   its next skill, and the session stops there and reports.

## What it never does

- No run without the person's word, and none against production
  without it in this session.
- No write outside the generator's own tenants, and none left behind:
  the generator removes them when the run ends, and the report names
  any it could not; no scaling, no apply, no change to the scenario.
- No secret value printed.
- No verdict from the generator's numbers alone: the platform's own
  signals decide.
- No target moved to fit the result: a target stated before the run is
  the run's; one rewritten after the numbers are in is not a target,
  in the scenario file or in a rerun.

## Output

```markdown
# Stress test: <name>, <env>, <profile>, <duration>s

**Credential.** <profile and Arn, or local>
**Target.** p95 <ms> ms over the working requests (<from the scenario |
set for this run>), error ratio <ratio> over every request (<source>)
**Verdict.** <PASS | FAIL: <route>, <p95 or ratio>, request id <id>>

## Requests

| Route | Status | Count | p50 ms | p95 ms | p99 ms |
|-------|--------|-------|--------|--------|--------|
| <route> | <status> | <n> | <ms> | <ms> | <ms> |

- Working, the requests the target judges: <n> requests, p50 <ms> ms,
  p95 <ms> ms, p99 <ms> ms
- Sign-in and sign-out, reported beside the verdict and judged by
  nothing: <n> requests, p50 <ms> ms, p95 <ms> ms

## Signals over the soak

- Platform p95: <ms> ms, error ratio <ratio>, requests <n>
- Workers: <outcomes per kind>, queue oldest <age>
- Alarms during the window: <names, or none>
- Request <id>: log <found>, trace <found>, error event <found | none>

## Next

<the skill to run next, with its arguments, or "nothing">
```
