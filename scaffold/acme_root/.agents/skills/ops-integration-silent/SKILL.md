---
name: ops-integration-silent
description: "Say why an integration went silent, in one environment, with a read-only credential: count what its inbound webhook route answered, read what the worker made of each delivery and the lines it logged, and read the webhooks queue and its dead letters, then report where the deliveries stop, why, and what to do next. Every read goes through the signals' own APIs (CloudWatch and SQS in the cloud; the compose stack's logs, Prometheus, and ElasticMQ locally). Never a delivery's body, never a tenant's rows, never a write."
allowed-tools: Read, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(jq:*), Bash(sleep:*)
---

# ops-integration-silent

The investigator's skill for an integration that went quiet. A
provider's events arrive as signed deliveries at an inbound webhook
route. The route checks the signature and the timestamp and queues the
delivery on the `webhooks` queue. The maintenance worker receives it,
finds the org it names, and applies it there. A delivery that keeps
failing comes back after its visibility, and after its last receive,
the fifth by default, the queue moves it to its dead letters. So a delivery stops in one of five
places: it never arrives, the route refuses it, it waits on the queue,
the worker fails it, or the worker drops it. This skill reads each
place and says which one holds the deliveries, and why.

The integration the tree holds is the identity provider's, at
`/webhooks/identity`. An integration the tree adds has its own route,
and only the route's reads, step 2's, are its own: the worker's counter
carries only the subsystem and the outcome, its failure and drop lines
name no provider, and every integration shares the `webhooks` queue and
its dead letters.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles and the account check are there.

## Input

`--env local|staging|production [--route /webhooks/identity] [--since 6h]`

`--env` is required; ask for it when missing. `--route` is the
integration's route, the identity provider's by default. `--since` is
the window, a duration ending now, six hours by default.

## Role and credential

The investigator: every signal, no secret, and no tenant's rows. The
skill sources no env file and holds no operator token.

`--env local` reads the compose stack through `docker compose`, from
the repository root: the processes' logs, Prometheus in the `devx`
profile (`make devx-up`, or `make up`), and ElasticMQ. No cloud
credential.

`--env staging` and `--env production` run under the investigate
profile of that environment, `acme-<env>-investigate`, checked with
`sts get-caller-identity` before any other command as the preamble
states. Refuse any profile wider than the investigate role. Every
`aws` command below carries `--profile acme-<env>-investigate`.

## Procedure

In the cloud the route's lines are in `/acme/<env>/api`, the worker's
in `/acme/<env>/maintenance`, and the queue is `acme-<env>-webhooks`
with its dead letters in `acme-<env>-webhooks-dead`. Locally the queue
is `acme-webhooks`, with `acme-webhooks-dead`.

1. In the cloud, check the profile as Role and credential states,
   before any other command, and compute the window as epoch seconds:
   `--since` back from now. Locally there is no profile to check; ask
   which processes run in a container:

   ```bash
   docker compose -f deployment/local/docker-compose.yml -f deployment/local/docker-compose.full.yml \
     ps --services --status running
   ```

   An `api` or a `maintenance` this does not list runs on the host, and
   its log is read as the end of step 3 says.
2. The route: what it answered, by status, over the window. Every
   request writes one access line, `POST <route> <status> <ms>`, which
   holds no body. Locally:

   ```bash
   docker compose -f deployment/local/docker-compose.yml -f deployment/local/docker-compose.full.yml \
     logs --no-log-prefix --since <since> api | grep -oE 'POST <route> [0-9]{3}' | sort | uniq -c
   docker compose -f deployment/local/docker-compose.yml -f deployment/local/docker-compose.full.yml \
     logs --no-log-prefix --since <since> api | grep -oE '[a-z_]+ on POST <route>: [^"]*' | sort | uniq -c
   ```

   In the cloud, two Logs Insights queries of the API's group:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api \
     --start-time <start> --end-time <end> \
     --query-string 'filter http.route = "<route>" | stats count(*) as answered by http.status'
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, request_id, message | filter @message like "on POST <route>:" | sort @timestamp desc | limit 20'
   aws logs get-query-results --query-id <id> --profile acme-<env>-investigate
   ```

   Poll `get-query-results` at most 10 times for one query, each poll
   after `sleep 5` in the same command. When the status is still
   `Scheduled` or `Running` after the tenth, stop polling, and the
   report writes that read as "not read: the query did not finish in
   10 polls", with the query id. Step 3's query is polled the same way.
   A `4xx` refusal has no line of its own: the status is all the log
   holds of it. A `5xx` writes one, `<code> on POST <route>: <message>`
   with its exception, at `WARNING` or `ERROR`, and the second command
   of each block reads it.
3. The worker: what it made of each delivery. It counts each one as
   `acme_outcomes_total{subsystem="deliveries"}`, by outcome: `applied`
   and `duplicate` (applied now, or before), `unowned` (it names no
   org, or an org that is gone) and `malformed` (not a delivery), both
   dropped, and `failed` and `unknown_provider`, which stay on the
   queue to come back. `receive_failed` is a receive that never reached
   the queue. Locally, Prometheus through the port compose gives it:

   ```bash
   prom="http://$(docker compose -f deployment/local/docker-compose.yml port prometheus 9090)"
   curl -sG "$prom/api/v1/query" \
     --data-urlencode 'query=max by (outcome) (acme_outcomes_total{subsystem="deliveries"})'
   curl -sG "$prom/api/v1/query" \
     --data-urlencode 'query=process_start_time_seconds{job="maintenance"}'
   ```

   A local counter is read whole: it counts from the worker's start,
   which the second query gives, and the report says so. `increase()`
   is not used locally: a series born inside the window, as one failed
   delivery's is, has no earlier sample, so `increase()` misses its
   first count, and a single failure reads as none. In the cloud, the
   counter by the dashboard's schema:

   ```bash
   aws cloudwatch get-metric-data --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> --output text \
     --query 'MetricDataResults[?length(Values) > `0`].[Id,Label,sum(Values)]' \
     --metric-data-queries '[{"Id":"out","Period":60,"Label":"${PROP('"'"'Dim.outcome'"'"')}","Expression":"SEARCH('"'"'{\"Acme\",OTelLib,environment,outcome,service,subsystem} MetricName=\"acme_outcomes_total\" environment=\"<env>\" subsystem=\"deliveries\"'"'"', '"'"'Sum'"'"', 60)"}]'
   ```

   An `out` line is one outcome, and its sum is the window's count.
   The counter is the shape; the worker's lines are the record. Each
   failure is an `ERROR` line, `delivery <message id> failed on receive
   <n>`, with the exception under it; each drop names its reason
   (`names no org`, `which is gone`, `is not a delivery`, `which this
   worker lacks`), and a receive that failed says `receiving deliveries
   failed`. Locally, each line with the exception's own lines and not
   its frames:

   ```bash
   docker compose -f deployment/local/docker-compose.yml -f deployment/local/docker-compose.full.yml \
     logs --no-log-prefix --since <since> maintenance \
     | jq -cR 'fromjson? | select(.message | test("deliver|worker lacks")) | {ts, request_id, message, raised: ((.exception // "") | split("\n") | map(select(test("^[A-Za-z_][\\w.]*(: |$)"))))}'
   ```

   In the cloud:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, request_id, message, exception | filter message like /deliver|worker lacks/ | sort @timestamp desc | limit 50'
   ```

   A process step 1 does not list runs outside its container
   (`scripts/dev.sh`, which logs to its terminal and writes no file):
   its container's log is empty whatever it did, so read the file it
   was started with by the same patterns of steps 2 and 3, and write
   "not read" when there is none, never "none in the window".
4. The queue and its dead letters, as they stand now: the deliveries
   waiting, the ones a receive holds, and the ones that failed every
   receive. Locally, ElasticMQ's own API inside its container:

   ```bash
   for q in webhooks webhooks-dead; do
     docker compose -f deployment/local/docker-compose.yml exec -T elasticmq wget -q -O- \
       "http://127.0.0.1:9324/queue/acme-$q?Action=GetQueueAttributes&AttributeName.1=ApproximateNumberOfMessages&AttributeName.2=ApproximateNumberOfMessagesNotVisible"
   done
   ```

   In the cloud:

   ```bash
   for q in webhooks webhooks-dead; do
     url="$(aws sqs get-queue-url --queue-name acme-<env>-$q \
       --query QueueUrl --output text --profile acme-<env>-investigate)"
     aws sqs get-queue-attributes --queue-url "$url" --profile acme-<env>-investigate \
       --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
   done
   ```

   `ApproximateNumberOfMessages` is the visible count, and
   `ApproximateNumberOfMessagesNotVisible` the in-flight one.

   Each read of steps 2 to 4 is made once. One that answers nothing is
   "none in the window", never read again with a wider window; locally,
   only when step 1 lists its process, since a log of a process it does
   not list is read as the end of step 3 says. One that answers an
   error, or nothing a reader can parse, is "not read", naming it.
5. Find where the deliveries stop. Read the rows top to bottom, and
   report every row whose condition holds, in this order, each with the
   counts that decided it. When the tree holds an integration besides
   the identity provider's, "Fails in the worker" and "Dropped by the
   worker" are reported as the queue's, not the route's, unless one of
   the worker's lines names the provider:

   | Where | Condition | Why | Next |
   |---|---|---|---|
   | Never arrives | The route answered nothing in the window | The provider stopped sending, or sends to another address; its own dashboard says which | the provider's endpoint and its state, by a person |
   | Refused at the route | `400` answers | The signature, its timestamp, or the body did not check out, and nothing was queued. Most or all of them refused: the signing secret differs between the provider and the environment. A few among `2xx`: a replay past the three-minute window, or a delivery the provider did not sign | every one or most: the route's signing secret (the identity provider's is `ACME_WORKOS_WEBHOOK_SECRET`, as `docs/runbooks/providers/workos.md` sets it), by a person; a few: nothing |
   | Refused at the route | `503` or another `5xx` | The route could not check a delivery or could not queue it, and its `on POST <route>:` line gives the reason, such as no signing secret, no provider configured, or the queue refusing the send. A local stack with no provider configured answers `503` by design | `ops-investigate` over the same window |
   | Waits on the queue | `2xx` answers, the queue's visible count above 0, and few or no worker outcomes, or `receive_failed` | The worker is not running, or cannot reach the queue | `ops-investigate` over the same window |
   | Fails in the worker | `failed` or `unknown_provider`, or dead letters above 0 | The worker received the delivery and could not apply it: the exception under its `failed on receive` line names what refused it. `unknown_provider` is a provider this worker does not know, such as a worker older than the API. A dead letter is a delivery that failed every receive | `ops-investigate --request-id <the request id of the last failed receive>` |
   | Dropped by the worker | `unowned` or `malformed` | The delivery names an org this environment does not hold, or is not a delivery: another environment's organizations delivering here, or an org deleted | every one: the provider's endpoint, by a person; a few: nothing |

   When no row holds and the worker counts `applied` or `duplicate`,
   the deliveries reach their orgs: the silence is past this
   integration, in what reads the applied events, and Next is
   `ops-investigate`.
6. Write the report. A run follows at most 2 hops of Next: this skill
   is hop zero, the report of the second hop still names its next
   skill, and the run stops there and reports.

## What it never does

- No write: no redrive of the dead letters, no resend, no purge of the
  queue, no change of a secret. A dead letter goes back to the queue by
  a person, once its cause is fixed.
- No delivery's body: not a message of the queue or of the dead
  letters, and not a request's. The access lines, the counts, and the
  worker's lines are enough.
- No tenant's rows: the operator plane is not called.
- No secret value read or printed; no env file sourced.
- No `aws` verb that is not `get`, `describe`, `list`, `start-query`,
  or `get-query-results`.
- No read made twice, no wider window than `--since`, and never more
  than 10 polls of a query.

## Output

```markdown
# Integration silent: <env>, <route>, the last <since>

**Credential.** <profile and the Arn it resolved to, or local>
**Route.** <status: count, ...>, or none in the window, or not read: <why>
**Worker.** <outcome: count, ...> (locally: since <worker start>), or not read: <why>
**Queue.** webhooks <visible> visible, <in flight> in flight; dead letters <n>, or not read: <why>

## Where the deliveries stop

- <where>: <the counts that decided it>. <why, with the line that says it: its time, its request id, and the exception's own lines>

## Next

<the next step of each row that holds, or "nothing">
```
