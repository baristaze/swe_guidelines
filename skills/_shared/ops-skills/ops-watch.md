---
name: ops-watch
description: "Watch one environment of the platform live from a sub-agent: a log tail, the alarms as they fire, and the error and latency signals, batched per interval and capped, with a read-only credential. Run it in a sub-agent the invoking session spawns, because it polls for the whole window and reports when the window ends or an alarm fires. It applies the first responder rule: outside production an alarm raised by the team's own traffic may be suppressed with the reason recorded; in production an alarm is never suppressed. Never writes."
allowed-tools: Read, Grep, Bash(aws:*), Bash(curl:*), Bash(docker compose:*), Bash(uv run:*), Bash(sleep:*)
---

# ops-watch

A live tail with a cap. The skill polls the logs of one environment
once per interval, reads the alarms every interval, and reports in
batches. It is written to be run by a sub-agent: the invoking session
spawns one with this skill's text and the arguments, and reads the
report when the agent returns. A session that runs it inline waits
for the whole window.

No command follows a stream. A command that never returns runs into
the shell's time cap, which ends the call and loses the batch. So
every read is a bounded query over one closed interval, and the wait
between two is a `sleep` of the interval, which is capped well under
the time cap.

## Input

`--env local|staging|production [--for 15m] [--interval 60s] [--cap 50] [--filter <text>]`

`--env` is required; ask for it when missing. `--for` is the window,
fifteen minutes by default; the skill ends when it passes. `--interval`
is the batch length, at most five minutes, so one wait never nears
the shell's time cap. `--cap` is the most lines one batch reports;
what is over the cap is counted, not printed. `--filter` narrows the
read to lines containing the text (a request id, a route, a level).

Spawn it in a sub-agent. The invoker names the window and reads the
report; the sub-agent does the polling.

`local` reads `docker compose logs` over each interval and the twins;
no cloud is needed.

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

The env file `~/.config/acme/ops/<env>.env` is owner-only and outside
the repository. It holds `ACME_API_URL`, `ACME_OPERATOR_TOKEN` (a
`read` operator token; the file's `ACME_PROVISIONER_TOKEN`, a `write`
token, belongs to the traffic generator alone), `ACME_ERROR_TRACKER_URL`,
and `ACME_ERROR_TRACKER_TOKEN`. `local.env`, which `make seed` writes,
points at the compose stack and adds the twins, `ACME_PROMETHEUS_URL`
and `ACME_JAEGER_URL`, on the ports `.env` names. It holds no password
and no TOTP secret: an agent never signs in with a password.

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

1. Verify the credential as Role and credential states. A chained
   session lasts an hour at most, so every interval reads the profile
   again and checks `sts get-caller-identity`; when the person's
   session behind it has ended, the watch stops and says so in its
   report, rather than retrying on a credential that is gone. Note
   the start time; every batch is
   `[start + k * interval, start + (k + 1) * interval)`, read once
   that interval has closed, and no batch is read twice.
2. Read the platform's size once:

   ```bash
   uv run acme-ops size --env <env>
   ```

   Keep the numbers; the first responder rule of step 6 reads them.
3. Each interval, wait for it to close, then read its lines. The
   wait:

   ```bash
   sleep <interval in seconds>
   ```

   The read, cloud, one query per process `deployment/README.md`
   lists (`api` and `maintenance` on a tree the scaffold built with
   its worker), bounded by the batch's start and end in epoch
   milliseconds, never `--follow`:

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

   Local: the alarm conditions of `ops-investigate`, the queue's
   among them, as queries against
   `$ACME_PROMETHEUS_URL/api/v1/query`. An alarm that was already in
   `ALARM` in the last batch is not reported again; a transition
   (`OK` to `ALARM`, `ALARM` to `OK`) is.
5. Each interval, read one number per signal for that interval and
   nothing more: the request count, the 5xx count, the p95, the
   worker failures, the oldest waiting item's age, and the outbox's
   lag, through `get-metric-data` with `--period` equal
   to the interval, or the same as a Prometheus range query. A burst
   is a count in the batch, never a line per event: the batch's lines
   over `--cap` are counted by level and dropped.
6. The first responder rule. An alarm transition is read against the
   size of step 2 and whose traffic it was. In production an alarm is
   never suppressed. Outside production, an alarm raised by the team's
   own traffic (one tenant and one user who are the developer, a
   stress run, the traffic generator's run tenants) is written into
   the batch as suppressed, with the reason and what was read, and the
   watch goes on. Anything else is an escalation: the batch is closed early, the report is written
   with the alarm at the top, and the sub-agent returns so the
   invoker can act.
7. When the window passes, write the report with
   every batch in order.

## What it never does

- No write to the cloud, no acknowledgement or silence of an alarm,
  no change to a log group.
- No secret value printed, no line printed that carries one (a line
  with a bearer or a password field is reported as its count).
- No tenant data outside the signals: the logs carry ids, never rows.
- No `terraform apply`, no console clicks.
- No unbounded output: never more than `--cap` lines per batch, never
  the same window twice, never a read past `--for`.
- No command that does not return: no `--follow`, no `-f`, no wait
  longer than one interval.

## Output

```markdown
# Watch: <env>, <start> to <end>, every <interval>

**Credential.** <profile and the Arn it resolved to, or local>
**Size.** <tenants> tenants, <users> users, <n> written in the last day
**Ended.** <window passed | escalated on <alarm> at <time>>

## Alarms

- <time> <alarm name>: <OK to ALARM | ALARM to OK>, <suppressed: reason | escalated>

## Batches

- <start of batch>: <requests> requests, <5xx> 5xx, p95 <ms>, <failures> worker failures, queue oldest <age>, outbox lag <age>; <lines> lines shown, <dropped> over the cap (<by level>)
  - <line>
  - <line>

## Findings

- <what moved, one sentence, with the request id that proves it>

## Next

<the skill to run next, with its arguments, or "nothing">
```
