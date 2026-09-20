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

and check that `Arn` reads
`arn:aws:sts::<account>:assumed-role/acme-investigate-<env>/...`.
Refuse to run under any other identity, the administrator profile
`acme-admin` above all. A wider credential is not a convenience; it is
the boundary gone. Every `aws` command below carries
`--profile acme-<env>-investigate`. Never read `AWS_PROFILE` as a
substitute.

The env file `~/.config/acme/ops/<env>.env` is owner-only and outside
the repository. It holds `ACME_API_URL`, `ACME_OPERATOR_EMAIL`,
`ACME_OPERATOR_PASSWORD` (a `read` entry; the file's `write` entry,
`ACME_PROVISIONER_EMAIL`, belongs to the traffic generator alone), `ACME_ERROR_TRACKER_URL`, and
`ACME_ERROR_TRACKER_TOKEN`. `local.env` points at the compose stack
and adds the twins, `ACME_PROMETHEUS_URL` and `ACME_JAEGER_URL`, on
the ports `.env` names. Read the file, use its values in commands, and
never print the password or the token.

## Procedure

1. Verify the credential as Role and credential states. Read the env
   file. Compute the window: `--since` back from now, as epoch seconds
   for the cloud and as a Prometheus range for local.
2. Read the platform's size first:

   ```bash
   uv run acme-ops size --env <env>
   ```

   It prints tenants, users, and the product's main entity written in
   the last day, through `GET /v1/admin/size` with the env file's
   operator identity. A platform of one tenant and one user is the
   developer. Every finding below is read against this number.
3. Alarms. Cloud:

   ```bash
   aws cloudwatch describe-alarms --alarm-name-prefix acme-<env>- \
     --profile acme-<env>-investigate
   ```

   Local has no alarm topic: run the six alarm conditions as
   Prometheus queries against `$ACME_PROMETHEUS_URL/api/v1/query`
   (5xx ratio, targets up, p95 latency, running processes, and the
   database's CPU and free storage where the exporter reports them).
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
   curl -sG "$ACME_PROMETHEUS_URL/api/v1/query" \
     --data-urlencode 'query=sum by (route, status) (rate(acme_http_requests_total[5m]))'
   curl -sG "$ACME_PROMETHEUS_URL/api/v1/query" \
     --data-urlencode 'query=histogram_quantile(0.95, sum by (le, route) (rate(acme_http_request_seconds_bucket[5m])))'
   ```

5. Workers, queue, pool, cache: the outcome counters per kind, the
   queue depth and the oldest age, pool checkouts and timeouts, cache
   hits and misses, through the same two APIs. Cloud also reads the
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
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" \
     "$ACME_ERROR_TRACKER_URL/api/0/organizations/<org>/issues/?statsPeriod=<since>"
   ```

7. Logs. Cloud, one log group per process, `/acme/<env>/<process>`:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, level, request_id, @message | filter level = "ERROR" | sort @timestamp desc | limit 100'
   aws logs get-query-results --query-id <id> --profile acme-<env>-investigate
   ```

   Local: `docker compose -f deployment/local/docker-compose.yml logs
   --since <since>` for the containers, and the host processes' log
   files `scripts/dev.sh` writes. With `--request-id`, filter every
   source on it: `filter request_id = "<id>"` in the cloud, `grep`
   locally.
8. Traces. Cloud:

   ```bash
   aws xray get-trace-summaries --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> \
     --filter-expression 'service("acme-api") AND responsetime > 1'
   ```

   Local: `curl -s "$ACME_JAEGER_URL/api/traces?service=acme-api&lookback=<since>&minDuration=1s&limit=20"`.
   With `--request-id`, filter on the `request_id` annotation or tag
   instead.
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
    the size of step 2. When the platform holds one tenant and one
    user, the person behind the signal is the developer: the finding
    is reported as suppressed, with the reason and the size, and not
    escalated. A platform with tenants who are not the team gets the
    finding as a finding, with the request ids that prove it.
11. Write the report. Name the next skill: `ops-root-cause` with an
    org id when one tenant's rows explain it, `ops-watch` when the
    signal is still moving, `ops-infra-as-code` when the fix is a
    resource.

## What it never does

- No write to the cloud: no `aws` verb that is not `get`, `describe`,
  `list`, `start-query`, `get-query-results`, or `tail`.
- No secret value printed: the password and the token stay in the env
  file, `aws secretsmanager get-secret-value` is denied to the role
  and never attempted.
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
- Workers: <outcomes per kind>, queue depth <n>, oldest <age>
- Pool and cache: <checkouts, timeouts, hits, misses>
- Errors: <count>, top issue <title> (<request id>)
- Cost: <month to date> of <budget> USD (cloud only)

## Findings

- <what is wrong, one sentence, with the request id that proves it>

## Next

<the skill to run next, with its arguments, or "nothing">
```
