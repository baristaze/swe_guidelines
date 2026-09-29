---
name: audit-deploy-time
description: "Audit where a deploy's minutes go: the pipeline's jobs and steps from the Actions run, the Terraform apply's resources, the migration's one-off task, each service's rollout from its ECS events, its tasks, and their first log lines, and the settings that pace them (health-check grace, container health checks, thresholds, deregistration delay, stop timeout, the steady-state wait). Read-only, under the investigate profile; reports each phase against a target with a fix and its effort. Never changes anything."
allowed-tools: Read, Grep, Glob, Write, Bash(uv run:*), Bash(git:*), Bash(gh run:*), Bash(aws:*), Bash(jq:*), Bash(mkdir:*)
---

# audit-deploy-time

One deploy, or the last few, taken apart into phases: what each took,
what it waited on, and which setting or step would shorten it. Every
number comes from what the pipeline and the cluster already record.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the profiles, the account check, and the env
file are there.

## Input

`--env staging|production [--run <run id>] [--last <n>] [--target <minutes>]`

`--env` is required; ask for it when missing. `--run` is one run of the
environment's deploy workflow (`deploy-staging.yml`,
`deploy-production.yml`); without it, the last `--last` runs (3 by
default) that succeeded. `--target` is the time the report judges
against: 5 minutes for a deploy without a migration by default, and for
one with a migration the target plus the migration's own time, judged
both whole and without it. Say which target and where it came from.
Every command runs from the repository root, and every time is in UTC.

## Role and credential

Investigator, read-only. `gh` reads the runs and their logs under the
person's own GitHub sign-in (`gh auth status`). The cloud reads run
under `acme-<env>-investigate` (`acme-staging-investigate`,
`acme-production-investigate`), checked with `sts get-caller-identity`
before any other `aws` command, as the preamble states. Refuse any
profile wider than the investigate role. Every `aws` command carries
`--profile acme-<env>-investigate` and `--region` with the value of
`.region` in `deployment/cloud/environments.json`, written out in full
on each command: a shell's own region answers from another region, and
an empty answer there looks like nothing deployed. The skill reads no
env file and no token.

## Procedure

1. Pick the runs, one more than asked, so each has the deploy before it
   (step 3 diffs against it), and make the evidence folder
   `~/Downloads/acme_deploy_time_<yyyy-mm-dd>/` (`mkdir -p`):

   ```bash
   gh run list --workflow deploy-<env>.yml --status success --limit <n + 1> --json databaseId,headSha,createdAt,displayTitle
   ```

2. The pipeline's phases, per run:

   ```bash
   gh run view <run id> --json createdAt,updatedAt,jobs > ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/run_<run id>.json
   uv run python ops/audit/deploy_timeline.py steps ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/run_<run id>.json
   ```

   It prints each job with its id and its start and end in UTC, then the
   long steps with theirs. Read every job over a minute, not only the
   apply's (`plan and apply <env>, migrate, and publish`); the smoke test
   runs one-off tasks too. In each such job's log (`gh run view <run id>
   --log --job <job id>`, whose lines carry colour codes and three
   tab-separated fields), name every Terraform resource over thirty
   seconds from its `Creation complete after`, `Modifications complete
   after`, `Still creating... [<n>s elapsed]`, and `Still modifying...`
   lines: the migration (`terraform_data.pre_rollout`), each service
   (`aws_ecs_service.this`), and anything else.
3. The migration. `deployment/terraform/modules/service/pre_rollout.sh`
   and `deployment/terraform/modules/README.md` say when it runs a task:
   when a path `deployment/migration-inputs.json` names changed (`git
   diff --stat <the previous deploy's sha> <this sha> -- <those paths>`),
   or the database or its secrets did. Say whether this release needed
   it and whether it ran. Its task's times come from the apply log's
   `pre-rollout task ... started` and `finished` lines, the task's own
   log group `/acme/<env>/migrate`, and the task itself while ECS still
   holds it (about an hour after it stops):

   ```bash
   aws ecs describe-tasks --cluster acme-<env> --tasks <task arn> --profile acme-<env>-investigate --region <region>
   ```

   whose `createdAt`, `pullStartedAt`, `pullStoppedAt`, `startedAt`,
   `executionStoppedAt`, and `stoppedAt` split its cold start from its
   work.
4. Each service's rollout. Read the events over the apply's window plus
   two minutes after its end, since a service reports its steady state
   after the apply returns:

   ```bash
   aws ecs describe-services --cluster acme-<env> --services api maintenance \
     --profile acme-<env>-investigate --region <region> > ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/services.json
   uv run python ops/audit/deploy_timeline.py events ~/Downloads/acme_deploy_time_<yyyy-mm-dd>/services.json \
     --since <the apply step's start> --until <its end plus two minutes>
   ```

   Then each task the events name, with `aws ecs describe-tasks` as in
   step 3, and each new task's start line in its log group, the API's
   `api started with` and the worker's `maintenance <id> started with`:

   ```bash
   aws logs filter-log-events --log-group-name /acme/<env>/<api|maintenance> \
     --start-time <epoch ms> --end-time <epoch ms> --filter-pattern '"started with"' \
     --profile acme-<env>-investigate --region <region>
   ```

   An epoch in milliseconds is `uv run python -c "from datetime import
   datetime; print(int(datetime.fromisoformat('<ISO time>').timestamp() *
   1000))"`. For each rollout: when the task started, when it registered
   with the target group, when the process logged its start, when it
   turned healthy, and when the old task was gone. The load balancer
   keeps no history, so "healthy" is read from ECS stopping the old task
   of a service behind it, and is "not read" for a service without one.
   ECS keeps a service's last hundred events and a stopped task for about
   an hour, so an older run's may be gone: say so. A task that failed its
   health checks and was replaced is the costliest finding; the gap
   between the task's start and the process's start line is the cold
   start.
5. The settings that pace a rollout: in
   `deployment/terraform/modules/service/main.tf`,
   `health_check_grace_period_seconds`, the containers' own health checks
   (`startPeriod`, `interval`), `stopTimeout`, and
   `wait_for_steady_state`; in
   `deployment/terraform/modules/load_balancer/main.tf`, the target
   group's `deregistration_delay` and its health check's `interval`,
   `healthy_threshold`, and `unhealthy_threshold`; in
   `deployment/terraform/modules/environment/main.tf`, what each service
   is given (`deployment_minimum_healthy_percent`,
   `deployment_maximum_percent`, `stop_timeout_seconds`); and the
   environment's root (`deployment/terraform/environments/staging/` or
   `.../prod/`) for its counts. The live values are in the task
   definition (`aws ecs describe-task-definition`). Hold each against
   what step 4 measured.
6. Write the report: the phases in a table with their times and a
   verdict against the target, then the findings by the minutes each
   would save, each with its fix, its effort, and a proposed ticket. The
   skill files none.

## What it never does

- Never writes to an environment: no `aws` verb but `describe`, `get`,
  `list`, and `filter-log-events`; no `terraform apply`, no dispatch of
  a workflow, no rerun of a job.
- Never modifies a tracked file, never commits, never opens a pull
  request: it writes a report and proposes tickets.
- No secret read or printed.
- Never reports a phase it could not read as fast: an event ECS no
  longer holds is "not read".

## Output

`~/Downloads/acme_deploy_time_<yyyy-mm-dd>.md`:

```markdown
# Acme: where a <env> deploy's minutes go

Runs <ids and shas>; read under <profile and Arn>, <region>. Times in UTC. Target: <minutes> (<source>).

## The answer

<the total, the two phases that hold most of it, and what would bring it under the target>

## Phases

| Phase | Run <id> | Waits on | Verdict |
|---|---|---|---|

## Each rollout

| Service | Task started | Registered | Process up | Healthy | Old task gone | Replaced? |

## Findings, by minutes saved

1. **<finding>.** Evidence (times, events). Fix (the setting or step, and its value). Saves <minutes>. Effort S/M/L. Proposed ticket: <title>.

## What I could not read
```
