#!/usr/bin/env bash
# Run the migration once, by hand, as one-off tasks on the migrate task
# definition an environment root just applied, and fail if either fails:
# first `acme-api migrate ensure-logins`, which connects as the master and
# creates or updates the three logins and their grants, then
# `acme-api migrate --all` as the migration login. The deploys run the same
# two inside the apply, before the services roll; this is the by-hand
# runner, from the repo root with the AWS session that applied the
# environment. The migrate task definition is the one that holds the master's
# and the migration login's URLs; the API's holds neither.
#
#   scripts/cloud_migrate.sh deployment/terraform/environments/staging
set -euo pipefail

root="${1:?usage: cloud_migrate.sh <terraform environment dir>}"

cluster="$(terraform -chdir="$root" output -raw cluster_name)"
task_definition="$(terraform -chdir="$root" output -raw migrate_task_definition_arn)"
container="$(terraform -chdir="$root" output -raw migrate_container_name)"
subnets="$(terraform -chdir="$root" output -json private_subnet_ids | jq -r 'join(",")')"
security_group="$(terraform -chdir="$root" output -raw app_security_group_id)"

# One one-off task per command, each waited for and read by the container's
# name, not by position: the task definition may carry a sidecar, and ECS
# does not order `containers`.
run_once() {
  local overrides task_arn exit_code reason
  overrides="$(jq -nc --arg name "$container" --arg subcommand "$1" \
    '{containerOverrides: [{name: $name, command: (["acme-api", "migrate"] + ($subcommand | split(" ")))}]}')"
  task_arn="$(aws ecs run-task \
    --cluster "$cluster" \
    --task-definition "$task_definition" \
    --launch-type FARGATE \
    --started-by migrate \
    --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$security_group],assignPublicIp=DISABLED}" \
    --overrides "$overrides" \
    --query 'tasks[0].taskArn' --output text)"
  echo "migrate $1: task $task_arn started on $cluster"
  aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task_arn"
  exit_code="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task_arn" \
    --query "tasks[0].containers[?name=='$container'] | [0].exitCode" --output text)"
  reason="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task_arn" \
    --query 'tasks[0].stoppedReason' --output text)"
  if [ "$exit_code" != "0" ]; then
    echo "migrate $1 failed: exit code $exit_code ($reason)" >&2
    exit 1
  fi
}

run_once ensure-logins
run_once --all
echo "migration applied"
