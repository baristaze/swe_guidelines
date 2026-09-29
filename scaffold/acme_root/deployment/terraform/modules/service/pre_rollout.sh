#!/usr/bin/env bash
# Run commands in order, in one one-off task on a task definition, and stop
# at the first that fails. Terraform runs it from the service module before
# the service rolls (the API's: the logins, then the migration); the inputs
# arrive as environment variables from the provisioner, and the credentials
# are the ones the apply runs with. COMMANDS is a JSON array of commands,
# each a JSON array of words.
#
# One task for all of them: each task start is a Fargate cold start, so a
# task per command would pay it once per command. The task runs them as one
# shell script, each word quoted, under `set -e`; the container's exit code
# is the first failing command's.
#
# A command that exits 75 (the temporary failure of sysexits) failed for a
# moment, not for good: a migration that waited past its lock bound behind a
# transaction of the tasks still serving (ADR 0071). The task then runs
# again, whole, up to RUNS runs in all; every command is safe to repeat, and
# the next task's cold start, about a minute, is the wait between runs. Any
# other failure fails the apply at once.
set -euo pipefail

: "${CLUSTER:?}" "${TASK_DEFINITION:?}" "${SUBNETS:?}" "${SECURITY_GROUPS:?}" "${CONTAINER:?}" "${COMMANDS:?}"
RUN_AGAIN=75
RUNS=3

# One run of the task. It sets the caller's `exit_code` and `reason`.
run_once() {
  local task_arn

  # A role created moments ago in the same apply is not everywhere yet, and
  # ECS answers "unable to assume the role" until it is. That is the one
  # failure worth waiting out; every other one fails the deploy at once.
  local attempt=1 started
  while true; do
    if started="$(aws ecs run-task \
      --cluster "$CLUSTER" \
      --task-definition "$TASK_DEFINITION" \
      --launch-type FARGATE \
      --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SECURITY_GROUPS],assignPublicIp=DISABLED}" \
      --overrides "$overrides" \
      --query 'tasks[0].taskArn' --output text 2>&1)"; then
      task_arn="$started"
      break
    fi
    if [ "$attempt" -ge 10 ] || ! grep -q 'unable to assume the role' <<<"$started"; then
      echo "pre-rollout task did not start: $started" >&2
      exit 1
    fi
    echo "pre-rollout waits for the task role to reach ECS (attempt $attempt)"
    attempt=$((attempt + 1))
    sleep 6
  done

  echo "pre-rollout task $task_arn ($command) started on $CLUSTER"
  aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task_arn"

  # The exit code is read from the container by name, never by position: a
  # task definition may carry a sidecar, and ECS does not order `containers`.
  exit_code="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" \
    --query "tasks[0].containers[?name=='$CONTAINER'] | [0].exitCode" --output text)"
  reason="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" \
    --query 'tasks[0].stoppedReason' --output text)"
}

run_all() {
  local command overrides exit_code reason run
  command="$(jq -cn --argjson commands "$COMMANDS" \
    '["sh", "-c", (["set -eu"] + [$commands[] | map(@sh) | join(" ")] | join("\n"))]')"
  overrides="$(jq -cn --arg name "$CONTAINER" --argjson command "$command" \
    '{containerOverrides: [{name: $name, command: $command}]}')"

  for run in $(seq "$RUNS"); do
    run_once
    if [ "$exit_code" = "0" ]; then
      echo "pre-rollout task finished: $command"
      return
    fi
    if [ "$exit_code" != "$RUN_AGAIN" ] || [ "$run" -ge "$RUNS" ]; then
      break
    fi
    echo "pre-rollout task asked to run again: $command exited $exit_code ($reason); run $((run + 1)) of $RUNS"
  done
  echo "pre-rollout task failed: $command exited $exit_code ($reason); the service keeps its current tasks" >&2
  exit 1
}

[ "$(jq 'length' <<<"$COMMANDS")" -gt 0 ] || { echo "pre-rollout has no commands" >&2; exit 1; }
run_all
