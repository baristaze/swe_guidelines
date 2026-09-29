#!/usr/bin/env bash
# Run `acme-api grant-operator <arguments>` once, as a one-off task on the
# grant task definition an environment root declares, and fail if it fails.
# grant-operator.yml runs it, and so does each deploy's smoke step, from the
# repo root with the deploy role's session and the root initialized.
#
#   scripts/cloud_grant.sh deployment/terraform/environments/staging --email <email> --permission read
#   scripts/cloud_grant.sh deployment/terraform/environments/staging --email <email> --disable
#   scripts/cloud_grant.sh deployment/terraform/environments/staging --email <email> --mint-token smoke
#
# The task holds the runtime login's and the system login's URLs and never
# the migration login's or the master's. A minted token goes from the task
# straight into the secret store; nothing here reads it. The arguments carry
# an email, so they go into the task's overrides and are never printed.
set -euo pipefail

root="${1:?usage: cloud_grant.sh <terraform environment dir> <grant-operator arguments>}"
shift
[ $# -gt 0 ] || { echo "usage: cloud_grant.sh <terraform environment dir> <grant-operator arguments>" >&2; exit 2; }

cluster="$(terraform -chdir="$root" output -raw cluster_name)"
task_definition="$(terraform -chdir="$root" output -raw grant_task_definition_arn)"
subnets="$(terraform -chdir="$root" output -json private_subnet_ids | jq -r 'join(",")')"
security_group="$(terraform -chdir="$root" output -raw app_security_group_id)"
container="$(terraform -chdir="$root" output -raw grant_container_name)"

# Each argument as one string, whatever it holds: jq reads them NUL-split
# from stdin, since an argument that starts with a dash is an option to it.
overrides="$(printf '%s\0' "$@" | jq -Rsc --arg name "$container" \
  '{containerOverrides: [{name: $name, command: (["acme-api", "grant-operator"] + (split("\u0000")[:-1]))}]}')"

task_arn="$(aws ecs run-task \
  --cluster "$cluster" \
  --task-definition "$task_definition" \
  --launch-type FARGATE \
  --started-by grant-operator \
  --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$security_group],assignPublicIp=DISABLED}" \
  --overrides "$overrides" \
  --query 'tasks[0].taskArn' --output text)"

echo "grant task $task_arn started on $cluster"
aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task_arn"

exit_code="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task_arn" \
  --query "tasks[0].containers[?name=='$container'] | [0].exitCode" --output text)"
reason="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task_arn" \
  --query 'tasks[0].stoppedReason' --output text)"

if [ "$exit_code" != "0" ]; then
  echo "the grant failed: exit code $exit_code ($reason); its log is in the task's log group" >&2
  exit 1
fi
echo "the grant ran"
