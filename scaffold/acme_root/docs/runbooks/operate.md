# Operate: read an environment under the investigate profile

Every read of an environment runs under its investigate profile, and
nothing that writes runs under it. `acme-staging-investigate` and
`acme-production-investigate` assume `acme-investigate-<environment>`,
a role with `ReadOnlyAccess` plus the signal reads that policy leaves
out, fenced off secret values, data bucket objects, database
connections, the other environment, and every IAM write. An agent
holding it may look at everything it reaches. Each environment has an
AWS account of its own, so the profile also decides the account. The
investigate profile chains from a person's Identity Center sign-in
(`acme-staging`, `acme-prod`), so sign in first. [deploy.md](deploy.md)
says how the profiles are made; this runbook says what to run under
them. The `ops-investigate`, `ops-watch`, and `ops-root-cause` skills
run these same commands.

First, every time:

```bash
aws sso login --profile acme-staging            # or acme-prod; once per session
export AWS_PROFILE=acme-staging-investigate   # or acme-production-investigate
aws sts get-caller-identity --query Arn --output text
# arn:aws:sts::<account>:assumed-role/acme-investigate-staging/<session>
```

A skill that sees any other role in that answer stops, the sign-in's
role (PowerUserAccess, ReadOnlyAccess) included. The commands
below are staging's; production's replace `staging` with `production`
in every name.

## The dashboard

One CloudWatch dashboard per environment, `acme-<environment>`,
declared in `deployment/terraform/modules/dashboard` from
`dashboard.json.tftpl`. Its first five widgets are the local Grafana
dashboard's panels by title (`infra/tests/test_dashboard_parity.py`
holds them equal): Targets up, HTTP requests per second by route, HTTP
responses per second by status, HTTP latency p95 by route, Outcomes per
second. The rows after them are the cloud's own: the database's CPU and
connections, the cache's CPU, the queue's visible and dead messages,
the running tasks, the reads with a latency alarm, each queue's oldest
message, and the sweep's pass duration. The last row is both
dashboards' again, the work queue and the outbox that live in Postgres:
Work queue oldest ready item age, Work items failed in the last fifteen
minutes, Outbox oldest pending row age.

```bash
aws cloudwatch get-dashboard --dashboard-name acme-staging \
  --query DashboardBody --output text | jq '.widgets[].properties.title'
```

The application metrics live in the `Acme` namespace with every
Prometheus label as a dimension. The three the dashboard reads:

```bash
aws cloudwatch list-metrics --namespace Acme --metric-name acme_http_requests_total \
  --dimensions Name=environment,Value=staging \
  --query 'Metrics[].Dimensions[?Name==`route`].Value' --output text | sort -u

now=$(date +%s); aws cloudwatch get-metric-data \
  --start-time "$((now - 3600))" --end-time "$now" \
  --metric-data-queries '[{"Id":"rps","Expression":"SUM(SEARCH('"'"'{Acme,environment,method,route,service,status} MetricName=\"acme_http_requests_total\" environment=\"staging\"'"'"', '"'"'Sum'"'"', 60)) / 60","Label":"requests per second"}]' \
  --query 'MetricDataResults[0].Values[:10]'
```

The long-running records, the imports and each org's daily cleanup of
old done tasks, show on Outcomes per second under the subsystem
`orchestrations`, one outcome per kind and state (`task_cleanup_running`
when the sweep opens an org's day, `task_cleanup_succeeded`,
`task_import_parked`, `task_import_failed`). A failure that is a defect
is also an `ERROR` line in `/acme/<environment>/maintenance` naming the
record and its org; reading that one org's rows is `ops-root-cause`.

## The alarms

Seventeen per environment (one per service, two per inbound queue), to the topic `acme-<environment>-alarms`, from
`deployment/terraform/modules/alarms`. Their thresholds are the
module's inputs with defaults, and they are illustrative: a busier
platform moves them.

| Alarm | Fires when | Periods |
|-------|------------|---------|
| `acme-<env>-http-5xx-ratio` | more than 5 percent of requests answer 5xx | 3 |
| `acme-<env>-http-p95-latency` | the load balancer's p95 passes 1 second | 3 |
| `acme-<env>-unhealthy-targets` | one API target fails its health check | 3 |
| `acme-<env>-read-latency-v1-billing` | `GET /v1/billing` passes 1 second at p95, as the API times it | 3 |
| `acme-<env>-database-cpu` | the database's CPU passes 80 percent | 3 |
| `acme-<env>-database-free-storage` | free storage falls under 2 GiB | 3 |
| `acme-<env>-webhooks-backlog`, `-slack-backlog` | a queue's oldest message waited more than ten minutes, past the five tries a failing message gets before it is dead-lettered | 3 |
| `acme-<env>-webhooks-dead-letter`, `-slack-dead-letter` | one message is in the queue's `-dead` twin | 1 |
| `acme-<env>-sweep-duration` | a sweep pass took longer than 30 seconds: its budget is 20, so a longer pass is one step that is slow on its own, and the worker's `sweep:` lines name the tenant and the purge | 3 |
| `acme-<env>-work-backlog` | the work item ready longest waited more than ten minutes for a worker: none is claiming | 3 |
| `acme-<env>-work-dead-letter` | a work item failed for good in the last fifteen minutes | 1 |
| `acme-<env>-outbox-lag` | the oldest outbox row not yet relayed landed more than five minutes ago: the relay is stuck | 3 |
| `acme-<env>-outbox-dead-letter` | an outbox row failed for good in the last fifteen minutes: its relay ran out of attempts | 1 |
| `acme-<env>-api-tasks-below-desired`, `-maintenance-tasks-below-desired` | a service runs fewer tasks than it wants; six periods, which a routine worker deploy would otherwise trip | 6 |

A period is one minute. A queue that goes idle stops reporting, so a
queue alarm keeps its state through missing data: a dead-letter alarm
stays in `ALARM` until a person takes the message off `-dead`, not
until the queue goes quiet. The command at the end of the next block
counts what a dead-letter queue holds.

The four work and outbox alarms read what no AWS service publishes,
because the work queue and the outbox are Postgres tables. Each sweep
pass reads four numbers across every tenant and writes them as fields
of its one line in `/acme/<environment>/maintenance`, and a metric
filter per field writes them to the `Acme` namespace. They keep their
state through missing data too: a worker that is not running writes no
line, which the maintenance tasks alarm reports. The work dead-letter
alarm turns `OK` fifteen minutes after the last failure, or once the
item is requeued; the item stays failed until a person sends it back
(below). The outbox dead-letter alarm turns `OK` fifteen minutes after
the last row failed; nothing relays a failed row again, and the purge
takes it with the done rows after eight days.

```bash
aws cloudwatch describe-alarms --alarm-name-prefix acme-staging- \
  --query 'MetricAlarms[].{name:AlarmName,state:StateValue,since:StateUpdatedTimestamp}' --output table

aws cloudwatch describe-alarm-history --alarm-name acme-staging-http-5xx-ratio \
  --history-item-type StateUpdate --max-items 10 \
  --query 'AlarmHistoryItems[].{at:Timestamp,what:HistorySummary}' --output table
```

The first responder is an agent. Before it escalates, it reads the
platform's size (`uv run acme-ops size --env staging`): an alarm on a
platform of one tenant and one user is the developer at work. What it
cannot explain, it escalates with everything it read.

Reading behind an alarm, by request id:

```bash
# The log line that names it, from the last hour.
now=$(date +%s); query=$(aws logs start-query --log-group-name /acme/staging/api \
  --start-time "$((now - 3600))" --end-time "$now" \
  --query-string 'fields @timestamp, @message | filter request_id = "<request id>" | sort @timestamp' \
  --query queryId --output text)
sleep 2; aws logs get-query-results --query-id "$query" --query 'results[][].value' --output text

# A live tail while something is happening; ops-watch runs this in a loop.
aws logs tail /acme/staging/api --since 15m --format short

# The trace, then its segments.
aws xray get-trace-summaries --start-time "$((now - 3600))" --end-time "$now" \
  --filter-expression 'http.url CONTAINS "/v1/tasks"' \
  --query 'TraceSummaries[].{id:Id,ms:Duration,error:HasError}' --output table
aws xray batch-get-traces --trace-ids <trace id> --query 'Traces[0].Segments[].Document' --output text | jq .

# What runs, and what wants to run.
aws ecs describe-services --cluster acme-staging --services api maintenance \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,rollout:deployments[0].rolloutState}' --output table
aws rds describe-db-instances --db-instance-identifier acme-staging \
  --query 'DBInstances[0].{status:DBInstanceStatus,class:DBInstanceClass,storage:AllocatedStorage,max:MaxAllocatedStorage}'
for queue in webhooks slack; do
  aws sqs get-queue-attributes --queue-url "$(aws sqs get-queue-url --queue-name acme-staging-$queue-dead --query QueueUrl --output text)" \
    --attribute-names ApproximateNumberOfMessages
done
```

What the role refuses, by design, and what the refusal looks like:

```bash
aws secretsmanager get-secret-value --secret-id acme/staging/database_url   # AccessDeniedException
aws s3 cp s3://acme-staging-exports/some/key -                              # AccessDenied
aws sts assume-role --role-arn arn:aws:iam::<account>:role/acme-deploy-staging --role-session-name x   # AccessDenied
```

Planning an infrastructure change is a read too. The role reads
staging's state and takes no lock, so the plan is the pull request's
preview and nothing more (`ops-infra-as-code`):

```bash
terraform -chdir=deployment/terraform/environments/staging init -input=false \
  -backend-config="bucket=acme-state-<account>" \
  -backend-config="key=environments/staging/terraform.tfstate" \
  -backend-config="region=us-west-2"
terraform -chdir=deployment/terraform/environments/staging plan -lock=false -refresh=false \
  -var "api_image=<the digest deployed>" -var "maintenance_image=<the digest deployed>" \
  -var "api_domain_name=api.staging.acme.example" -var "app_domain_name=app.staging.acme.example" \
  -var "alarm_email=$ALARM_EMAIL"
```

The account and the two names are staging's entry in
`deployment/cloud/environments.json`.

## A client answered 429

A 429 is a rate limit, `rate_limited` in the envelope, with a
`Retry-After` of the seconds left on the window. The message says which
budget was spent
([ADR 0059](../adr/0059-authenticated-routes-have-limits.md)):

| Message | Budget | Setting |
|---|---|---|
| `rate limit exceeded`, on a sign-in route | the client address's sign-ins | `ACME_LOGIN_RATE_LIMIT` |
| `rate limit exceeded`, on any other route | the session's or API key's reads, or its writes | `ACME_CREDENTIAL_RATE_LIMIT_READS`, `ACME_CREDENTIAL_RATE_LIMIT_WRITES` |
| `too many failed authentications from this address` | the address's bearers that turned out unknown, expired, or revoked | `ACME_FAILED_AUTHENTICATION_LIMIT` |

Every window is a minute unless its `_WINDOW_SECONDS` setting says
otherwise. The refusal lifts on its own when the window ends. Nothing
needs clearing.

Outcomes per second shows where the refusals come from, under the
subsystem `rate_limit`: `rejected` for a spent credential or sign-in
budget, `authentication_failed` for each failed lookup, and
`address_refused` for each request refused without one. The 429s of
the last hour, by route:

```bash
now=$(date +%s); query=$(aws logs start-query --log-group-name /acme/staging/api \
  --start-time "$((now - 3600))" --end-time "$now" \
  --query-string 'filter http.status = 429 | stats count(*) by http.route' \
  --query queryId --output text)
sleep 2; aws logs get-query-results --query-id "$query" --query 'results' --output text
```

What each one usually is:

- **A credential's budget.** One integration polling too fast, or a
  client in a retry loop. Ask the tenant to poll less often, or to
  listen on the realtime channel instead. Raise the budget only when
  honest traffic needs it: set the setting in the environment's
  `app_environment` and deploy.
- **An address's failures.** A client that kept a revoked key, or
  someone trying tokens. Everyone behind that address is refused
  until the window ends, so a company's gateway can trip it. The
  budget is per address, never per person.

The limits fail open. While the cache is down or its breaker is open,
nothing is refused, and dead tokens reach the database again, one
transaction each. Admission still bounds the process.

## The work queue and the outbox

The four numbers behind their alarms, pass by pass, from the worker's
own lines:

```bash
now=$(date +%s); query=$(aws logs start-query --log-group-name /acme/staging/maintenance \
  --start-time "$((now - 3600))" --end-time "$now" \
  --query-string 'fields @timestamp, sweep.work_oldest_ready_seconds, sweep.work_failed_recently, sweep.outbox_oldest_pending_seconds, sweep.outbox_failed_recently | filter ispresent(sweep.duration_ms) | sort @timestamp desc | limit 20' \
  --query queryId --output text)
sleep 2; aws logs get-query-results --query-id "$query" --query 'results[]' --output json

aws cloudwatch get-metric-statistics --namespace Acme --metric-name acme_work_oldest_ready_seconds \
  --start-time "$(date -u -d '-1 hour' +%FT%TZ 2>/dev/null || date -u -v-1H +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
  --period 60 --statistics Maximum --query 'sort_by(Datapoints, &Timestamp)[-10:].[Timestamp,Maximum]' --output text
```

A backlog with the maintenance service at its desired count is a worker
that claims nothing: read its log for `claim failed`, and the Outcomes
widget for `worker`/`lease_lost`. An item on a lane no worker serves
waits too. A dead letter is the next section. An outbox lag is a relay
that keeps failing: each attempt of the sweep is a warning in the
worker's log, `outbox relay of <row id> (<kind>) failed on attempt <n>:
<error>`, and the error says whether the event store or the bus
refused. `the bus dropped the publish` is the bus: Valkey refused the
push, or the Valkey breaker is open. The event is already in the
stream, and the row waits until the bus takes its push. Read the
Outcomes widget for `outbox`/`publish_failed` beside
`valkey_breaker`/`opened`, and fix Valkey; the next pass sends the
backlog. After its tenth attempt the row is `failed for good`, an
`outbox.row.failed` event in its org's diary, which `ops-root-cause`
reads. It no longer counts as pending, so the lag lets it go, and
`acme-<env>-outbox-dead-letter` fires on it instead. The event names
the row's kind and its target. An entity change whose event store kept
refusing is missing from the org's stream, though its row in `core` is
written. One whose push alone was dropped is in the stream, and a
client reads it on its next catch-up. A `work.<kind>` row is work that
was never queued.

## Open sockets: the recheck and the head

Two settings of the API decide what an open socket costs the database
and how long it can be wrong. Both are in `.env.example` and left at
their defaults in every environment.

- `ACME_REALTIME_RECHECK_SECONDS` (300). Each socket asks again, once
  per interval, whether the session or the key behind it still holds:
  two transactions, six round trips, 72 an hour per socket. It is the
  longest a revoked credential keeps its socket when the bus lost the
  revocation. Lower it for a tighter bound, and pay per socket.
- `ACME_REALTIME_HEAD_MAX_AGE_SECONDS` (60). A ping answers with the
  head the process heard on the bus, and reads it only when nothing was
  heard or read for the tenant within this bound. It is the longest a
  hint the bus lost can hide from a quiet socket. Zero reads on every
  ping, three round trips each.

A socket the recheck closed says so in the API's log, `socket of user
<id> closed on its recheck: <reason>`: `not_authenticated` when the
credential is refused, `rights_changed` when the role changed. Each is
a session that went idle under an open tab, or a change the bus did
not carry. A burst of them is the second kind: read it beside the
Outcomes widget's `topics`/`publish_failed` and `topics`/`listener_failed`.

```bash
now=$(date +%s); query=$(aws logs start-query --log-group-name /acme/staging/api \
  --start-time "$((now - 3600))" --end-time "$now" \
  --query-string 'fields @timestamp, @message | filter @message like "closed on its recheck" | stats count() by bin(5m)' \
  --query queryId --output text)
sleep 2; aws logs get-query-results --query-id "$query" --query 'results[]' --output json
```

## A work item that failed for good

A background job fails for good when its attempts run out, or at once
when a provider refused the call itself (a `4xx` for the request, not
its availability and not the process's own key, which park). It stays
failed until a person sends it back. A `DELETE_ACCOUNT` item is the one
that matters most: the deleted person's personal org stays until it
runs.

Find it in the worker's log. The line names the item, its kind, its
org, and the reason:

```bash
now=$(date +%s); query=$(aws logs start-query --log-group-name /acme/staging/maintenance \
  --start-time "$((now - 86400))" --end-time "$now" \
  --query-string 'fields @timestamp, @message | filter @message like /failed for good/ | sort @timestamp desc | limit 20' \
  --query queryId --output text)
sleep 2; aws logs get-query-results --query-id "$query" --query 'results[][].value' --output text
# work item <item id> (DELETE_ACCOUNT) in org <org id> failed for good: refused: a provider refused the call: ...
```

The org's diary holds it too, as a `work.item.failed` event whose
`target_id` is the item, which `ops-root-cause` reads on the operator
plane (`GET /v1/admin/orgs/<org id>/events`).

Fix the cause first: the reason says whose it is. Then a person with a
`write` operator entry sends it back, in their own terminal:

```bash
uv run acme-ops work requeue --env staging --org <org id> <item id>
# requeued <item id> (DELETE_ACCOUNT) in org <org id>: queued, 0 of 3 attempts spent, available now
```

It signs you in with the second factor, mints a `write` token for
that one call, and signs that token out after it; the env file keeps its
`read` token. The item runs again
as a fresh one, with every attempt it had. A `work.item.requeued` event
in the org's diary names you. An item that is not failed is refused
(`work_not_failed`), so running it twice does nothing the second time.
This is a write, so no agent runs it.

## The costs

Each account has its own budget and anomaly monitor, declared by its
bootstrap root, so the account's bill is the environment's. The
`acme:environment` tag on every resource splits a report the same way.

```bash
account=$(aws sts get-caller-identity --query Account --output text)
aws budgets describe-budget --account-id "$account" --budget-name acme-staging-monthly \
  --query 'Budget.{limit:BudgetLimit.Amount,spent:CalculatedSpend.ActualSpend.Amount,forecast:CalculatedSpend.ForecastedSpend.Amount}'

first=$(date +%Y-%m-01); today=$(date +%F)
aws ce get-cost-and-usage --time-period "Start=$first,End=$today" --granularity MONTHLY \
  --metrics UnblendedCost --group-by Type=TAG,Key=acme:environment \
  --query 'ResultsByTime[0].Groups[].{env:Keys[0],usd:Metrics.UnblendedCost.Amount}' --output table
aws ce get-cost-and-usage --time-period "Start=$first,End=$today" --granularity MONTHLY \
  --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE \
  --query 'ResultsByTime[0].Groups[].{service:Keys[0],usd:Metrics.UnblendedCost.Amount}' --output table

aws ce get-anomalies --date-interval "StartDate=$(date -d '-30 days' +%F 2>/dev/null || date -v-30d +%F)" \
  --query 'Anomalies[].{at:AnomalyStartDate,usd:Impact.TotalImpact,service:RootCauses[0].Service}' --output table
```

A cost that moved is a question for the environment tag first (which
one grew), the service dimension second (what grew), and the dashboard
third (why: traffic, a scale-out, a retention that lapsed).

## What this profile cannot do

Change anything. A threshold, a subscription, a scale-out, a retention:
each is a pull request to `deployment/terraform`, applied by the
deployer. A subscription of another address to the alarm topic is the
one write the runbook names, and it is the account's administrator's:

```bash
AWS_PROFILE=acme-staging-admin aws sns subscribe --topic-arn "$(terraform -chdir=deployment/terraform/environments/staging output -raw alarm_topic_arn)" \
  --protocol email --notification-endpoint someone@example.com
```
