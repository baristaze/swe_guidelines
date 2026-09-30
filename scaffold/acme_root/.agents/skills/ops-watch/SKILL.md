---
name: ops-watch
description: "Watch one environment of the platform live from a sub-agent: a log tail, the alarms as they fire, and the error and latency signals, batched per interval and capped, with a read-only credential. Run it in a sub-agent the invoking session spawns, because it polls for the whole window, at most 30 batches, and reports when the window ends, the 30th batch closes, or an alarm fires. It applies the first responder rule: outside production an alarm raised by the team's own traffic may be suppressed with the reason recorded; in production an alarm is never suppressed. Never writes."
allowed-tools: Read, Grep, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(uv run:*), Bash(sleep:*)
---

# ops-watch

A live tail with a cap. The skill polls the logs of one environment
once per interval, reads the alarms every interval, and reports in
batches. It is written to be run by a sub-agent: the invoking session
spawns one with this skill's text, the arguments, and the preamble by
its path from the repository root,
`.agents/skills/_shared/ops-preamble.md`, since the sub-agent has the
text without this skill's folder. The session reads the report when
the agent returns. A session that runs it inline waits for the whole
window.

No command follows a stream. A command that never returns runs into
the shell's time cap, which ends the call and loses the batch. So
every read is a bounded query over one closed interval, and the wait
between two is a `sleep` of the interval, which is capped well under
the time cap.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step; a sub-agent reads
`.agents/skills/_shared/ops-preamble.md` from the repository root. The
profiles, the account check, and the env file are there.

## Input

`--env local|staging|production [--for 15m] [--interval 60s] [--cap 50] [--filter <text>]`

`--env` is required; ask for it when missing. `--for` is the window,
fifteen minutes by default. `--interval` is the batch length, at least
30 seconds, so a batch is worth its calls, and at most five minutes,
so one wait never nears the shell's time cap. A shorter interval is
raised to 30 seconds, and a longer one lowered to five minutes.
`--cap` is the most lines one batch reports; what is over the cap is
counted, not printed. `--filter` narrows the read to lines containing
the text (a request id, a route, a level).

A watch runs at most 30 batches. When `--for` holds more than 30
intervals, the interval is widened to `--for` divided by 30, up to
five minutes: an hour asked at 10 seconds runs 30 batches of two
minutes. The watch ends when the window passes or when its 30th batch
closes, whichever comes first. A window longer than 30 batches of five
minutes ends at the 30th: the watch stops there, and its report names
the part of the window it did not watch.

Spawn it in a sub-agent. The invoker names the window and reads the
report; the sub-agent does the polling.

`local` reads `docker compose logs` over each interval and the twins;
no cloud is needed.

## Role and credential

`--env local` needs the compose stack with the `devx` profile up
(`make devx-up`) and the env file below. No cloud credential.

`--env staging` and `--env production` run under the investigate
profile of that environment, `acme-<env>-investigate`, checked with
`sts get-caller-identity` before any other command as the preamble
states. Refuse any profile wider than the investigate role. A chained
session lasts an hour at most, so the watch passes the profile to
every command and never caches a credential: each batch gets a fresh
session from the person's sign-in. When the sign-in itself has ended,
the watch closes its batch, says the session ended, and returns; it
never asks for a sign-in.

The env file `~/.config/acme/ops/<env>.env` gives the API's URL, the
`read` operator token, and the error tracker's. Never read the env
file; a command that needs a value sources it in the same command, as
every block below does. Never print a token. On a `401` the token has
expired: stop, and name the refresh the preamble gives.

When the env file names no tracker, the batches below are unaffected:
they count requests, 5xx, the p95, and the worker failures out of the
account's own metrics, and anything that would have come from the
tracker is reported as "not read", never as "no errors".

## Procedure

A batch makes at most 20 tool calls. Its main path is six: the wait,
the credential check, one log read per process (two, `api` and
`maintenance`), the alarms, and one `get-metric-data` call with a
query per signal. The bound sits well above that, so a retry of each
read and the reads of step 6 fit in it; only a batch that loops
reaches it. A tree with more than two processes reads all their logs
in one command. Locally there is no credential check, and the
Prometheus queries of step 4 run as one command, as do those of
step 5. A retry counts as a call. A read that would be the 21st call
is not made: the batch stops there, writes that read as not read, and
the watch goes on to the next batch. The first credential check and
the reads of step 2 come before the first batch, and are not counted
in it; the first batch still makes its own check after its wait.

1. Verify the credential as Role and credential states. A chained
   session lasts an hour at most, so every interval reads the profile
   again and checks `sts get-caller-identity`; when the person's
   session behind it has ended, the watch stops and says so in its
   report, rather than retrying on a credential that is gone. Note
   the start time; every batch is
   `[start + k * interval, start + (k + 1) * interval)`, with `k` from
   0 to at most 29, read once that interval has closed, and no batch
   is read twice.
2. Read the platform's size once:

   ```bash
   uv run acme-ops size --env <env>
   ```

   Keep the numbers and how long ago the worker counted them; the
   first responder rule of step 6 reads them. The worker counts every
   five minutes, so a count older than ten minutes is itself a finding.

   In the cloud, read the load balancer's ARN once too; step 5 reads
   the p95 from it:

   ```bash
   aws elbv2 describe-load-balancers --names acme-<env> \
     --query 'LoadBalancers[0].LoadBalancerArn' --output text \
     --profile acme-<env>-investigate
   ```

   `<load balancer>` in step 5 is what follows `loadbalancer/` in that
   ARN, `app/acme-<env>/<id>`.
3. Each interval, wait for it to close, then read its lines. The
   wait:

   ```bash
   sleep <interval in seconds>
   ```

   The read, cloud, one query per process (`api` and `maintenance`,
   the log groups `/acme/<env>/api` and `/acme/<env>/maintenance`),
   bounded by the batch's start and end in epoch milliseconds, never
   `--follow`:

   ```bash
   aws logs filter-log-events --log-group-name /acme/<env>/api \
     --start-time <batch start> --end-time <batch end> \
     --filter-pattern '<filter>' --max-items <cap + 1> \
     --profile acme-<env>-investigate
   ```

   Local, the same interval, never `-f`:

   ```bash
   docker compose -f deployment/local/docker-compose.yml logs \
     --since <batch start, RFC 3339> --until <batch end, RFC 3339>
   ```

   from the repository root (add `-f deployment/local/docker-compose.full.yml`
   when the application runs in containers), together with the lines
   of the interval from the file a host process was started with;
   `scripts/dev.sh` writes no file, it logs to its terminal.
4. Each interval, read the alarms. Cloud:

   ```bash
   aws cloudwatch describe-alarms --alarm-name-prefix acme-<env>- \
     --state-value ALARM --profile acme-<env>-investigate
   ```

   Local: the ten alarm conditions of `ops-investigate` as queries against
   `$ACME_PROMETHEUS_URL/api/v1/query`. An alarm that was already in
   `ALARM` in the last batch is not reported again; a transition
   (`OK` to `ALARM`, `ALARM` to `OK`) is.
5. Each interval, read one number per signal for that interval and
   nothing more: the request count, the 5xx count, the p95, the
   worker failures, through `get-metric-data`, or the same as a
   Prometheus range query. A datapoint is a whole minute, so the two
   bounds a batch passes are its start and its end, each rounded down
   to a whole minute. Two batches in a row then split the minutes
   between them, no minute is read twice, and each is read once it is
   complete. A batch whose rounded start and end are the same minute
   (a 30-second batch can be) makes no metric read; the next batch
   reads that minute. Such a batch writes "metrics read in the next
   batch" in place of its numbers, never a zero, which would read as
   traffic stopping. The period of each `get-metric-data` query, its
   `Period` and the last argument of a `SEARCH` expression, is a whole
   minute, 60 seconds, whatever the batch interval: CloudWatch refuses
   a period that is not a multiple of 60 for a regular-resolution
   metric, and a longer one would reach past the batch's rounded end
   into the next batch's minutes.

   Cloud, one call reads the four, between the two rounded bounds in
   epoch seconds:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <rounded start> --end-time <rounded end> \
     --query "{requests: sum(MetricDataResults[?Id=='req'].Values[]), server_errors: sum(MetricDataResults[?Id=='by' && starts_with(Label, '5')].Values[]), p95_seconds: max(MetricDataResults[?Id=='p95'].Values[]), worker_failures: sum(MetricDataResults[?Id=='fail'].Values[])}" \
     --metric-data-queries '[
       {"Id":"req","Period":60,"Expression":"SUM(SEARCH('"'"'{\"Acme\",OTelLib,environment,method,route,service,status} MetricName=\"acme_http_requests_total\" environment=\"<env>\"'"'"', '"'"'Sum'"'"', 60))"},
       {"Id":"by","Period":60,"Label":"${PROP('"'"'Dim.status'"'"')}","Expression":"SEARCH('"'"'{\"Acme\",OTelLib,environment,method,route,service,status} MetricName=\"acme_http_requests_total\" environment=\"<env>\"'"'"', '"'"'Sum'"'"', 60)"},
       {"Id":"p95","Label":"p95","MetricStat":{"Metric":{"Namespace":"AWS/ApplicationELB","MetricName":"TargetResponseTime","Dimensions":[{"Name":"LoadBalancer","Value":"<load balancer>"}]},"Period":60,"Stat":"p95"}},
       {"Id":"fail","Period":60,"Expression":"SUM(SEARCH('"'"'{\"Acme\",OTelLib,environment,outcome,service,subsystem} MetricName=\"acme_outcomes_total\" environment=\"<env>\" subsystem=\"worker\" (outcome=\"failed\" OR outcome=\"refused\")'"'"', '"'"'Sum'"'"', 60))"}
     ]'
   ```

   It prints the four numbers of the batch. `requests` is every series
   of the app's request counter, and `server_errors` the ones whose
   status starts with `5`. `p95_seconds` is the load balancer's target
   response time over every route, at its highest minute, and `null`
   when no request crossed it. `worker_failures` counts the worker's
   `failed` and `refused` outcomes: an attempt a handler failed, and
   an item it refused for good. The two schemas are the dashboard's
   own (`deployment/terraform/modules/dashboard/`): one that leaves
   out a dimension the series carry, `OTelLib` among them, matches
   nothing and reads as a zero, so keep them as written.

   Locally, a count is `increase(<metric>[1m])` and the p95 is
   `histogram_quantile(0.95, sum by (le) (increase(acme_http_request_seconds_bucket[1m])))`,
   each a range query from the rounded start plus 60 seconds to the
   rounded end, at a 60-second `step`: each point is one complete
   minute of the batch. The three counts are the cloud's, each summed
   over its series: `acme_http_requests_total`, the same with
   `status=~"5.."`, and
   `acme_outcomes_total{subsystem="worker",outcome=~"failed|refused"}`.
   A batch's count is the sum of its datapoints,
   and its p95 the highest among them. A burst is a count in the
   batch, never a line per event: the batch's lines over `--cap` are
   counted by level and dropped.
6. The first responder rule. In production every alarm transition is
   an escalation. Outside production it is read against the size of
   step 2, and it is suppressed only when the traffic is the team's
   own: one tenant and one user, and that user is the team's. Then the
   alarm is written into the batch as suppressed, with the reason and
   the size, and the watch goes on. Anything else, and the alarm is an
   escalation: the batch is closed early, the report is written
   with the alarm at the top, and the sub-agent returns so the
   invoker can act.
7. When the window passes or the 30th batch closes, write the report
   with every batch in order. The sub-agent names its Next and never
   runs it; the invoking session decides. A session follows at most 2
   hops of Next. The skill it starts with is hop zero; the report of
   the second hop still names its next skill, and the session stops
   there and reports.

## What it never does

- No write to the cloud, no acknowledgement or silence of an alarm,
  no change to a log group.
- No secret value printed, no line printed that carries one (a line
  with a bearer or a password field is reported as its count).
- No tenant data outside the signals: the logs carry ids, never rows.
- No `terraform apply`, no console clicks.
- No unbounded output: never more than `--cap` lines per batch, never
  the same window twice, never a read past `--for`.
- No unbounded loop: never more than 30 batches, never a batch
  shorter than 30 seconds, never more than 20 tool calls in a batch.
- No command that does not return: no `--follow`, no `-f`, no wait
  longer than one interval.

## Output

```markdown
# Watch: <env>, <start> to <end>, every <interval>

**Credential.** <profile and the Arn it resolved to, or local>
**Size.** <tenants> tenants, <users> users, <n> events in the last day, counted <age> ago
**Ended.** <window passed | 30th batch, <start> to <end> not watched | escalated on <alarm> at <time> | credential ended at <time>>

## Alarms

- <time> <alarm name>: <OK to ALARM | ALARM to OK>, <suppressed: reason | escalated>

## Batches

- <start of batch>: <<requests> requests, <5xx> 5xx, p95 <ms>, <failures> worker failures | metrics read in the next batch>; <lines> lines shown, <dropped> over the cap (<by level>)
  - <line>
  - <line>

## Findings

- <what moved, one sentence, with the request id that proves it>

## Next

<the skill to run next, with its arguments, or "nothing">
```
