#!/usr/bin/env bash
# Destroy an environment, as its account's administrator. The
# `ops-cloud-deployment-nuke` skill narrates this script; the script is the
# hands.
#
#   scripts/cloud_nuke.sh staging [--dry-run]
#   scripts/cloud_nuke.sh production --confirm production [--dry-run]
#
# Staging goes on the word. Production refuses unless three things hold: its
# name is typed after `--confirm`; `environments/prod/main.tf` on
# `origin/release`, the branch production applies, reads
# `database_deletion_protection = false`; and production's state shows that
# release applied. The destruction of production is itself a pull request a
# person read, released like any other.
#
# The run applies from the exact commit the environment runs, in a clean
# worktree of its own: `release` for production, and for staging the commit
# of its last deploy whose apply succeeded, never the tip of `main`. It
# applies the environment root once with `destroyable=true`: buckets
# empty on destroy, and staging's database drops its protection and skips
# its final snapshot. Production's database does neither: its protection is
# off only because a release turned it off, and it keeps its final snapshot
# and its automated backups. Then it destroys the root and prints what
# remains. It prints every command
# before it runs it, and `--dry-run` prints them without running anything.
# The account, the administrator profile, the region, and the public names
# come from deployment/cloud/environments.json, and the account is checked
# before the apply and before the destroy. The bootstrap root stays: the
# account keeps its roles, its registry, its zones, and its state. Nothing
# at the providers is touched either; the list at the end names what stays
# at WorkOS and in the error tracker.
#
# After the destroy it removes what the environment left that Terraform does
# not own: the secrets the application wrote and the leftovers AWS made for
# the resources the root declared. The tenants' secrets follow the database:
# while its final snapshot exists a restore needs them, and they stay. A root
# already destroyed goes straight to that step, so a run that stopped part
# way can be finished.
#
# Inputs, as flags or as environment variables:
#   --alarm-email    ALARM_EMAIL       the address the root's alarms go to
set -euo pipefail

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

refuse() {
  echo "refused: $*" >&2
  exit 2
}

environment=""
confirm=""
dry_run=false
profile="${AWS_PROFILE:-}"
alarm_email="${ALARM_EMAIL:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    --confirm) confirm="${2:-}"; shift ;;
    --profile) profile="${2:-}"; shift ;;
    --alarm-email) alarm_email="${2:-}"; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *) [ -z "$environment" ] || usage; environment="$1" ;;
  esac
  shift
done

case "$environment" in
  staging|production) ;;
  "") usage ;;
  *) refuse "the environment is staging or production, not '$environment'" ;;
esac

cd "$(dirname "$0")/.."
environments=deployment/cloud/environments.json

config() {
  jq -er "$1" "$environments"
}

region="$(config .region)"
account_id="$(config ".environments.$environment.account_id")"
admin_profile="$(config ".environments.$environment.admin_profile")"
root="$(config ".environments.$environment.environment_root")"
api_domain_name="$(config ".environments.$environment.api_domain_name")"
app_domain_name="$(config ".environments.$environment.app_domain_name")"
site_domain_name="$(config ".environments.$environment.site_domain_name")"
state_bucket="acme-state-$account_id"
artifacts_bucket="acme-artifacts-$account_id"

profile="${profile:-$admin_profile}"
[ "$profile" = "$admin_profile" ] || refuse "$environment is destroyed under the $admin_profile profile only (AWS_PROFILE or --profile); it holds '$profile'"

[ -n "$alarm_email" ] || refuse "missing: ALARM_EMAIL (as a flag or an environment variable)"

# Keys exported in the shell outrank a profile for Terraform; they do not
# get to decide which account this destroys.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_DEFAULT_PROFILE AWS_DEFAULT_REGION
export AWS_PROFILE="$profile"
export AWS_REGION="$region"

if [ "$environment" = "production" ]; then
  # Read here rather than trusted: the typed name, and the released change.
  # `origin/release` is what production applies; `main` may hold a change
  # that was merged and never released, and the working tree is what someone
  # might have edited a minute ago. The applied state is checked below.
  [ "$confirm" = "production" ] || refuse "production is destroyed only behind its typed name: pass --confirm production"
  if ! $dry_run; then git fetch --quiet origin release || true; fi
  release_root="$(git show origin/release:deployment/terraform/environments/prod/main.tf 2>/dev/null)" \
    || refuse "cannot read environments/prod/main.tf on origin/release; fetch it, then run again"
  printf '%s\n' "$release_root" | grep -Eq '^\s*database_deletion_protection\s*=\s*false\s*$' \
    || refuse "environments/prod/main.tf on origin/release does not read database_deletion_protection = false; lift it in a pull request, merge it, release it, then run again"
fi

run() {
  printf '+ %s\n' "$*"
  if ! $dry_run; then "$@"; fi
}

say() {
  printf '%s\n' "$*"
}

check_account() {
  say "+ aws sts get-caller-identity --profile $profile  (expect account $account_id)"
  if $dry_run; then return; fi
  local actual
  actual="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
  [ "$actual" = "$account_id" ] || refuse "$profile resolves to account $actual, and $environment is account $account_id"
}

say "== 1. Who am I"
check_account

say "== 2. The environment root, as it is deployed"
# The apply below runs as the administrator, so it must be the code the
# environment runs and nothing else, in a clean worktree of its own, never
# the person's working tree, where an unreleased change would reach
# production without its approval. Production runs `release`. Staging runs
# the commit of its last deploy whose apply succeeded, which the deploy's
# run names; the tip of `main` may still be deploying, or may have failed.
# The job name is deploy-staging.yml's apply job, which release.yml reads
# the same way.
apply_job="plan and apply staging, migrate, and publish"

last_staging_deploy() {
  local run applied
  for run in $(gh run list --workflow deploy-staging.yml --branch main --status completed \
      --limit 50 --json databaseId -q '.[].databaseId'); do
    applied="$(gh run view "$run" --json jobs \
      -q "[.jobs[] | select(.name == \"$apply_job\") | .conclusion] | first // \"\"")"
    [ "$applied" = "success" ] || continue
    gh run view "$run" --json displayTitle -q '.displayTitle' | awk '{ print $NF }'
    return 0
  done
  return 1
}

case "$environment" in
  staging)
    branch=main
    say "+ gh run list --workflow deploy-staging.yml --branch main  (the newest run whose apply succeeded names the commit staging runs)"
    ;;
  production) branch=release ;;
esac
if $dry_run; then
  case "$environment" in
    staging) commit="<the last commit staging deployed>" ;;
    production) commit="<origin/release>" ;;
  esac
  source_dir="<a worktree of the deployed commit>"
else
  git fetch --quiet origin "$branch" || refuse "cannot fetch origin/$branch"
  if [ "$environment" = "staging" ]; then
    running="$(gh run list --workflow deploy-staging.yml --branch main --limit 20 --json status \
      -q '[.[] | select(.status != "completed")] | length')"
    [ "$running" = "0" ] || refuse "a deploy-staging run is still going; let it finish, then run again"
    commit="$(last_staging_deploy)" || refuse "no deploy-staging run on main has applied staging; there is no deployed commit to destroy from"
    git cat-file -e "$commit^{commit}" 2>/dev/null || refuse "the last commit staging deployed ($commit) is not in this checkout; fetch it, then run again"
    git merge-base --is-ancestor "$commit" origin/main || refuse "the last commit staging deployed ($commit) is not on main"
  else
    commit="$(git rev-parse "origin/$branch")"
  fi
  source_dir="$(mktemp -d)/acme-$environment"
  git worktree add --quiet --detach "$source_dir" "$commit"
  trap 'git worktree remove --force "$source_dir" >/dev/null 2>&1 || true' EXIT
fi
say "+ git worktree add --detach $source_dir $commit  (what $environment runs)"
root_dir="$source_dir/deployment/terraform/$root"
run terraform -chdir="$root_dir" init -input=false \
  -backend-config="bucket=$state_bucket" \
  -backend-config="key=$root/terraform.tfstate" \
  -backend-config="region=$region" \
  -backend-config="use_lockfile=true"

# The images the environment runs are in its state; the apply below must
# pass the same ones, or it would roll the services on the way down.
image_in_state() {
  terraform -chdir="$root_dir" show -json | jq -r --arg name "$1" '
    [.. | objects | select(.type? == "aws_ecs_task_definition")
     | .values.container_definitions | fromjson | .[] | select(.name == $name) | .image] | first // empty'
}
# A destroy that already ran leaves the state empty, and only its leftovers
# remain (step 5). An empty state while the environment's cluster or database
# still exists is a backend pointed at the wrong place, never a destroyed
# root.
still_running() {
  local clusters
  clusters="$(aws ecs describe-clusters --clusters "acme-$environment" \
    --query 'length(clusters[?status==`ACTIVE`])' --output text)"
  [ "$clusters" = "0" ] || { echo "the cluster acme-$environment"; return; }
  # Only a database that is not found counts as gone; any other answer is
  # read as one that still runs.
  local answer
  if answer="$(aws rds describe-db-instances --db-instance-identifier "acme-$environment" 2>&1)" \
      || ! grep -q DBInstanceNotFound <<<"$answer"; then
    echo "the database acme-$environment"
  fi
}
root_destroyed=false
api_image=""
maintenance_image=""
resources=""
if ! $dry_run; then
  # A state that cannot be read is never an empty one.
  resources="$(terraform -chdir="$root_dir" state list)" || refuse "cannot read the state at $root/"
fi
if ! $dry_run && [ -z "$resources" ]; then
  live="$(still_running)"
  [ -z "$live" ] || refuse "the state at $root/ holds nothing, and $live still exists: the backend is wrong"
  root_destroyed=true
fi

if $dry_run; then
  api_image="<api image in state>"
  maintenance_image="<maintenance image in state>"
elif ! $root_destroyed; then
  api_image="$(image_in_state api)"
  maintenance_image="$(image_in_state maintenance)"
  [ -n "$api_image" ] && [ -n "$maintenance_image" ] || refuse "the state holds no task definitions; nothing to destroy, or the backend is wrong"
fi

# The released change, as applied: the database in state carries no
# deletion protection. A release that has not deployed yet is refused here.
if [ "$environment" = "production" ] && ! $root_destroyed; then
  say "+ terraform -chdir=$root_dir show -json  (expect the database's deletion_protection false)"
  if ! $dry_run; then
    protected="$(terraform -chdir="$root_dir" show -json | jq -r '
      [.. | objects | select(.type? == "aws_db_instance") | .values.deletion_protection] | first // "missing"')"
    [ "$protected" = "false" ] || refuse "production's database in state reads deletion_protection = $protected; the release that lifts it has not applied yet"
  fi
fi

# The company site is optional: the apply names it only when the state
# holds it, since its certificate may never have been issued.
if $dry_run; then
  site_in_state="$site_domain_name"
else
  site_bucket="$(terraform -chdir="$root_dir" output -raw site_bucket 2>/dev/null || true)"
  site_in_state="${site_bucket:+$site_domain_name}"
fi

root_vars=(
  -var "api_image=$api_image"
  -var "maintenance_image=$maintenance_image"
  -var "api_domain_name=$api_domain_name"
  -var "app_domain_name=$app_domain_name"
  -var "site_domain_name=$site_in_state"
  -var "alarm_email=$alarm_email"
  -var "destroyable=true"
)

if $root_destroyed; then
  say "== 3. and 4. Skipped: the state at $root/ holds nothing, and neither the cluster nor the database exists; a destroy already ran"
else
  say "== 3. Lift the protections: buckets empty on destroy, the database skips its snapshot"
  check_account
  run terraform -chdir="$root_dir" apply -input=false -auto-approve "${root_vars[@]}"

  say "== 4. Destroy $environment"
  check_account
  run terraform -chdir="$root_dir" destroy -input=false -auto-approve "${root_vars[@]}"
fi

say "== 5. Remove what Terraform does not own"
# The destroy removes what the root declares. The application writes secrets
# of its own, and AWS makes a few things for the resources the root declared;
# neither is in the state, so each is found here by the environment's names
# and removed. Each read is printed before it runs; a dry run runs none.
check_account

# Prints a read, runs it unless this is a dry run, and leaves the names it
# found in $found, one to a line.
found=""
names() {
  say "+ aws $*"
  found=""
  if $dry_run; then return 0; fi
  found="$(aws "$@" --output text | tr '\t' '\n' | sed '/^None$/d; /^$/d')"
}

# The tenants' secrets follow the database. While its final snapshot exists,
# a restore of it needs them, so they stay. When the database went without
# one, nothing can read them again, and they go with no recovery window. Only
# a snapshot that is not found counts as none: any other answer stops here.
final_snapshot="acme-$environment-final"
tenant_prefix="acme/$environment/app/org/"
say "+ aws rds describe-db-snapshots --db-snapshot-identifier $final_snapshot  (while it exists, the tenants' secrets stay)"
snapshot_kept=false
if ! $dry_run; then
  if answer="$(aws rds describe-db-snapshots --db-snapshot-identifier "$final_snapshot" 2>&1)"; then
    snapshot_kept=true
  elif ! grep -q DBSnapshotNotFound <<<"$answer"; then
    refuse "cannot tell whether $final_snapshot exists, so the tenants' secrets stay: $answer"
  fi
fi
names secretsmanager list-secrets --filters "Key=name,Values=$tenant_prefix" \
  --query "SecretList[?starts_with(Name, '$tenant_prefix')].Name"
tenant_secrets="$found"
if $dry_run; then
  say "+ aws secretsmanager delete-secret --secret-id <each name above> --force-delete-without-recovery  (only when $final_snapshot does not exist)"
elif $snapshot_kept; then
  say "- the tenants' secrets under $tenant_prefix stay with $final_snapshot: $(grep -c . <<<"$tenant_secrets" || true)"
else
  for name in $tenant_secrets; do
    run aws secretsmanager delete-secret --secret-id "$name" --force-delete-without-recovery
  done
fi

# ECS makes the cluster's Container Insights log group when the cluster turns
# them on, outside the state.
names logs describe-log-groups --log-group-name-prefix "/aws/ecs/containerinsights/acme-$environment/" \
  --query 'logGroups[].logGroupName'
for name in $found; do
  run aws logs delete-log-group --log-group-name "$name"
done
if $dry_run; then
  say "+ aws logs delete-log-group --log-group-name <each name above>"
fi

# Each deploy registers a revision of every task definition family, and the
# destroy deregisters only the last. The revisions cost nothing, but they are
# the environment's, and it is gone: any still active is deregistered, then
# every revision is deleted, ten to a call.
names ecs list-task-definition-families --family-prefix "acme-$environment-" --status ALL \
  --query families
for family in $found; do
  names ecs list-task-definitions --family-prefix "$family" --status ACTIVE --query taskDefinitionArns
  for arn in $found; do
    run aws ecs deregister-task-definition --task-definition "$arn" --query taskDefinition.status
  done
  names ecs list-task-definitions --family-prefix "$family" --status INACTIVE --query taskDefinitionArns
  inactive="$found"
  say "+ aws ecs delete-task-definitions  ($(grep -c . <<<"$inactive" || true) revisions of $family, ten to a call)"
  # The call is rate limited to about one a second: the CLI retries a
  # throttled one, the loop paces itself, and a batch the CLI still could
  # not send counts as failed rather than ending the run.
  failed=0
  while read -r batch; do
    [ -n "$batch" ] || continue
    # shellcheck disable=SC2086
    if answer="$(AWS_RETRY_MODE=adaptive AWS_MAX_ATTEMPTS=10 aws ecs delete-task-definitions \
        --task-definitions $batch --query 'length(failures)' --output text)"; then
      failed=$((failed + answer))
    else
      failed=$((failed + $(wc -w <<<"$batch")))
    fi
    sleep 1
  done < <(xargs -n 10 <<<"$inactive")
  [ "$failed" = "0" ] || say "- $failed revisions of $family could not be deleted; list them with aws ecs list-task-definitions --family-prefix $family --status INACTIVE"
done
if $dry_run; then
  say "+ aws ecs deregister-task-definition, then delete-task-definitions, for every revision of each family above"
fi

say "== 6. What remains"
say "- the bootstrap root, whole: the zones $api_domain_name and $app_domain_name and their delegation at Cloudflare, the site's certificate for $site_domain_name, the registry and its images, the roles, the budget, and the anomaly monitor"
say "- the state prefix $root/ and plans/$root/ in s3://$state_bucket (empty the prefix by hand if the environment is not coming back)"
say "- the portal and site builds under builds/ in s3://$artifacts_bucket"
say "- $site_domain_name and its certificate's validation record at Cloudflare: the site's CNAME now points at a distribution that is gone, and the create run of a new $environment removes it before its first deploy; delete it there by hand if the environment is not coming back"
if [ "$environment" = "production" ]; then
  say "- the database's final snapshot acme-production-final, and its automated backups"
fi
# Staging's builds reach production's account only once the artifacts bucket
# replicates there, which the create run turns on after production exists.
if [ "$environment" = "staging" ]; then
  say "+ aws s3api get-bucket-replication --bucket $artifacts_bucket  (production holds copies only when it replicates)"
  if $dry_run; then
    say "- production's copies of what staging built, in production's account, when the artifacts bucket replicates"
  elif aws s3api get-bucket-replication --bucket "$artifacts_bucket" >/dev/null 2>&1; then
    say "- production's copies of what staging built: its images and static builds, in production's account"
  else
    say "- nothing in production's account: the artifacts bucket does not replicate"
  fi
fi
say "- $HOME/.config/acme/ops/$environment.env and $environment.provisioner.env beside it, and the investigate profile in $HOME/.aws/config; their tokens, and every operator's second-factor enrolment, went with the database: a recreated $environment grants and enrols each operator again"
say "- the GitHub environments and their variables: SMOKE_EMAIL still names the smoke identity, whose grant went with the database, so a recreated $environment's smoke step fails until the grant runs again"
if $snapshot_kept; then
  say "- the tenants' secrets under $tenant_prefix in Secrets Manager, with the final snapshot that needs them; delete them by hand only with it"
fi

# The providers: nothing here writes to them. What the environment leaves
# there is listed so a person decides; a recreate reuses most of it.
case "$environment" in
  staging) workos_environment="Staging" ;;
  production) workos_environment="Production" ;;
esac
say "== 7. What remains at the providers (docs/runbooks/providers/)"
say "- WorkOS $workos_environment: the organizations its team orgs made (external_id = the old org id) and the users who signed in. Harmless; a recreate makes new orgs. The Acme App application, its API keys, its redirects for https://$app_domain_name, and its webhook endpoint https://$api_domain_name/webhooks/identity stay, and the next $environment uses them."
say "- The error tracker: the product's one project keeps the events $environment reported, tagged environment:$environment."
say "- LaunchDarkly: the project, its flags, and their rules stay, and the next $environment uses them."
say "- The values of acme/$environment/{workos_api_key,workos_webhook_secret,sentry_dsn,launchdarkly_sdk_key} went with the secrets: a recreate writes each again after its first deploy. Revoke a key at its provider if $environment is not coming back."
