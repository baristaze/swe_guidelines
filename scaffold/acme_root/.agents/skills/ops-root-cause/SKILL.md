---
name: ops-root-cause
description: "Find the root cause of one tenant's problem in one environment: read that tenant's rows through the operator plane's read routes with a read-only operator token, correlate them with the logs, the trace, and the error event by request id, at most five ids and one pass each, and report the cause and the fix, or that none was found. Takes the org id and optionally a user id. Never a database login, never a write, never another tenant's data."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(curl:*), Bash(jq:*), Bash(docker compose:*), Bash(uv run acme-ops:*), Bash(sleep:*)
---

# ops-root-cause

The supporter's skill. An investigation ends at "tenant X sees Y"; this
skill opens tenant X, and only tenant X, through the operator plane,
and follows each request id, at most five, once across every signal.
The cause it names is a line of code, a row, or a resource, or it
reports "not found".

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env local|staging|production --org <org_id> [--user <user_id>] [--request-id <id>]... [--since 24h]`

`--env` and `--org` are required; ask for them when missing. `--user`
narrows to one member of the tenant. `--request-id` starts from a
request, and may be given more than once; without it the skill finds
the failing requests of the window in the tenant's events (step 3),
the one signal read by tenant: the error tracker holds no tenant
(step 4). `--since` is the window, a day by default.

A run follows at most 5 request ids, one pass each: the first five
given, in the order given, or without `--request-id`, the five newest
requests whose events step 3 ties to the symptom (the Y of "tenant X
sees Y"), never the newest failures of any kind. An event tied to the
symptom is a failure the stream records or a write that landed, and
either request is followed, so a run whose only such events are
writes that landed still makes its passes: a request can land its
write and fail its answer. Without `--request-id`, the
symptom is the one the prompt or the investigation's report names;
when neither names one, ask for it, as for `--env`. A pass that finds
no cause reports "not found" for its id. After the fifth pass the
skill stops and writes the report. It lists every id past the fifth
as not followed, for a second run to take.

Without `--request-id`, when the window's events hold no request tied
to the symptom, the run makes no pass and writes the report. Its Requests line says none was
found, and its Cause says "not found", with what the feed held. The
report then names the second run that could find one: with the
request id the tenant saw as `--request-id`, which every error answer
carries as `error.request_id`, or with a longer `--since`. The run
never widens the window itself, and never takes a failure the symptom
does not name.

`local` reads the compose stack and its twins; no cloud is needed.

## Role and credential

`--env local` needs the compose stack with the `devx` profile up
(`make devx-up`) and the env file below. No cloud credential.

`--env staging` and `--env production` run under the investigate
profile of that environment, `acme-<env>-investigate`, checked with
`sts get-caller-identity` before any other command as the preamble
states. Refuse any profile wider than the investigate role. Every
`aws` command below carries `--profile acme-<env>-investigate`.

The tenant's rows come through the operator plane, never through a
database login: the role denies `rds-db:connect` and holds no database
URL. The credential for the plane is the env file's operator token,
whose permission is `read`; a `write` token is refused by this skill
even when the file holds one. Never read the env file; a command that
needs a value sources it in the same command, as every block below
does. Never print a token. On a `401`, which a read through its `jq`
prints as the error code `not_authenticated`, the token has expired:
stop, and name the refresh the preamble gives.

## Procedure

The processes are `api` and `maintenance`, as `deployment/README.md`
lists them.

Steps 4 to 8 are one pass, for one request id, and each id gets one
pass. A pass reads each signal once: a signal that answers nothing is
written as empty, never read a second time with a wider window or
another filter. Without `--request-id`, the pick of ids in step 3
runs once, before the passes. It is not a pass.

1. Verify the credential as Role and credential states, then read who
   the operator plane admitted. The `jq` keeps the role and the domain
   of the operator's address, never the address:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/me" \
     | jq '{operator_role, email_domain: (.email // "" | split("@")[1]), error: .error.code}'
   ```

   The run goes on only on `operator_role: read`. A `write` stops it,
   and so do an `error` and an answer that is empty or not JSON, as in
   step 2. No sign-in runs: the operator plane admits the token, and
   no tenant session is exchanged.
2. Read the tenant, then its members, through the operator plane's
   read routes, every one under `/v1/admin/orgs/{org_id}/`:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>" \
     | jq '{id, kind, created_at, deleted_at, error: .error.code}'
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/members" \
     | jq '{members: [.items[]? | {id, created_at}], next_cursor, error: .error.code}'
   ```

   Each read keeps the ids, the kind, and the timestamps, and drops
   what the tenant wrote: the org's `name` and `slug`, a member's
   `display_name` and `email`. Those are a tenant's own words, and
   this session holds an operator's token, so they never reach it:
   never run either read without its `jq`, and never print a whole
   answer. The report names the tenant by its id.

   A page holds at most 50 members. When `next_cursor` is not null,
   read the next page with it as `cursor`, through the same `jq`:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/members?cursor=<next_cursor>" \
     | jq '{members: [.items[]? | {id, created_at}], next_cursor, error: .error.code}'
   ```

   Without `--user`, read on until `next_cursor` is null: the member
   count is the sum of the pages. With `--user`, stop at the page that
   holds that member, and keep that member alone. Read at most 20 pages
   of members, 1,000 of them. After the twentieth the read stops: the
   report says "more than 1,000 members", and with `--user`, that the
   member was not among the first 1,000.

   `error` is null on an answer and the refusal's code otherwise. A
   route that answers 403 or 404 ends the run: the token is not
   allowed, or the tenant does not exist, and neither is guessed
   around.

   An answer that is empty, or that `jq` cannot parse, is no answer.
   `curl -s` prints nothing when the API is out of reach, and `jq`
   then prints nothing and exits 0. A parse error means the answer was
   not JSON, such as a proxy's error page. Either way the run ends
   there, as on a refusal: the report says the operator plane was not
   read, and which of the two it was. The read is not made a second
   time.
3. Read the tenant's activity of the window, the operator's events
   feed. The feed answers a bare list of events, never an object that
   wraps one, and a refusal with the error object. The operator's feed
   carries `request_id` and `app` beside the actor, which the tenant's
   own feed leaves out, so it is the map from what the tenant did to
   the requests that did it. The rows themselves are the org and its
   members of step 2.

   The feed reads only forward from `after_seq`, with no time filter,
   so the skill first finds the window's first `seq`, and never reads
   from `after_seq=0` unless the tenant's first event is inside the
   window. An event's time is its `produced_at`. The window ends when
   this step starts and begins `--since` before it, both read once,
   here, and every later step reads the same two:

   ```bash
   jq -nc 'now | floor | {start: (. - <since in seconds>), end: .} | .start_at = (.start | todate) | .end_at = (.end | todate)'
   ```

   `start` and `end` are epoch seconds, for the cloud's reads;
   `start_at` and `end_at` are RFC 3339. Probe with `limit=1`, one read
   per probe:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/events?after_seq=<n>&limit=1" \
     | jq -c 'if type == "array" then {seq: .[0].seq, produced_at: .[0].produced_at} else {error: .error.code} end'
   ```

   A probe prints the event's `seq` and `produced_at`, both null when
   none is returned. Probe `after_seq=0`, then 1, 2, 4, 8, doubling,
   until the event returned is inside the window or none is returned.
   Then bisect between the last probe before the window and the first
   one inside it or past the last event, until the two are one apart.
   The probes take about twice the base-2 log of the tenant's event
   count: about 28 calls for 10,000 events, about 40 for a million.

   Read the feed forward from `after_seq` at the later of the two, 200
   events a page:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/events?after_seq=<seq>&limit=200" \
     | jq -c 'if type == "array" then {count: length, last_seq: .[-1].seq, events: [.[] | {seq, produced_at, kind, target_id, actor_id, request_id, app}]} else {error: .error.code} end'
   ```

   The next page reads from the page's `last_seq`, until a page's
   `count` is under 200: the window bounds the read, and no page count
   cuts it. A read of this step that answers an `error`, or an answer
   that is empty or not JSON, ends the run as in step 2.

   Without `--request-id`, pick the request ids here, at most five, the
   newest first among the events tied to the symptom. A failure the
   stream records is `work.item.failed` or `outbox.row.failed`: a work
   item or an outbox row that failed for good. Any other event tied to
   the symptom is a write that landed, whose request is followed for
   what it did next. When no event is tied to the symptom, the run
   makes no pass, as Input says.
4. The error tracker, by the pass's request id. The tracker holds no
   tenant: an event's tags are `service`, `request_id`, and the SDK's
   own (`environment`, `release`, `server_name`), never an org id, so
   it is never read by the tenant or by a tag guessed for one. One
   project holds the product's errors for every environment, so the
   read names it and asks for this environment. An issue's `title`,
   `culprit`, and `metadata` carry the exception's text, which can
   quote what the tenant sent, so the `jq` keeps the issue's `id`,
   `lastSeen`, and `count` alone:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" --get \
     --data-urlencode "query=environment:<env> request_id:<id>" \
     "$ACME_ERROR_TRACKER_URL/api/0/projects/$ACME_ERROR_TRACKER_ORG/$ACME_ERROR_TRACKER_PROJECT/issues/" \
     | jq -c 'if type == "array" then {issues: [.[] | {id, lastSeen, count}]} else {error: (.detail // "not a list")} end'
   ```

   An issue the query returns can hold events of another environment
   too, so the event that answers is the one whose `request_id` tag is
   the id **and** whose `environment` tag is `<env>`. An event of
   another environment is never this environment's evidence. Read each
   issue's events for this environment and this id, with `full=true`,
   without which Sentry answers no stack. A tracker that ignores the
   two filters answers a page of the issue's events, and the `jq` keeps
   the one event alone: its exception's type and the last three frames
   of the product's code, never the exception's text:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" --get \
     --data-urlencode "environment=<env>" --data-urlencode "query=request_id:<id>" \
     --data-urlencode "full=true" \
     "$ACME_ERROR_TRACKER_URL/api/0/issues/<issue id>/events/" \
     | jq -c 'if type == "array" then {events: [.[] | (.tags | if type == "array" then map({(.key): .value}) | add else . end) as $tag | select($tag.request_id == "<id>" and $tag.environment == "<env>") | {id: (.eventID // .event_id), at: (.dateCreated // .date_created), release: $tag.release, exception: [.entries[]? | select(.type == "exception") | .data.values[]? | {type, frames: ([.stacktrace.frames[]? | select(.inApp) | {filename, lineNo, function}] | .[-3:])}]}]} else {error: (.detail // "not a list")} end'
   ```

   A tracker read that answers an `error`, or an answer that is empty
   or not JSON, does not end the run, as a read of the operator plane
   does in step 3: the tracker is one leg of the pass. The pass writes
   its error leg as "not read", with which of the three it was, and
   goes on to the logs; the read is not made a second time. An answer
   with no issue, or no event of this id, is "no error event".

   The org's slug is what `GET /api/0/organizations/` lists (locally
   `acme`, the one the seed creates):

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" "$ACME_ERROR_TRACKER_URL/api/0/organizations/"
   ```

   Local runs the same call against GlitchTip. An event names the
   exception, the file, the line, the release.

   When the env file names no tracker, this step reads nothing and the
   report says "not read", never "no error event". The tenant's
   events, the logs, and the trace still carry the request id, and the
   cause is found in them.
5. The logs, by request id. Cloud:

   ```bash
   aws logs start-query --profile acme-<env>-investigate \
     --log-group-names /acme/<env>/api /acme/<env>/maintenance \
     --start-time <start> --end-time <end> \
     --query-string 'fields @timestamp, level, @message | filter request_id = "<id>" or caused_by_request_id = "<id>" | sort @timestamp asc'
   aws logs get-query-results --query-id <id> --profile acme-<env>-investigate
   ```

   Poll `get-query-results` at most 10 times for one query, each poll
   after `sleep 5` in the same command, so ten polls cover about a
   minute. When its status is still `Scheduled` or `Running` after
   the tenth, stop polling: the pass writes its log leg as "not read:
   the query did not finish in 10 polls", with the query id, and goes
   on to the trace.

   Local: `docker compose -f deployment/local/docker-compose.yml -f
   deployment/local/docker-compose.full.yml logs --since <start_at>
   --until <end_at> api maintenance | grep <id>`, the window's two
   bounds of step 3, from the repository root when the processes
   run in containers. When they run on the host (`scripts/dev.sh`
   writes no file; it logs to its terminal), `grep` the file the
   process was started with, and say "not read" when there is none.
   A local line carries the request id in brackets, `[<id>]`, or as
   `"request_id"` when `ACME_LOG_JSON` is on.
   `caused_by_request_id` follows the request across a handoff into a
   worker.
6. The trace, by request id. Cloud:

   ```bash
   aws xray get-trace-summaries --profile acme-<env>-investigate \
     --start-time <start> --end-time <end> \
     --filter-expression 'annotation.acme_request_id = "<id>"'
   ```

   Local, Jaeger's v3 API (the service is the process name, `api`, and
   the request id is the span attribute `acme.request_id`):

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -sG "$ACME_JAEGER_URL/api/v3/traces" \
     --data-urlencode query.service_name=api \
     --data-urlencode "query.start_time_min=<start, RFC 3339>" \
     --data-urlencode "query.start_time_max=<end, RFC 3339>"
   ```

   and filter the spans on the attribute client-side; the query API
   ignores attribute filters. An empty answer means the process ran
   with no `ACME_OTEL_ENDPOINT`, which is a finding, not an error.
   The trace says where the time went and which span failed.
7. Or in one call, the same four reads through the platform's own
   binary, which holds the twins and the cloud behind one interface:

   ```bash
   uv run acme-ops signals check --env <env> --request-id <id> \
     [--log-file <the process's log>] [--since-minutes <n>]
   ```

   It reads back from now, so `<n>` is the minutes from the window's
   `start` of step 3 to now, rounded up, and its read covers the
   window: `jq -n '(now - <start>) / 60 | ceil'`. The local reader has
   no log store of its own: without `--log-file` its log leg reports
   zero lines, which says nothing.

8. Correlate. One request id ties the event row (what the tenant
   asked), the log lines (what the process decided), the trace (where
   it waited), and the error event (where it broke). The cause is the
   first of those that disagrees with the code's intent; read the
   code at the file and line the error names with `Read` and `Grep`.
   When none disagrees, the pass ends with "not found" for its id,
   naming each leg it could not read, and the next id's pass starts.
9. Write the report once the last pass ends, the fifth at most. The
   fix is a pull request, a setting, or a resource, named; it is never
   applied here.

## What it never does

- No write to the cloud, no write to the tenant: only `GET` routes of
  the operator plane, only a `read` token.
- No database login: `rds-db:connect` is denied to the role; the
  compose Postgres is not opened either, so `local` proves the same
  path the cloud runs.
- No secret value read or printed: the env file is sourced and never
  read, and no bearer is written to the report.
- No text a tenant wrote read: an org's name or slug, a member's
  display name or address. The reads of step 2 drop them.
- No data outside `--org`: no list of orgs, no cross-tenant query, no
  second org id "for comparison".
- No `terraform apply`, no console clicks.
- No unbounded search: never more than 5 request ids, never a second
  pass over one, never more than 10 polls of a query, never a page of
  the feed read from before the window's first `seq` (the one-event
  probes that find it aside), never more than 20 pages of members.

## Output

```markdown
# Root cause: <env>, org <org_id>[, user <user_id>]

**Credential.** <profile and Arn, or local>; operator <email domain only>, READ
**Tenant.** <kind> org, <members> members, <n> events in the last <since>, from seq <seq> (<p> probes)
**Requests.** <n> given or found, <m> followed (at most 5), or none
found in the window's events

- <request id>, <route>, <status>, <when>: <cause found | not found>
- <request id>: not followed, past the fifth

## Timeline

- <time> event: <kind> by <actor id>
- <time> log: <line>
- <time> trace: <span>, <ms>
- <time> error: <exception> at <file>:<line>, or "error event: not read,
  the environment names no error tracker"

## Cause

<one paragraph: what happened, where, and why, with the line of code
or the row or the resource that decided it; or "not found", with what
each pass read; or, with no pass, what the feed held and the second
run that could find a request>

## Fix

<the pull request, setting, or resource change, one sentence each;
nothing applied>
```
