---
name: ops-investigate
description: "Investigate one environment of the system with a read-only credential: the alarms, the error rate, the latency, the outcomes, gauges, queues, and log lines each namespace's signals file names, the pool, the cost against the budget, and the system's size, then report what is wrong and what to do next. Every read goes through the signals' own APIs (CloudWatch, X-Ray, the error tracker in the cloud; Prometheus, Jaeger, GlitchTip locally). Use when something looks off, when an alarm fires, or as the daily look. Never writes."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(uv run acme-ops size:*), Bash(sleep:*)
---

# ops-investigate

One environment, one window, one credential that can only read. The
skill looks at every signal the system emits and says what is wrong,
how big the system is, and which skill runs next. It changes
nothing.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env local|staging|production [--since 1h] [--request-id <id>] [--alarm <name>]`

`--env` is required; ask for it when missing. `--since` is the window,
a duration ending now, one hour by default. `--request-id` narrows the
look to one request across every signal. `--alarm` starts from one
alarm by name and reads the system's size before anything else.

`local` reads the compose stack's twins (Prometheus, Jaeger, GlitchTip,
`docker compose logs`) and needs no cloud. The skill is testable with
no account.

## Role and credential

`--env local` needs the compose stack with the `devx` profile up
(`make devx-up`) and the env file below. No cloud credential.

`--env staging` and `--env production` run under the investigate
profile of that environment, `acme-<env>-investigate`, checked with
`sts get-caller-identity` before any other command as the preamble
states. Refuse any profile wider than the investigate role. Every
`aws` command below carries `--profile acme-<env>-investigate`.

The env file `~/.config/acme/ops/<env>.env` gives the API's URL, the
`read` operator token, and the error tracker's URL, token, org, and
project. It holds no `write` token: the traffic generator's has a file
of its own, which this skill never sources. Never read the env file; a
command that needs a value sources it in the same command, as every
block below does. Never print a token. On a `401`
the token has expired: stop, and name the refresh the preamble gives.

## Procedure

The processes are `api` and `maintenance`, as
`deployment/README.md` lists them. Each is an ECS service of that
name in the cluster `acme-<env>`, with the log group
`/acme/<env>/<process>`.

Before step 1, read every file in `../_shared/signals/`, a path from
this skill's folder. Its `README.md` says what each heading holds, and
each other file is one namespace or one process, the scaffold's and a
copy's alike: a copy adds `<ns>.md` there for each namespace of its
own. Step 3 reads their Alarms, step 5 their Outcomes, Gauges, and
Queues, step 7 their Log lines, Kinds, and Commands, step 8 their
Search, and the report their Report.

1. Verify the credential as Role and credential states. Compute the
   window: `--since` back from now, as epoch seconds
   for the cloud and as a Prometheus range for local.
2. Read the system's size first:

   ```bash
   uv run acme-ops size --env <env>
   ```

   When it refuses the env file for holding the provisioner's token,
   stop, and give the person the line it printed: no later step runs,
   and none sources the file. It prints tenants, users, and the change events
   produced in the last day,
   through `GET /v1/admin/size`
   with the env file's operator token (`uv run acme-ops size --env <env>`,
   which leaves the traffic generator's own tenants out). The size is
   the maintenance worker's latest count, made every five minutes, and
   the command prints how long ago it counted. A count older than ten
   minutes says the worker's sweep has stopped counting, which is a
   finding of its own; a `404` says no worker has counted in this
   environment yet. A system of one tenant and one user is the
   developer. Every finding below is read against this number and
   against whose traffic it was.
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
   - each signals file's Alarms, by the condition and the threshold it
     gives

   A gauge is a read of the worker's sweep, one per pass; a local stack
   whose worker is not running has none, which is "not read".
   With `--alarm <name>`, start here and apply the first responder
   rule of step 11 before reading anything else.
4. Request rate, error ratio, p95, by route. Cloud, the queries of the
   dashboard `acme-<env>`, namespace `Acme`. The p95 is the load
   balancer's, so read its ARN first:

   ```bash
   aws elbv2 describe-load-balancers --names acme-<env> \
     --query 'LoadBalancers[0].LoadBalancerArn' --output text \
     --profile acme-<env>-investigate
   ```

   `<load balancer>` below is what follows `loadbalancer/` in that
   ARN, `app/acme-<env>/<id>`. One call then reads the three signals:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> --output text \
     --query 'MetricDataResults[?length(Values) > `0`].[Id,Label,sum(Values),max(Values)]' \
     --metric-data-queries '[
       {"Id":"req","Period":60,"Expression":"SUM(SEARCH('"'"'{\"Acme\",OTelLib,environment,method,route,service,status} MetricName=\"acme_http_requests_total\" environment=\"<env>\"'"'"', '"'"'Sum'"'"', 60))"},
       {"Id":"by","Period":60,"Label":"${PROP('"'"'Dim.status'"'"')} ${PROP('"'"'Dim.method'"'"')} ${PROP('"'"'Dim.route'"'"')}","Expression":"SEARCH('"'"'{\"Acme\",OTelLib,environment,method,route,service,status} MetricName=\"acme_http_requests_total\" environment=\"<env>\"'"'"', '"'"'Sum'"'"', 60)"},
       {"Id":"p95","Label":"p95","MetricStat":{"Metric":{"Namespace":"AWS/ApplicationELB","MetricName":"TargetResponseTime","Dimensions":[{"Name":"LoadBalancer","Value":"<load balancer>"}]},"Period":60,"Stat":"p95"}}
     ]'
   ```

   It prints one line for each series with a datapoint in the window:
   its id, its label, the sum of its minutes, and the highest of them.

   - `req` is every request: its sum is the window's count, and the
     rate is that count over the window's seconds.
   - A `by` line is one status, method, and route. The error ratio is
     the sum of the lines whose status starts with `5` over the sum of
     `req`; one route's ratio is the same over that route's lines.
   - `p95` is the load balancer's target response time over every
     route, in seconds. The report's p95 is its highest minute times
     1,000, in milliseconds, written once, as every route together;
     with no `p95` line, no request crossed the load balancer, and the
     report writes `p95 none`. The cloud has no p95 by route: the
     app's histogram reaches CloudWatch as a statistic set, which
     holds no percentile.

   Every series carries all its labels as dimensions, and the exporter
   adds `OTelLib`. CloudWatch matches dimensions exactly, so a
   `MetricStat` naming only some of them finds nothing, and neither
   does a `SEARCH` whose schema leaves one out: it answers with no
   datapoint and no error. The schema is the dashboard's own
   (`deployment/terraform/modules/dashboard/`). The load balancer's
   health checks are requests, so a call that prints no `req` line is
   "not read", never "no traffic".

   Local:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -sG "$ACME_PROMETHEUS_URL/api/v1/query" \
     --data-urlencode 'query=sum by (route, status) (rate(acme_http_requests_total[5m]))'
   curl -sG "$ACME_PROMETHEUS_URL/api/v1/query" \
     --data-urlencode 'query=histogram_quantile(0.95, sum by (le, route) (rate(acme_http_request_seconds_bucket[5m])))'
   ```

5. Outcomes, gauges, queues, the pool: one counter carries every
   outcome, `acme_outcomes_total{subsystem, outcome}`, read as
   `sum by (subsystem, outcome) (increase(acme_outcomes_total[<since>]))`.
   A signals file's Outcomes says what each outcome of its subsystems
   means. Two subsystems are the infrastructure's, the report's Pool
   and cache: `storage` counts the pool's `checkout_timeout` and
   `statement_timeout`, and `cache` its `hit`, `miss`, and
   `unreachable`.

   The gauges are every signals file's Gauges. Locally they are gauges
   on Prometheus, read as `max(<gauge>)`. In the cloud they are metrics
   of the same names in `Acme` with no dimension, filtered from the
   worker's sweep line, which the alarms read. One call reads them all,
   with one query per gauge:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> \
     --metric-data-queries '[{"Id":"<id>","MetricStat":{"Metric":{"Namespace":"Acme","MetricName":"<gauge>"},"Period":60,"Stat":"Maximum"}}]'
   ```

   The list holds one such query for each gauge, each with an `Id` of
   its own: lowercase letters, digits, and underscores, starting with
   a letter.

   The outcomes and the pool are one more call in the cloud: the
   counter by the dashboard's schema, the twin of the `sum by` above,
   and the database's connection count:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> --output text \
     --query 'MetricDataResults[?length(Values) > `0`].[Id,Label,sum(Values),max(Values)]' \
     --metric-data-queries '[
       {"Id":"out","Period":60,"Label":"${PROP('"'"'Dim.service'"'"')} ${PROP('"'"'Dim.subsystem'"'"')} ${PROP('"'"'Dim.outcome'"'"')}","Expression":"SEARCH('"'"'{\"Acme\",OTelLib,environment,outcome,service,subsystem} MetricName=\"acme_outcomes_total\" environment=\"<env>\"'"'"', '"'"'Sum'"'"', 60)"},
       {"Id":"pool","Label":"connections","MetricStat":{"Metric":{"Namespace":"AWS/RDS","MetricName":"DatabaseConnections","Dimensions":[{"Name":"DBInstanceIdentifier","Value":"acme-<env>"}]},"Period":60,"Stat":"Maximum"}}
     ]'
   ```

   An `out` line is one process, subsystem, and outcome, and its sum
   is the window's count. `pool` is read by its highest minute: the
   most connections the database held.

   An outcome, a gauge, or an alarm is read as its signals file says,
   with the log line it names as the cause, which step 7 or step 8
   finds. A queue's depth and oldest age, and pool checkouts, have no
   metric of the worker's; the cloud reads the pool from the database's
   connection count, as above, and each queue of every signals file's
   Queues, with its dead letter, from SQS:

   ```bash
   for q in <queue> <queue>-dead; do
     url="$(aws sqs get-queue-url --queue-name acme-<env>-$q \
       --query QueueUrl --output text --profile acme-<env>-investigate)"
     aws sqs get-queue-attributes --queue-url "$url" --profile acme-<env>-investigate \
       --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
   done
   ```

   with every queue the signals files name in the list, each followed
   by its `-dead`. A message in a `-dead` queue is one its consumer
   could not handle after its retries: a finding, with the queue's
   name. Cloud also reads the running count against the desired count:

   ```bash
   aws ecs describe-services --cluster acme-<env> \
     --services api maintenance \
     --profile acme-<env>-investigate
   ```

6. Errors. There is one tracker project for the product, and every
   environment reports into it, so the read names that project and
   filters on the environment. The error tracker's REST API at
   `$ACME_ERROR_TRACKER_URL` with the token as a bearer, the issues
   last seen in the window in this environment, newest first, with
   `local` as the environment locally:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   case "$ACME_ERROR_TRACKER_URL" in *sentry.io*) window=" lastSeen:-<since>" ;; *) window="" ;; esac
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" --get \
     --data-urlencode "query=environment:<env>$window" \
     "$ACME_ERROR_TRACKER_URL/api/0/projects/$ACME_ERROR_TRACKER_ORG/$ACME_ERROR_TRACKER_PROJECT/issues/"
   ```

   The tracker decides how the window is asked, not `--env`. Sentry
   takes it as the search term `lastSeen:-<since>`, in minutes, hours,
   days, or weeks (`90m`, `1h`, `2d`, `1w`). GlitchTip, the local
   stack's tracker or a deployed one, reads `lastSeen:` as a tag no
   issue carries and would answer with no issue. So only Sentry's own
   host gets the term, and any other tracker gets the query without
   it. Either way, keep the issues whose `lastSeen` field is inside
   the window. The window is never `statsPeriod`: that parameter sizes
   each issue's graph, takes only `24h` and `14d`, and answers `400`
   to any other value.

   An issue in that project can hold events of more than one
   environment, so an issue the query returned is not by itself this
   environment's: read its events
   (`/api/0/issues/<issue id>/events/`) and keep the ones whose
   `environment` tag is `<env>`. Dropping the filter reports every
   environment's errors as this one's, which is wrong, not wider.

   The org's slug is what `GET /api/0/organizations/` lists (locally
   `acme`):

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" "$ACME_ERROR_TRACKER_URL/api/0/organizations/"
   ```

   When the env file names no tracker, this step reads nothing and the
   report says "not read", never "no errors"; the other steps stand on
   their own, and an investigation is not stopped by it.

7. Logs. Cloud, one log group per process, `/acme/<env>/<process>`:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, level, request_id, @message | filter level = "ERROR" | sort @timestamp desc | limit 100'
   aws logs get-query-results --query-id <id> --profile acme-<env>-investigate
   ```

   Poll `get-query-results` at most 10 times for one query, each poll
   after `sleep 5` in the same command, so ten polls cover about a
   minute. When the status is still `Scheduled` or `Running` after the
   tenth, stop polling: the report reads that query's logs as "not
   read: the query did not finish in 10 polls", with the query id, and
   the next step starts. Step 8's query is polled the same way.

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
   A line a signals file's Log lines names is read as that file says:
   the finding it is, with the ids it carries, and a kind on it as the
   Kinds of the file that names the kind say. When the file's Commands
   give a person's command for that finding, the report names it for a
   person to run in their own terminal; this skill never runs it.
8. The lines a read of `ERROR` misses: a start line, a warning, an
   access line. Each signals file's Search is one pattern, and one
   query joins them all with `|`. Cloud, over the same window:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, @log, @message | filter @message like /<every Search, joined with |>/ | sort @timestamp desc | limit 50'
   ```

   What each line means, and the secret it points to, is its signals
   file's Log lines. The skill reads a secret's name, never its value:
   the secrets are denied to the role, and their names are enough. A
   line written once, when a task starts, is not in a window after the
   last rollout: report "not in the window", never "configured".
   Writing a secret's value is a person's step under their own
   sign-in, never this skill's. Locally, `grep` the same patterns in
   each process's own output, as step 7 reads it.
9. Traces. Cloud:

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
   With `--request-id`, filter on the `acme_request_id` annotation in the
   cloud and the `acme.request_id` attribute locally instead.
10. Cost, cloud only. The month to date against the budget:

    ```bash
    aws ce get-cost-and-usage --profile acme-<env>-investigate \
      --time-period Start=<first of month>,End=<today> \
      --granularity MONTHLY --metrics UnblendedCost \
      --filter '{"Tags":{"Key":"environment","Values":["<env>"]}}'
    aws budgets describe-budgets --account-id <account> \
      --profile acme-<env>-investigate
    ```

11. The first responder rule. In production nothing is suppressed:
    a new production's one tenant is its first customer, so every
    alarm and finding there is reported as a finding, with the request
    ids that prove it. Outside production, an alarm or a finding is
    read against the size of step 2, and it is suppressed only when
    the traffic behind it is the team's own: one tenant and one user,
    and that user is the team's. A suppression is never silent: it is
    reported as suppressed, with the reason, the size, and the request
    ids it rests on.
12. Write the report. Name the next skill: `ops-root-cause` with an
    org id when one tenant's rows explain it, `ops-watch` when the
    signal is still moving, `ops-infra-as-code` when the fix is a
    resource, and, for a finding whose cause is fixed, the command its
    signals file gives a person to run, as step 7 names it. A session
    follows at most 2 hops of Next. The skill it starts with is hop
    zero; the report of the second hop still names its next skill, and
    the session stops there and reports.

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
- No unbounded poll: never more than 10 polls of a query.

## Output

```markdown
# Investigation: <env>, last <since>

**Credential.** <profile and the Arn it resolved to, or local>
**Size.** <tenants> tenants, <users> users, <n> change events in the last day, counted <age> ago

## Alarms

- <alarm name>: <state since when>, <suppressed: reason | escalated>

## Signals

- Requests: <rate>, error ratio <ratio>, p95 <ms> by route (cloud: one p95 <ms, or none>, every route together)
- Services: api, maintenance <running>/<desired> (cloud only)
- Pool and cache: <connections, checkout and statement timeouts, hits, misses, unreachable>
- <each Report line of every signals file, in the order of the files' names>
- Errors: <count>, top issue <title> (<request id, or none>), or "not
  read: the environment names no error tracker"
- Cost: <month to date> of <budget> USD (cloud only)

## Findings

- <what is wrong, one sentence, with the request id that proves it>

## Next

<the skill to run next, with its arguments, or "nothing">
```
