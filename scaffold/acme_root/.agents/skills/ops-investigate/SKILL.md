---
name: ops-investigate
description: "Investigate one environment of the platform with a read-only credential: the alarms, the error rate, the latency, the worker outcomes and the work items that failed for good, the pool, the queue and its dead letter, the identity provider (the sign-in and its webhook), the cost against the budget, and the platform's size, then report what is wrong and what to do next. Every read goes through the signals' own APIs (CloudWatch, X-Ray, the error tracker in the cloud; Prometheus, Jaeger, GlitchTip locally). Use when something looks off, when an alarm fires, or as the daily look. Never writes."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(uv run acme-ops size:*), Bash(sleep:*)
---

# ops-investigate

One environment, one window, one credential that can only read. The
skill looks at every signal the platform emits and says what is wrong,
how big the platform is, and which skill runs next. It changes
nothing.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

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
`/acme/<env>/<process>`. The queue is `acme-<env>-webhooks` (the
identity provider's deliveries, which the API checked at
`/webhooks/identity` and queued), with a dead-letter queue named with
`-dead` after it. The deliveries come in through `/acme/<env>/api`,
and their handling is in `/acme/<env>/maintenance`.

1. Verify the credential as Role and credential states. Compute the
   window: `--since` back from now, as epoch seconds
   for the cloud and as a Prometheus range for local.
2. Read the platform's size first:

   ```bash
   uv run acme-ops size --env <env>
   ```

   When it refuses the env file for holding the provisioner's token,
   stop, and give the person the line it printed: no later step runs,
   and none sources the file. It prints tenants, users, and the events
   produced in the last day,
   through `GET /v1/admin/size`
   with the env file's operator token (`uv run acme-ops size --env <env>`,
   which leaves the traffic generator's own tenants out). The size is
   the maintenance worker's latest count, made every five minutes, and
   the command prints how long ago it counted. A count older than ten
   minutes says the worker's sweep has stopped counting, which is a
   finding of its own; a `404` says no worker has counted in this
   environment yet. A platform of one tenant and one user is the
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
   - work backlog: `max(acme_work_oldest_ready_seconds)`, alarm above
     600 (the item ready longest waited ten minutes: no worker claims)
   - work dead letter: `max(acme_work_failed_recently)`, alarm at 1 or
     more (an item failed for good in the last fifteen minutes)
   - outbox lag: `max(acme_outbox_oldest_pending_seconds)`, alarm above
     300 (the oldest row not yet relayed landed five minutes ago)
   - outbox dead letter: `max(acme_outbox_failed_recently)`, alarm at 1
     or more (a row failed for good in the last fifteen minutes)

   The last four are the worker's sweep reads, one per pass; a local
   stack whose worker is not running has none, which is "not read".
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

5. Workers, queue, pool, cache: one counter carries every outcome,
   `acme_outcomes_total{subsystem, outcome}` (subsystems `worker`,
   `work`, `outbox`, `queue`, `deliveries`, `cache`, `rate_limit`,
   `admission`, `idempotency`, `orchestrations`), read as `sum by (subsystem, outcome)
   (increase(acme_outcomes_total[<since>]))`. A work item that failed
   for good counts `work`/`dead_letter`: its attempts ran out
   (`worker`/`failed` beside it), or its handler was refused, a failure
   no retry changes (`worker`/`refused`, a provider refusing the call
   itself). Each one is an `ERROR` line in `/acme/<env>/maintenance`,
   `work item <id> (<kind>) in org <org> failed for good: <reason>`,
   which step 7 finds; `work`/`requeued` counts the ones an operator
   sent back. The long-running records
   are `orchestrations`, one outcome per kind and state,
   `<kind>_<state>`: `_running` when a record starts, `_parked` when a
   step waits on a provider that did not answer, `_resumed`,
   `_succeeded`, and `_failed`. The mechanism's own kind is `noop`
   (`noop_succeeded`); a product adds its kinds beside it. A parked
   record is woken by the event that clears its reason (`WAKE_PARKED`)
   or by a person; one that stays parked is read beside the provider's
   lines of step 8. A `_failed` is `defect`, a step that still failed
   on its item's last attempt, which the maintenance log names at level
   `ERROR` with the record's id and the org's (`<kind> <id> in org
   <org> failed: defect ...`), so step 7 finds it. A running record
   whose `_succeeded` never follows is a step the queue keeps retrying:
   read the worker's `failed` outcomes beside it. The work queue and the outbox are Postgres
   tables, and each sweep pass reads four numbers of them across every
   tenant: `acme_work_oldest_ready_seconds` (how long the item ready
   longest has waited), `acme_work_failed_recently` (items failed in the
   last fifteen minutes and still failed),
   `acme_outbox_oldest_pending_seconds` (how long ago the oldest row not
   yet relayed landed), and `acme_outbox_failed_recently` (rows failed
   for good in the last fifteen minutes). Locally they are gauges on
   Prometheus. In the cloud they are metrics of the same names in `Acme`
   with no dimension, which the alarms `acme-<env>-work-backlog`,
   `-work-dead-letter`, `-outbox-lag`, and `-outbox-dead-letter` read,
   filtered from the worker's sweep line:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> \
     --metric-data-queries '[{"Id":"ready","MetricStat":{"Metric":{"Namespace":"Acme","MetricName":"acme_work_oldest_ready_seconds"},"Period":60,"Stat":"Maximum"}},{"Id":"failed","MetricStat":{"Metric":{"Namespace":"Acme","MetricName":"acme_work_failed_recently"},"Period":60,"Stat":"Maximum"}},{"Id":"outbox","MetricStat":{"Metric":{"Namespace":"Acme","MetricName":"acme_outbox_oldest_pending_seconds"},"Period":60,"Stat":"Maximum"}},{"Id":"dead","MetricStat":{"Metric":{"Namespace":"Acme","MetricName":"acme_outbox_failed_recently"},"Period":60,"Stat":"Maximum"}}]'
   ```

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

   A backlog with the maintenance service at its desired count is a
   worker that claims nothing: read its log for `claim failed`. An
   outbox lag is a relay that keeps failing: its log names each attempt,
   `outbox relay of <row id> (<kind>) failed on attempt <n>: <error>`.
   An outbox dead letter is the last of those attempts: the log says
   `outbox row <row id> (<kind>) failed for good after <n> attempts:
   <error>`, and the org's diary holds an `outbox.row.failed` event that
   names the row's kind and target.
   A work item failed for good is step 7's line. The inbound queue's
   depth and oldest age, and pool checkouts, have no metric of the
   worker's; the cloud reads the pool from the database's connection
   count, as above, and the queue and its dead letter from SQS:

   ```bash
   for q in webhooks webhooks-dead; do
     url="$(aws sqs get-queue-url --queue-name acme-<env>-$q \
       --query QueueUrl --output text --profile acme-<env>-investigate)"
     aws sqs get-queue-attributes --queue-url "$url" --profile acme-<env>-investigate \
       --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
   done
   ```

   A message in a `-dead` queue is a delivery the worker could not
   handle after its retries: a finding, with the queue's name. The
   worker counts each delivery it handles as `deliveries`/`<outcome>`
   (`applied`, `duplicate`, `unowned`, `malformed`, `failed`); a
   `failed` stays on the queue and comes back. Cloud
   also reads the running count against the desired count:

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
   A failed work item is a finding with its id, its kind, its org, and
   its reason, all off the `failed for good` line. The reason says
   whether it is the provider's or a person's to fix; a
   `DELETE_ACCOUNT` one leaves a deleted person's personal org in
   place until it runs. Once the cause is fixed, a person sends it back
   with `uv run acme-ops work requeue --env <env> --org <org> <item>`,
   in their own terminal: it signs them in with the second factor and
   mints a `write` token for that one call. This skill names the
   command in the report and never runs it.
8. The identity provider. It says in its own log when it is not
   configured, and the skill reads that, never a secret: the secrets
   are denied to the role, and their names are enough. Cloud, over the
   same window:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, @log, @message | filter @message like /identity provider|WorkOS application|WorkOS credential check|ACME_WORKOS_WEBHOOK_SECRET|webhooks\/identity (400|503)|identity delivery/ | sort @timestamp desc | limit 50'
   ```

   What each line means, and the secret it points to:

   - `identity provider: none (ACME_WORKOS_API_KEY is not set)` and
     `every sign-in through one answers 503`: nobody can sign in
     through WorkOS. `acme/<env>/workos_api_key` is `off`, or the
     API's tasks started before it was written.
   - `ACME_WORKOS_API_KEY is not the API key of the WorkOS application`,
     and the API's tasks stop at start: the secret holds a key WorkOS
     refuses as the Acme App's (the environment's API key, another
     application's, or the other WorkOS environment's). The key belongs
     on the Acme App's own API keys tab. `the WorkOS credential check
     did not finish` or `answered` is a warning only: WorkOS could not
     say, and the API started.
   - `ACME_WORKOS_WEBHOOK_SECRET is not set` on `POST
     /webhooks/identity`, a `503` there in the access lines of
     `/acme/<env>/api`: `acme/<env>/workos_webhook_secret` is `off`, or
     the API's tasks started before it was written. Every delivery is
     refused, and WorkOS sends it again later.
   - A `400` (`webhook_signature_invalid`) on `/webhooks/identity` in
     the access lines of `/acme/<env>/api`: the secret does not match
     the signing secret of the endpoint in the WorkOS dashboard, so
     every delivery is refused.
   - `identity delivery <id> names no org` or `names org <id>, which
     is gone` in `/acme/<env>/maintenance`: one delivery about an org
     this environment does not hold, dropped. Not a finding about a
     secret.

   The start lines are written once, when a task starts, so a window
   after the last rollout holds none: report "not in the window",
   never "configured". A finding here names the secret and
   `docs/runbooks/providers/workos.md`; writing the value is a
   person's step under their own sign-in, never this skill's. Locally,
   `grep` the same lines in each process's own output, as step 7 reads
   it; a laptop that sets no `ACME_WORKOS_API_KEY` signs people in by
   the local sign-in on purpose, so `identity provider: none` there is
   not a finding.
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
    resource, and, for a failed work item whose cause is fixed, the
    requeue command of step 7 for a person to run. A session follows at
    most 2 hops of Next. The skill it starts with is hop zero; the
    report of the second hop still names its next skill, and the
    session stops there and reports.

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
**Size.** <tenants> tenants, <users> users, <n> events in the last day, counted <age> ago

## Alarms

- <alarm name>: <state since when>, <suppressed: reason | escalated>

## Signals

- Requests: <rate>, error ratio <ratio>, p95 <ms> by route (cloud: one p95 <ms, or none>, every route together)
- Workers: <outcomes per kind>, oldest ready item <age>, failed in the last fifteen minutes <n>, oldest pending outbox row <age>
- Failed work items: <item id, kind, org id, reason; or none>
- Orchestrations: <kind> <started, parked, succeeded, failed> per kind, defects <record and org ids, or none>
- Queue: webhooks <n> (dead <n>), deliveries <outcomes>; services api, maintenance <running>/<desired>
- Identity provider: sign-in <configured | off: acme/<env>/workos_api_key | not in the window>, webhook <configured | off: acme/<env>/workos_webhook_secret | signature refused: acme/<env>/workos_webhook_secret | not in the window>
- Pool and cache: <checkouts, timeouts, hits, misses>
- Errors: <count>, top issue <title> (<request id, or none>), or "not
  read: the environment names no error tracker"
- Cost: <month to date> of <budget> USD (cloud only)

## Findings

- <what is wrong, one sentence, with the request id that proves it>

## Next

<the skill to run next, with its arguments, or "nothing">
```
