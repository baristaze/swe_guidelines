---
name: audit-deploy-time
description: "Audit where a deploy's minutes go: the pipeline's jobs and steps from the repository host's run, the Terraform apply's resources, the migration's one-off task, each service's rollout from its container service events and its tasks' first log lines, and the settings that pace them (health-check grace, thresholds, deregistration delay, the steady-state wait). Read-only, under the investigate profile; reports each phase against a target with a fix and its effort. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(uv run:*), Bash(git:*), Bash(gh run:*), Bash(aws:*), Bash(jq:*), Bash(mkdir:*)
---

# audit-deploy-time

One deploy, or the last few, taken apart into phases: what each took,
what it waited on, and which setting or step would shorten it. Every
number comes from what the pipeline and the cluster already record.

## Input

`--env staging|production [--run <run id>] [--last <n>] [--target <minutes>]`

`--env` is required; ask for it when missing. `--run` is one run of the
environment's deploy workflow; without it, the last `--last` runs (3 by
default) that succeeded. `--target` is the time the report judges
against: 5 minutes for a deploy without a migration by default, and for
one with a migration the target plus the migration's own time. Say
which target and where it came from. Every command runs from the
repository root, and every time is in UTC.

## Role and credential

Investigator, read-only. `gh` reads the runs under the person's own
sign-in to the repository host. The cloud reads run under
`acme-<env>-investigate`. Before any other `aws` command, run

```bash
aws sts get-caller-identity --profile acme-<env>-investigate
```

and check that `Account` is the environment's `account_id` in
`deployment/cloud/environments.json` and that `Arn` reads
`assumed-role/acme-investigate-<env>/...`. Refuse any profile wider than
the investigate role. Every `aws` command carries `--profile
acme-<env>-investigate` and `--region` with the value of `.region` in
`deployment/cloud/environments.json`: a shell's own region answers from
another region, and an empty answer there looks like nothing deployed.
The skill reads no env file and no token.

## Procedure

1. Pick the runs, one more than asked so each has the deploy before it,
   and make the evidence folder
   `~/Downloads/acme_deploy_time_<yyyy-mm-dd>/`:

   ```bash
   gh run list --workflow deploy-<env>.yml --status success --limit <n + 1> --json databaseId,headSha,createdAt
   ```

2. The pipeline's phases, per run:

   ```bash
   gh run view <run id> --json createdAt,updatedAt,jobs > ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/run_<run id>.json
   uv run python ops/audit/deploy_timeline.py steps ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/run_<run id>.json
   ```

   It prints each job's id and every start and end in UTC. Read every job
   over a minute; in each one's log (`gh run view <run id> --log --job
   <job id>`), name every Terraform resource over thirty seconds from its
   `complete after` and `Still creating` or `Still modifying` lines: the
   migration's one-off task, each service, and anything else.
3. The migration: say whether the release carried one (`git diff <the
   previous deploy's sha> <this sha> --stat -- om/migrations/`) and
   whether the task ran anyway, reading every path the migration's
   inputs name, not the migrations alone. Its time is its cold start
   plus the migration: `aws ecs describe-tasks` gives the task's created,
   pulled, started, and stopped times while the service still holds it,
   about an hour.
4. Each service's rollout:

   ```bash
   aws ecs describe-services --cluster acme-<env> --services <services> \
     --profile acme-<env>-investigate --region <region> > ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/services.json
   uv run python ops/audit/deploy_timeline.py events ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/services.json --since <the apply's start> --until <its end plus two minutes>
   ```

   then each task the events name (`aws ecs describe-tasks`), and each new
   task's start line in `/acme/<env>/<process>` (`aws logs
   filter-log-events`, its times in epoch milliseconds). For each rollout:
   the task started, registered with its target group, the process up, the
   target healthy (read from the old task's stop, since the load balancer
   keeps no history), the old task gone. A task replaced for failing its
   health checks is the costliest finding; the gap from the task's start
   to the process's start line is the cold start. The service keeps its
   last hundred events; an older run's may be gone, and the report says
   so.
5. The settings that pace a rollout, in the service, load balancer, and
   environment modules and the environment's root: the health-check grace,
   the containers' own health checks, the target group's interval and
   thresholds, the deregistration delay, the stop timeout, the rollout
   percentages, and the steady-state wait; the live values are in the task
   definition. Hold each against what step 4 measured.
6. Write the report: the phases with their times and a verdict against
   the target, then the findings by the minutes each would save, each
   with its fix, its effort, and a proposed ticket. The skill files none.

## What it never does

- Never writes to an environment: no `aws` verb but `describe`, `get`,
  `list`, and `filter-log-events`; no apply, no dispatch of a workflow,
  no rerun of a job.
- Never modifies a tracked file, never commits, never opens a pull
  request: it writes a report and proposes tickets.
- No secret read or printed.
- Never reports a phase it could not read as fast: it is "not read".

## Output

`~/Downloads/acme_deploy_time_<yyyy-mm-dd>.md`:

```markdown
# Acme: where a <env> deploy's minutes go

Runs <ids and shas>; read under <profile and Arn>. Target: <minutes> (<source>).

## The answer

## Phases

| Phase | Run <id> | Waits on | Verdict |

## Each rollout

| Service | Task started | Registered | Process up | Healthy | Old task gone | Replaced? |

## Findings, by minutes saved

1. Finding. Evidence. Fix. Saves <minutes>. Effort S/M/L. Proposed ticket.

## What I could not read
```
