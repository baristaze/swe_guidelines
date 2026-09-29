---
name: audit-provider-calls
description: "Audit every call to an external provider (WorkOS, Stripe, Slack, the AWS services, any HTTP to a third party), per flow: each route, inbound webhook, worker job, and boot. For each flow, the calls in order, whether they repeat or stand apart, whether they sit on the request path, how often the flow runs, whether the client is reused, the timeout times the retries, and whether the request has an overall deadline. Counts the calls through the provider twins where it can and reads the code where it cannot, then ranks fixes: remove a call, fold calls, move one off the request path, cache it, and run in parallel last. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(uv run:*), Bash(git:*), Bash(mkdir:*)
---

# audit-provider-calls

A call to a provider is the slowest thing a request does and the one
the platform controls least. This audit lists every one, flow by flow,
says what each costs a request in the worst case, and ranks what to do
about it.

## Input

`[--only <flow,...>]`

`--only` limits the counted run to some of the built-in flows, as
`audit-database-calls` takes it; the reading of the code covers every
flow all the same. The audit reads the checkout, tools and code alike;
to audit another commit, run it from a checkout of that commit that has
`ops/audit/`. Every command runs from the repository root.

## Role and credential

None, local only. The counted run is on the local stack (`make
infra-up`, with `make migrate` run once), in a database the run makes
and drops, with the provider twins in place of every provider. It holds
no cloud credential, reads no environment and no env file, and calls no
real provider.

## Procedure

1. Name the run: `audit_provider_calls_<yyyymmdd>`, today's date in UTC.
   When `uv run python ops/audit/auditdb.py list` shows that name taken,
   another run holds it: add a suffix (`_2`), and never drop a database
   this run did not make. The evidence folder is
   `~/Downloads/acme_provider_calls_<yyyy-mm-dd>/` and the report
   `~/Downloads/acme_provider_calls_<yyyy-mm-dd>.md`; when either
   exists, both take the next free suffix (`_2`), and the database takes
   the same suffix. Make the folder
   (`mkdir -p`). Say which commit the run read (`git rev-parse HEAD`).
2. List the provider clients and how each is built:
   - WorkOS: `integrations/src/acme/integrations/identity/workos.py`;
   - Stripe: `integrations/src/acme/integrations/payments/stripe.py`
     and `catalog.py`;
   - Slack: `integrations/src/acme/integrations/slack/web.py`;
   - AWS: `infra/src/acme/infra/aws_clients.py` and its callers
     (`buckets/s3.py`, `queues/sqs.py`, `secrets/aws.py`);
   - anything else that leaves the process for a third party: search
     `services/`, `workers/`, `infra/src`, `integrations/src`, and
     `om/src` for `httpx`, `aiohttp`, `urllib`, `boto`, `stripe`,
     `workos`, and `sentry_sdk`, and read the exporters in
     `infra/src/acme/infra/observability.py`. The cache and the
     database are the platform's own and not in scope.
   For each client: the timeout and where it is set
   (`integrations/src/acme/integrations/settings.py`, `aws_clients.py`),
   and the timeout the SDK actually sends, which it may override; the
   retries the SDK makes on its own and on what (a timeout, a 429, a
   5xx). Both are in the SDK's own source under
   `.venv/lib/python*/site-packages/<sdk>/` (`workos`, `stripe`,
   `slack_sdk`, `botocore`); botocore's retry mode is the default
   (legacy) unless `client_config` sets one. A timeout the settings name
   and the SDK overrides is a finding. The worst case of one call is
   timeout × (retries + 1), plus the backoff between tries; where the
   timeout is per phase (connect, then read), say so and take the sum. Say whether one client is built per
   process and reused, or one per call; and whether the process opens it
   at start (`start()`) or on first use.
3. List the flows: every route (the routers under
   `services/api/src/acme/services/api/routers/`), every inbound
   webhook (Stripe's and Slack's, in `routers/webhooks.py` there), every
   worker job
   (`WorkKind` in `om/src/acme/om/work/types/work_item.py`, its handler
   in `workers/maintenance/src/acme/workers/maintenance/`, and the
   consumers of the queues there), the sweep's steps (`loop.py`,
   `_sweep_once`), and boot (each process's start: `services/api/src/acme/services/api/app.py`
   and `main.py`, `token_secrets.py`, the worker's `main.py`, the order
   each container starts its roots in (`container.py` of the API and of
   the worker), and the roots' `start()`:
   `infra/src/acme/infra/impl/configured.py` and
   `integrations/src/acme/integrations/impl/configured.py`). The
   counter does not reach boot, so boot is read from the code. A flow
   that makes no provider call is left out of the table and counted in
   one line.
4. Count the calls the twins can show. Make the database and run the
   counter; every call records the provider calls it made, in order:

   ```bash
   uv run python ops/audit/auditdb.py create audit_provider_calls_<yyyymmdd>
   uv run python ops/audit/dbcalls.py run audit_provider_calls_<yyyymmdd> \
     --out ~/Downloads/acme_provider_calls_<yyyy-mm-dd>/calls.json \
     [--flows ~/Downloads/acme_provider_calls_<yyyy-mm-dd>/more_flows.py] [--only <flows>]
   uv run python ops/audit/dbcalls.py providers ~/Downloads/acme_provider_calls_<yyyy-mm-dd>/calls.json
   ```

   Run the built-in flows first, as above without `--flows`, and read
   `providers`. The counter wraps the identity, payments, and Slack
   twins and counts one call per method of the provider's interface.
   The real client may send more than one request for one method (a
   lookup and a create, a list read page by page): read each method in
   the real client and say how many requests it sends. The counter does
   not count AWS: locally the infra reaches no provider, so those calls
   are read from the code and marked as read. A flow of step 3 that
   calls a provider and that no built-in flow reaches (a checkout, a
   Slack install, a webhook, a worker job that calls Stripe) then goes
   in a flows file of the run's own, written as
   `.claude/skills/audit-database-calls/references/flows.md` shows
   (read it before writing one), run on the same database with `--only
   seed` into `calls_2.json`. A flow that fails is named, the rest run,
   and `run` exits 1; fix it in its own file and run that the same way
   (`calls_3.json`), or report it as not measured. Keep the counter's
   output beside `calls.json`.
5. Drop the run's database, whatever happened before, and check it is
   gone:

   ```bash
   uv run python ops/audit/auditdb.py drop audit_provider_calls_<yyyymmdd>
   uv run python ops/audit/auditdb.py list
   ```

6. For each flow that calls a provider, from the count and the code
   (file:line for each call):
   - the sequence, in order;
   - repeats and independents: the same call twice in a flow, a call
     whose answer the flow already had (from the database, an earlier
     call, the request), and calls that do not depend on one another;
   - on the request path or not: a route's handler that awaits the call
     before it answers is on it; a job, a sweep step, or work queued for
     after the response is not;
   - how often the flow runs: per request of a route, per sign-in, per
     delivery, per job, per boot. Where the flow's trigger gives a rate
     (an interval, a schedule), say it; otherwise say the trigger;
   - the worst case the calls add to the flow: the sum of each call's
     timeout × (retries + 1) on the path;
   - an overall deadline: whether anything bounds the whole request or
     job (a timeout around the handler, the load balancer's, the
     worker's lease), and whether it is shorter than that worst case. A
     request with no deadline shorter than its calls' worst case is a
     finding.
7. Rank the fixes in this order, and propose the first that applies:
   remove the call (its answer is known, or nothing reads it); fold
   calls into one (a batch, one read that answers two); move it off the
   request path (into a job, or after the response); cache it (with the
   TTL and what invalidates it); and last, run independent calls in
   parallel, which lowers the latency and not the load, and still waits
   on the slowest. A missing deadline, or a timeout the SDK overrides,
   is fixed by bounding it, and is ranked by its worst case beside
   them. Each finding gets its evidence, its fix, its effort,
   and a proposed ticket. The skill files none.
8. Write the report.

## What it never does

- Never writes to a shared database or to an environment: it logs in
  only to the database it made on the local stack, and drops it.
- Never modifies a tracked file, never commits, never opens a pull
  request: a run's own flows live in its evidence folder, and a fix is a
  proposal with its numbers.
- Never calls a real provider: the twins stand in for every one, and no
  key is read.
- Never reports a call it read from the code as counted: each row says
  measured or read.

## Output

`~/Downloads/acme_provider_calls_<yyyy-mm-dd>.md`:

```markdown
# Acme: provider calls per flow

<commit>, counted on <database> with the provider twins. How the count is taken, in two sentences.

## The answer

<the flows whose provider calls cost the most, per request and in total, and the first fix for each>

## Clients

| Provider | Client (file:line) | Reused | Timeout | Retries | Worst case of one call |
|---|---|---|---|---|---|

## Flows

| Flow | Calls, in order | Repeats or independent | Request path | Runs | Worst case added | Overall deadline | Measured |
|---|---|---|---|---|---|---|---|

Measured: yes (the twins), read (code only), or partly (say which calls).

## Findings, ranked

1. **<finding>.** Where (file:line). The cost (calls, seconds, how
   often). The fix, by the order above. Effort S/M/L. Proposed ticket:
   <title>.

## What I could not measure
```
