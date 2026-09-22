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

`--env staging` and `--env production` need the investigate profile
of that environment, `acme-<env>-investigate`, which assumes the role
`acme-investigate-<env>`. Before any other command, run

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and check that `Account` is the environment's `account_id` in
`deployment/cloud/environments.json` and that `Arn` reads
`arn:aws:sts::<account>:assumed-role/acme-investigate-<env>/...`.
Refuse any other identity, an administrator profile above all. Every `aws` command below carries
`--profile acme-<env>-investigate`.

The tenant's rows come through the operator plane, never through a
database login: the role denies `rds-db:connect` and holds no database
URL. The operator's credential is the env file's,
`~/.config/acme/ops/<env>.env`, owner-only and outside the
repository: `ACME_API_URL`, `ACME_OPERATOR_TOKEN` (a `read` operator
token; the file's `ACME_PROVISIONER_TOKEN`, a `write` token, belongs
to the traffic generator alone), `ACME_ERROR_TRACKER_URL`,
`ACME_ERROR_TRACKER_TOKEN`, and for `local.env`, which `make seed`
writes, the twins `ACME_PROMETHEUS_URL` and `ACME_JAEGER_URL`. The
token's permission is `read`; a `write` token is refused by this
skill even when the file holds one. The file holds no password and no
TOTP secret: an agent never signs in with a password.

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
3. Read the tenant's activity of the window: the events feed and the
   entity rows the product exposes on the plane:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/events?after_seq=<seq>&limit=200"
   ```

   The operator's feed carries `request_id` and `app` beside the
   actor, which the tenant's own feed leaves out, so it is the map from
   what the tenant did to the requests that did it. The rows themselves
   are `/v1/admin/orgs/<org_id>/<entities>`, one route per entity the
   product exposes on the plane, and `.../members`. Without `--request-id`, pick the request ids of the
   window's failed or missing writes here and in step 4.
4. The error tracker, by request id or by tenant window:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" \
     "$ACME_ERROR_TRACKER_URL/api/0/organizations/<org>/issues/?query=request_id%3A<id>"
   ```

   `<org>` is the organization slug `GET /api/0/organizations/` lists
   (locally `acme`, the one the seed creates):

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_ERROR_TRACKER_TOKEN" "$ACME_ERROR_TRACKER_URL/api/0/organizations/"
   ```

   Local runs the same call against GlitchTip. An event names the
   exception, the file, the line, the release.
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
     --filter-expression 'annotation.request_id = "<id>"'
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
- <time> error: <exception> at <file>:<line>

## Cause

<one paragraph: what happened, where, and why, with the line of code
or the row or the resource that decided it>

## Fix

<the pull request, setting, or resource change, one sentence each;
nothing applied>
```
