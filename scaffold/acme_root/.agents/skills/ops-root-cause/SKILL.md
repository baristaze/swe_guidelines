---
name: ops-root-cause
description: "Find the root cause of one tenant's problem in one environment: read that tenant's rows through the operator plane's read routes with a read-only operator token, correlate them with the logs, the trace, and the error event by request id, and report the cause and the fix. Takes the org id and optionally a user id. Never a database login, never a write, never another tenant's data."
allowed-tools: Read, Grep, Glob, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(uv run:*)
---

# ops-root-cause

The supporter's skill. An investigation ends at "tenant X sees Y"; this
skill opens tenant X, and only tenant X, through the operator plane,
and follows one request id across every signal until the cause is a
line of code, a row, or a resource.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env local|staging|production --org <org_id> [--user <user_id>] [--request-id <id>] [--since 24h]`

`--env` and `--org` are required; ask for them when missing. `--user`
narrows to one member of the tenant. `--request-id` starts from one
request; without it the skill finds the failing requests of the
window in the tenant's events and the error tracker. `--since` is the
window, a day by default.

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
does. Never print a token. On a `401` the token has expired: stop, and
name the refresh the preamble gives.

## Procedure

The processes are `api` and `maintenance`, as `deployment/README.md`
lists them.

1. Verify the credential as Role and credential states. Read
   `GET /v1/admin/me` with the operator token, sourcing the env file
   in the same command as Role and credential shows, and check the
   answer names `operator_role: read`; stop on `write`. No sign-in
   runs: the operator plane admits the token, and no tenant session
   is exchanged.
2. Read the tenant, then its members, through the operator plane's
   read routes, every one under `/v1/admin/orgs/{org_id}/`:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>"
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/members"
   ```

   With `--user`, keep that member alone. A route that answers 403 or
   404 ends the run: the token is not allowed, or the tenant does
   not exist, and neither is guessed around.
3. Read the tenant's activity of the window, the operator's events
   feed:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/events?after_seq=<seq>&limit=200"
   ```

   The operator's feed carries `request_id` and `app` beside the
   actor, which the tenant's own feed leaves out, so it is the map from
   what the tenant did to the requests that did it. The rows themselves
   are the org and its members of step 2. Without `--request-id`, pick
   the request ids of the window's failed or missing writes here and in
   step 4.
4. The error tracker, by request id or by tenant window. One project
   holds the product's errors for every environment, so the read names
   it and asks for this environment:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" --get \
     --data-urlencode "query=environment:<env> request_id:<id>" \
     "$ACME_ERROR_TRACKER_URL/api/0/projects/$ACME_ERROR_TRACKER_ORG/$ACME_ERROR_TRACKER_PROJECT/issues/"
   ```

   An issue the query returns can hold events of another environment
   too, so the event that answers is the one whose `request_id` tag is
   the id **and** whose `environment` tag is `<env>`
   (`/api/0/issues/<issue id>/events/`). An event of another
   environment is never this environment's evidence.

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

   Local: `docker compose -f deployment/local/docker-compose.yml -f
   deployment/local/docker-compose.full.yml logs --since <since> api
   maintenance | grep <id>` from the repository root when the processes
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

   The local reader has no log store of its own: without `--log-file`
   its log leg reports zero lines, which says nothing.

8. Correlate. One request id ties the event row (what the tenant
   asked), the log lines (what the process decided), the trace (where
   it waited), and the error event (where it broke). The cause is the
   first of those that disagrees with the code's intent; read the
   code at the file and line the error names with `Read` and `Grep`.
9. Write the report. The fix is a pull request, a setting, or a
   resource, named; it is never applied here.

## What it never does

- No write to the cloud, no write to the tenant: only `GET` routes of
  the operator plane, only a `read` token.
- No database login: `rds-db:connect` is denied to the role; the
  compose Postgres is not opened either, so `local` proves the same
  path the cloud runs.
- No secret value read or printed: the env file is sourced and never
  read, and no bearer is written to the report.
- No data outside `--org`: no list of orgs, no cross-tenant query, no
  second org id "for comparison".
- No `terraform apply`, no console clicks.

## Output

```markdown
# Root cause: <env>, org <org_id>[, user <user_id>]

**Credential.** <profile and Arn, or local>; operator <email domain only>, READ
**Tenant.** <name>, <members> members, <n> events in the last <since>
**Request.** <request id>, <route>, <status>, <when>

## Timeline

- <time> event: <kind> by <actor id>
- <time> log: <line>
- <time> trace: <span>, <ms>
- <time> error: <exception> at <file>:<line>, or "error event: not read,
  the environment names no error tracker"

## Cause

<one paragraph: what happened, where, and why, with the line of code
or the row or the resource that decided it>

## Fix

<the pull request, setting, or resource change, one sentence each;
nothing applied>
```
