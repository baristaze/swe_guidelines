---
name: ops-investigate
description: "Investigate one environment of the platform with a read-only credential: the alarms, the error rate, the latency, the worker outcomes, the pool, the queue, the cost against the budget, and the platform's size, then report what is wrong and what to do next. Every read goes through the signals' own APIs (CloudWatch, X-Ray, the error tracker in the cloud; Prometheus, Jaeger, GlitchTip locally). Use when something looks off, when an alarm fires, or as the daily look. Never writes."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(uv run:*)
---

# ops-investigate

One environment, one window, one credential that can only read. The
skill looks at every signal the platform emits and says what is wrong,
how big the platform is, and which skill runs next. It changes
nothing.

## Input

`--env local|staging|production [--since 1h] [--request-id <id>] [--alarm <name>]`

`--env` is required; ask for it when missing. `--since` is the window,
a duration ending now, one hour by default. `--request-id` narrows the
look to one request across every signal. `--alarm` starts from one
alarm by name and reads the platform's size before anything else.

`local` reads the compose stack's twins (Prometheus, Jaeger, GlitchTip,
`docker compose logs`) and needs no cloud. The skill is testable with
no account.

## Role and credential

`--env local` needs the compose stack with the `devx` profile up
(`make devx-up`) and the env file below. No cloud credential.

`--env staging` and `--env production` need the investigate profile
of that environment, `acme-<env>-investigate`, which assumes the role
`acme-investigate-<env>`. Before any other command, run

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and check that `Account` is the environment's `account_id` in
`deployment/cloud/environments.json` and that `Arn` reads
`arn:aws:sts::<account>:assumed-role/acme-investigate-<env>/...`.
Refuse to run under any other identity, an administrator profile
above all. A wider credential is not a convenience; it is
the boundary gone. Every `aws` command below carries
`--profile acme-<env>-investigate`. Never read `AWS_PROFILE` as a
substitute.

The env file `~/.config/acme/ops/<env>.env` is owner-only and outside
the repository. It holds `ACME_API_URL`, `ACME_OPERATOR_TOKEN` (a
`read` operator token; the file's `ACME_PROVISIONER_TOKEN`, a `write`
token, belongs to the traffic generator alone), `ACME_ERROR_TRACKER_URL`,
and `ACME_ERROR_TRACKER_TOKEN`. `local.env`, which `make seed` writes,
points at the compose stack and adds the twins, `ACME_PROMETHEUS_URL`
and `ACME_JAEGER_URL`, on the ports `.env` names. It holds no password
and no TOTP secret: an agent never signs in with a password.

Never read the env file, with `Read`, `cat`, or anything else: its
values stay out of this conversation. A command that needs one sources
the file and makes the call in the same command, because shell state
does not persist between calls. Every block below that names an
`ACME_` variable starts with that line and runs as one command:

```bash
set -a; . ~/.config/acme/ops/<env>.env; set +a
curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/me"
```

`acme-ops` reads the file itself from `--env`. Never print a token.
The operator token carries one permission and expires within the
hour. When a call answers `401`, stop and ask the person to run
`uv run acme-ops token --env <env> --identity operator` in their own
terminal, which asks there for the password and the TOTP code; never
ask for either in the conversation.

## Procedure

The process names below are the ones `deployment/README.md` lists;
`api` and `maintenance` are a tree the scaffold built with its worker,
and a worker added since is one more name.

1. Verify the credential as Role and credential states. Compute the
   window: `--since` back from now, as epoch seconds
   for the cloud and as a Prometheus range for local.
2. Read the platform's size first:

   ```bash
   uv run acme-ops size --env <env>
   ```

   It prints tenants, users, and the entities written in the last day
   (one count per entity the product exposes on the operator plane,
   and the events), through `GET /v1/admin/size` with the env file's
   operator token. Every finding below is read against this number
   and against whose traffic it was.
3. Alarms. Cloud:

   ```bash
   aws cloudwatch describe-alarms --alarm-name-prefix acme-<env>- \
     --profile acme-<env>-investigate
   ```

   Local has no alarm topic: run the alarm conditions as Prometheus
   queries against `$ACME_PROMETHEUS_URL/api/v1/query`, over the
   window `[<since>]`, each `curl` sourcing the env file in the same
   command:

   - 5xx ratio: `sum(rate(acme_http_requests_total{status=~"5.."}[<since>])) / sum(rate(acme_http_requests_total[<since>]))`, alarm above 0.01
   - targets up: `up{job!="prometheus"}`, alarm on any 0 (a host process
     and a container are two targets of one job; one of them is down
     by design)
   - p95 latency: `histogram_quantile(0.95, sum by (le) (rate(acme_http_request_seconds_bucket[<since>])))`, alarm above 1 s
   - running processes: the `up` targets again, one per process
   - the database's CPU and free storage: no local exporter; report
     them as not read
   - the queue, in a system with a worker, from the gauges its sweep
     sets: `max(acme_queue_oldest_age_seconds)` (the oldest waiting
     item), `max(acme_work_failed)` (failed work),
     `max(acme_records_parked)` (parked records), and
     `max(acme_outbox_lag_seconds)` (the outbox's lag), each against
     the threshold its cloud alarm declares
   With `--alarm <name>`, start here and apply the first responder
   rule of step 10 before reading anything else.
4. Request rate, error ratio, p95, by route. Cloud, one query per
   panel of the dashboard `acme-<env>`, namespace `Acme`:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> \
     --metric-data-queries '[{"Id":"req","MetricStat":{"Metric":{"Namespace":"Acme","MetricName":"acme_http_requests_total","Dimensions":[{"Name":"service","Value":"api"},{"Name":"environment","Value":"<env>"}]},"Period":60,"Stat":"Sum"}}]'
   ```

   Local:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -sG "$ACME_PROMETHEUS_URL/api/v1/query" \
     --data-urlencode 'query=sum by (route, status) (rate(acme_http_requests_total[5m]))'
   curl -sG "$ACME_PROMETHEUS_URL/api/v1/query" \
     --data-urlencode 'query=histogram_quantile(0.95, sum by (le, route) (rate(acme_http_request_seconds_bucket[5m])))'
   ```

5. Workers, queue, pool, cache: one counter carries every outcome,
   `acme_outcomes_total{subsystem, outcome}` (subsystems `worker`,
   `outbox`, `queue`, `cache`, `rate_limit`, `admission`,
   `idempotency`), read as `sum by (subsystem, outcome)
   (increase(acme_outcomes_total[<since>]))`. The queue's age, its
   failed and parked work, and the outbox's lag are the sweep's
   gauges of step 3, read over the window, and in the cloud from the
   same metrics through `get-metric-data` beside their alarms. Pool
   checkouts have no metric; the cloud reads the pool from the
   database's connection count. Cloud also reads the
   running count against the desired count:

   ```bash
   aws ecs describe-services --cluster acme-<env> \
     --services acme-<env>-api acme-<env>-maintenance \
     --profile acme-<env>-investigate
   ```

6. Errors. Cloud: the error tracker's REST API at
   `$ACME_ERROR_TRACKER_URL` with the token as a bearer, the issues
   of the window, newest first. Local: the same shape against
   GlitchTip:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" \
     "$ACME_ERROR_TRACKER_URL/api/0/organizations/<org>/issues/?statsPeriod=<since>"
   ```

   `<org>` is the slug `GET /api/0/organizations/` lists (locally
   `acme`):

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" "$ACME_ERROR_TRACKER_URL/api/0/organizations/"
   ```

7. Logs. Cloud, one log group per process, `/acme/<env>/<process>`:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, level, request_id, @message | filter level = "ERROR" | sort @timestamp desc | limit 100'
   aws logs get-query-results --query-id <id> --profile acme-<env>-investigate
   ```

   Local: `docker compose -f deployment/local/docker-compose.yml -f
   deployment/local/docker-compose.full.yml logs --since <since> api
   maintenance` from the repository root when the processes
   run in containers. When they run on the host (`scripts/dev.sh`
   writes no file; it logs to its terminal), `grep` the file the
   process was started with, and say "not read" when there is none.
   A local line carries the request id in brackets, `[<id>]`, or as
   `"request_id"` when `ACME_LOG_JSON` is on.
   With `--request-id`, filter every source on it: `filter request_id
   = "<id>"` in the cloud, `grep` locally.
8. Traces. Cloud:

   ```bash
   aws xray get-trace-summaries --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> \
     --filter-expression 'service("acme-api") AND responsetime > 1'
   ```

   Local, Jaeger's v3 API (the service is the process name, `api`, and
   the request id is the span attribute `acme.request_id`):

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -sG "$ACME_JAEGER_URL/api/v3/traces" \
     --data-urlencode query.service_name=api \
     --data-urlencode "query.start_time_min=<start, RFC 3339>" \
     --data-urlencode "query.start_time_max=<end, RFC 3339>" \
     --data-urlencode query.duration_min=1s
   ```

   and filter the spans on the attribute client-side; the query API
   ignores attribute filters. An empty answer means the process ran
   with no `ACME_OTEL_ENDPOINT`, which is a finding, not an error.
   With `--request-id`, filter on the `request_id` annotation in the
   cloud and the `acme.request_id` attribute locally instead.
9. Cost, cloud only. The month to date against the budget:

   ```bash
   aws ce get-cost-and-usage --profile acme-<env>-investigate \
     --time-period Start=<first of month>,End=<today> \
     --granularity MONTHLY --metrics UnblendedCost \
     --filter '{"Tags":{"Key":"environment","Values":["<env>"]}}'
   aws budgets describe-budgets --account-id <account> \
     --profile acme-<env>-investigate
   ```

10. The first responder rule. An alarm or a finding is read against
    the size of step 2 and whose traffic raised it. In production
    nothing is suppressed: a new production has one tenant, and it is
    the first customer. Outside production, when the traffic is the
    team's own (the developer, a stress run, the generator's run
    tenants), the finding is reported as suppressed, with the reason
    and what was read, and not escalated. A platform with tenants who are not the team gets the
    finding as a finding, with the request ids that prove it.
11. Write the report. Name the next skill: `ops-root-cause` with an
    org id when one tenant's rows explain it, `ops-watch` when the
    signal is still moving, `ops-infra-as-code` when the fix is a
    resource.

## What it never does

- No write to the cloud: no `aws` verb that is not `get`, `describe`,
  `list`, `start-query`, `get-query-results`, or `tail`.
- No secret value read or printed: the tokens stay in the env file,
  which is sourced and never read, and
  `aws secretsmanager get-secret-value` is denied to the role and
  never attempted.
- No tenant data: the signals carry no tenant id, and this skill reads
  no tenant's rows. That is `ops-root-cause`, for one named tenant.
- No `terraform apply`, no console clicks, no scaling by hand.
- No re-reading a wider window than asked; a longer look is a second
  run with a longer `--since`.

## Output

```markdown
# Investigation: <env>, last <since>

**Credential.** <profile and the Arn it resolved to, or local>
**Size.** <tenants> tenants, <users> users, <n> written in the last day

## Alarms

- <alarm name>: <state since when>, <suppressed: reason | escalated>

## Signals

- Requests: <rate>, error ratio <ratio>, p95 <ms> by route
- Workers: <outcomes per kind>, queue oldest <age>, failed <n>, parked <n>, outbox lag <age>
- Pool and cache: <checkouts, timeouts, hits, misses>
- Errors: <count>, top issue <title> (<request id, or none>)
- Cost: <month to date> of <budget> USD (cloud only)

## Findings

- <what is wrong, one sentence, with the request id that proves it>

## Next

<the skill to run next, with its arguments, or "nothing">
```
