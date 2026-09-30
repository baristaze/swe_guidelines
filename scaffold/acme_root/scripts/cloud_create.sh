#!/usr/bin/env bash
# Create an environment's account from nothing to its first deploy, as the
# account's administrator. The `ops-cloud-deployment-create` skill narrates
# this script; the script is the hands.
#
#   scripts/cloud_create.sh staging [--dry-run]
#   scripts/cloud_create.sh production [--dry-run]
#
# Each environment has an AWS account of its own, and
# deployment/cloud/environments.json names it: the account id, the
# administrator profile that bootstraps it, the Identity Center profile an
# operator signs in with, the region, and the environment's three public
# names (the API, the portal, the company site). This script reads
# everything from there and refuses to act in any other account.
#
# What it does, in order: checks the profile is the environment's
# administrator and the account is the environment's, and checks the GitHub
# login; applies the environment's bootstrap root with local state and moves
# that state into the bucket it made; delegates the API's and the portal's
# names from the domain's zone at Cloudflare to the zones the root made;
# writes the company site's records in that zone (its certificate's
# validation record, then, once a deploy has made the site's distribution,
# the site's name as a CNAME to it); writes the
# investigate profile, chained from the Identity Center profile; creates the
# GitHub environments and sets their variables from the root's outputs;
# writes the operator's env file with its two token lines empty; starts the
# first deploy through the pipeline, which is how every later commit reaches
# the cloud; prints the provider steps a person takes after that deploy (the
# WorkOS and error tracker values written into the secrets it made); and
# prints the grants that come after them, the first operator's among them,
# which the grant-operator workflow runs. It checks the
# account again before every apply. It prints every command before it runs
# it, and `--dry-run` prints them without running anything.
#
# environments.json and the environment root's WorkOS client id ship as
# placeholders: twelve equal digits for an account, a name under the
# reserved `.example` domain, 0 for a GitHub id, a `_PLACEHOLDER` client id.
# A dry run prints with them. A real run refuses while one is left, and
# names each, before it runs anything.
#
# Staging runs first, then production, then staging again: staging's images
# and portal builds replicate into production's account, and the replication
# needs production's artifacts bucket to exist. The second staging run finds
# it and turns the replication on.
#
# Inputs, as flags or as environment variables:
#   --owner-email    OWNER_EMAIL           where the budget and anomaly mail goes
#   --alarm-email    ALARM_EMAIL           where the environment's alarms go
#                    CLOUDFLARE_API_TOKEN  a token with DNS edit on the domain's
#                                          zone; an environment variable only,
#                                          so it never lands in shell history
set -euo pipefail

usage() {
  # The header comment, up to the first line that is not one.
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0" >&2
  exit 2
}

refuse() {
  echo "refused: $*" >&2
  exit 2
}

environment=""
dry_run=false
profile="${AWS_PROFILE:-}"
owner_email="${OWNER_EMAIL:-}"
alarm_email="${ALARM_EMAIL:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    --profile) profile="${2:-}"; shift ;;
    --owner-email) owner_email="${2:-}"; shift ;;
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
domain="$(config .domain)"
account_id="$(config ".environments.$environment.account_id")"
admin_profile="$(config ".environments.$environment.admin_profile")"
sso_profile="$(config ".environments.$environment.sso_profile")"
bootstrap_root="deployment/terraform/$(config ".environments.$environment.bootstrap_root")"
environment_root="deployment/terraform/$(config ".environments.$environment.environment_root")"
api_domain_name="$(config ".environments.$environment.api_domain_name")"
app_domain_name="$(config ".environments.$environment.app_domain_name")"
site_domain_name="$(config ".environments.$environment.site_domain_name")"
state_bucket="acme-state-$account_id"
artifacts_bucket="acme-artifacts-$account_id"

# The credential is the boundary. This script creates roles and trust, so it
# runs under the environment's administrator profile and refuses any other:
# a sign-in profile cannot write IAM, and another environment's
# administrator is the wrong account.
profile="${profile:-$admin_profile}"
[ "$profile" = "$admin_profile" ] || refuse "$environment is created under the $admin_profile profile only (AWS_PROFILE or --profile); it holds '$profile'"

missing=""
[ -n "$owner_email" ] || missing="$missing OWNER_EMAIL"
[ -n "$alarm_email" ] || missing="$missing ALARM_EMAIL"
if ! $dry_run && [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then missing="$missing CLOUDFLARE_API_TOKEN"; fi
[ -z "$missing" ] || refuse "missing:$missing (as flags or environment variables)"

# Every account, name, and id in environments.json, since the bootstrap roots
# read both environments' accounts; and the client id this environment's
# root signs people in with.
placeholders="$(jq -r '
  def example: test("(^|\\.)example$");
  [
    (.environments | to_entries[] | .key as $env | .value
      | (select(.account_id | split("") | unique | length == 1) | "environments.\($env).account_id"),
        (to_entries[] | select((.key | endswith("_domain_name")) and (.value | example)) | "environments.\($env).\(.key)")),
    (select(.domain | example) | "domain"),
    (select(.github_repository_id == "0") | "github_repository_id"),
    (select(.github_repository_owner_id == "0") | "github_repository_owner_id")
  ] | join(", ")' "$environments")"
if grep -q '_PLACEHOLDER"' "$environment_root/variables.tf"; then
  placeholders="${placeholders:+$placeholders, }workos_client_id in $environment_root/variables.tf"
fi
if [ -n "$placeholders" ] && ! $dry_run; then
  refuse "placeholders left: $placeholders. Set the deployment's own values in $environments and $environment_root/variables.tf first (deployment/cloud/README.md)."
fi

# Keys exported in the shell outrank a profile for Terraform, and a region
# exported there outranks the one the environment names. Neither is allowed
# to decide where this lands.
exported=""
for name in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_DEFAULT_PROFILE AWS_DEFAULT_REGION; do
  if [ -n "${!name:-}" ]; then exported="$exported $name"; fi
done
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_DEFAULT_PROFILE AWS_DEFAULT_REGION
export AWS_PROFILE="$profile"
export AWS_REGION="$region"
if [ -n "$exported" ]; then
  echo "note: the shell exported$exported; cleared for this run. An exported key is a long-lived one, and this platform uses none."
fi

# Every command is printed before it runs; a dry run stops at the printing.
run() {
  printf '+ %s\n' "$*"
  if ! $dry_run; then "$@"; fi
}

say() {
  printf '%s\n' "$*"
}

# Before anything that writes: the profile resolves to the environment's
# account, or nothing runs. The providers pin the same account, so a check
# skipped here would still fail there.
check_account() {
  say "+ aws sts get-caller-identity --profile $profile  (expect account $account_id)"
  if $dry_run; then return; fi
  local actual
  actual="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
  [ "$actual" = "$account_id" ] || refuse "$profile resolves to account $actual, and $environment is account $account_id"
}

# A value a later command needs: in a dry run, a placeholder that names it.
output_of() {
  if $dry_run; then
    echo "<$1>"
  else
    terraform -chdir="$bootstrap_root" output -raw "$1"
  fi
}

case "$environment" in
  staging) github_environments="staging-build and staging" ;;
  production) github_environments="production-plan and production" ;;
esac

say "== 1. Who am I"
check_account
run gh auth status

say "== 2. The bootstrap root: state, registry, OIDC trust, the deploy roles, the investigate role, the budget, the zones"
backend_args=(
  -backend-config="bucket=$state_bucket"
  -backend-config="key=bootstrap/terraform.tfstate"
  -backend-config="region=$region"
  -backend-config="use_lockfile=true"
)
bootstrap_vars=(-var "owner_email=$owner_email")

# The anomaly monitor needs Cost Explorer, and turning that on is the
# organization's management account's to do, never this script's.
# Cost Explorer answers in us-east-1 only, whatever the environment's region.
say "+ aws ce get-anomaly-monitors --region us-east-1  (answers once Cost Explorer is on for the account)"
if $dry_run; then
  anomaly_monitor="<true once Cost Explorer answers>"
elif aws ce get-anomaly-monitors --region us-east-1 --max-results 1 >/dev/null 2>&1; then
  anomaly_monitor=true
else
  anomaly_monitor=false
  say "note: Cost Explorer is not on for account $account_id, so the anomaly monitor is left out; the budget is not. Turn Cost Explorer on from the organization's management account, then run this again."
fi
bootstrap_vars+=(-var "anomaly_monitor=$anomaly_monitor")

# The budget is not optional, and a member account can make one only once
# the management account has turned Budgets on for the organization. Asked
# here, before the apply, so the root is never left half made over it.
say "+ aws budgets describe-budgets --account-id $account_id  (answers once the management account turned Budgets on)"
if ! $dry_run && ! probe="$(aws budgets describe-budgets --account-id "$account_id" --max-results 1 2>&1)"; then
  case "$probe" in
    *"linked account"*) refuse "Budgets is not on for account $account_id: the management account turns it on (deployment/cloud/first_time_manual.md, "Turn on Cost Explorer and Budgets for the member accounts"), then run this again." ;;
    *) refuse "cannot tell whether Budgets answers for account $account_id: $probe" ;;
  esac
fi
if [ "$environment" = "staging" ]; then
  # The replication writes into production's artifacts bucket, named from
  # environments.json. A bucket name is global, so asking for it from
  # staging's account answers 403 when it exists and 404 when it does not.
  production_bucket="acme-artifacts-$(config .environments.production.account_id)"
  say "+ aws s3api head-bucket --bucket $production_bucket  (403 or 200: production exists, replicate into it)"
  if $dry_run; then
    replicate="<true once production's bucket exists>"
  elif probe="$(aws s3api head-bucket --bucket "$production_bucket" 2>&1)"; then
    replicate=true
  else
    case "$probe" in
      *403*|*Forbidden*) replicate=true ;;
      *404*|*"Not Found"*) replicate=false ;;
      *) refuse "cannot tell whether production's bucket exists: $probe" ;;
    esac
  fi
  bootstrap_vars+=(-var "replicate_to_production=$replicate")
  say "Replication into production: $replicate."
fi

if $dry_run || [ -f "$bootstrap_root/terraform.tfstate" ] || ! aws s3api head-bucket --bucket "$state_bucket" >/dev/null 2>&1; then
  # The bucket the state lives in is made by this root, so the first apply
  # holds its state locally, and the state moves into the bucket after. A
  # local state left behind is a first apply that stopped part way: the
  # bucket may exist by then, and the rest still applies against that state.
  if [ -f "$bootstrap_root/terraform.tfstate" ]; then
    say "A local state is left from a first apply that stopped part way: the root applies against it, then its state moves into the bucket."
  else
    say "The state bucket does not exist yet: the root applies with local state, then its state moves into the bucket."
  fi
  say "+ write $bootstrap_root/backend_override.tf (terraform { backend \"local\" {} })"
  if ! $dry_run; then
    printf 'terraform {\n  backend "local" {}\n}\n' > "$bootstrap_root/backend_override.tf"
    trap 'rm -f "$bootstrap_root/backend_override.tf"' EXIT
  fi
  run terraform -chdir="$bootstrap_root" init -input=false
  check_account
  run terraform -chdir="$bootstrap_root" apply -input=false "${bootstrap_vars[@]}"
  say "+ rm $bootstrap_root/backend_override.tf"
  if ! $dry_run; then rm -f "$bootstrap_root/backend_override.tf"; trap - EXIT; fi
  run terraform -chdir="$bootstrap_root" init -input=false -migrate-state -force-copy "${backend_args[@]}"
  say "+ rm -f $bootstrap_root/terraform.tfstate $bootstrap_root/terraform.tfstate.backup"
  if ! $dry_run; then rm -f "$bootstrap_root/terraform.tfstate" "$bootstrap_root/terraform.tfstate.backup"; fi
else
  run terraform -chdir="$bootstrap_root" init -input=false -reconfigure "${backend_args[@]}"
  check_account
  run terraform -chdir="$bootstrap_root" apply -input=false "${bootstrap_vars[@]}"
fi

say "== 3. The delegation at Cloudflare: the API's and the portal's NS records name their zones here"
cloudflare_api=https://api.cloudflare.com/client/v4

# The token goes in a header and never on a printed line.
cloudflare() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS --fail-with-body -X "$method" -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json")
  [ -z "$body" ] || args+=(--data "$body")
  curl "${args[@]}" "$cloudflare_api$path"
}

if $dry_run; then
  say "+ cloudflare GET /zones?name=$domain"
  zone_id="<zone id>"
  for name in "$api_domain_name" "$app_domain_name"; do
    say "+ cloudflare GET /zones/<zone id>/dns_records?type=NS&name=$name"
    say "+ cloudflare POST /zones/<zone id>/dns_records NS $name -> <each name server of $name>, and DELETE any other NS record there"
  done
else
  name_servers="$(terraform -chdir="$bootstrap_root" output -json name_servers)"
  say "+ cloudflare GET /zones?name=$domain"
  zone_id="$(cloudflare GET "/zones?name=$domain" | jq -er '.result[0].id')" \
    || refuse "Cloudflare holds no zone named $domain that the token can read"
  for name in "$api_domain_name" "$app_domain_name"; do
    wanted="$(printf '%s' "$name_servers" | jq -r --arg name "$name" '.[$name][]')"
    say "+ cloudflare GET /zones/$zone_id/dns_records?type=NS&name=$name"
    existing="$(cloudflare GET "/zones/$zone_id/dns_records?type=NS&name=$name&per_page=100")"
    for server in $wanted; do
      if printf '%s' "$existing" | jq -e --arg server "$server" '.result[] | select(.content == $server)' >/dev/null; then
        say "$name NS $server is there already."
      else
        say "+ cloudflare POST /zones/$zone_id/dns_records NS $name -> $server"
        cloudflare POST "/zones/$zone_id/dns_records" \
          "$(jq -nc --arg name "$name" --arg server "$server" '{type: "NS", name: $name, content: $server, ttl: 3600}')" >/dev/null
      fi
    done
    # A record left from a zone that was destroyed and made again points at
    # name servers that no longer answer for it.
    printf '%s' "$existing" | jq -r '.result[] | "\(.id) \(.content)"' | while read -r record_id server; do
      if ! printf '%s\n' "$wanted" | grep -qxF "$server"; then
        say "+ cloudflare DELETE /zones/$zone_id/dns_records/$record_id ($name NS $server)"
        cloudflare DELETE "/zones/$zone_id/dns_records/$record_id" >/dev/null
      fi
    done
  done
fi

# One CNAME at a name, DNS only, so CloudFront and ACM see their own names
# and CloudFront serves TLS with its own certificate. Safe to repeat: a
# record that is right is left, one that points elsewhere is changed, and a
# name that holds an address record is refused, since what it serves is a
# person's call. At the apex Cloudflare flattens the CNAME into addresses.
cloudflare_cname() {
  local name="$1" target="$2" existing record_id content proxied others
  say "+ cloudflare GET /zones/$zone_id/dns_records?name=$name"
  if $dry_run; then
    say "+ cloudflare POST or PATCH /zones/$zone_id/dns_records CNAME $name -> $target (DNS only)"
    return
  fi
  existing="$(cloudflare GET "/zones/$zone_id/dns_records?name=$name&per_page=100")"
  others="$(printf '%s' "$existing" | jq -r '[.result[] | select(.type == "A" or .type == "AAAA" or .type == "NS") | "\(.type) \(.content)"] | join(", ")')"
  [ -z "$others" ] || refuse "$name holds $others at Cloudflare, and the site's CNAME cannot sit beside it; remove it by hand if the site is to serve there, then run this again"
  read -r record_id content proxied < <(printf '%s' "$existing" | jq -r '.result[] | select(.type == "CNAME") | "\(.id) \(.content) \(.proxied)"' | head -n 1) || true
  if [ -z "${record_id:-}" ]; then
    say "+ cloudflare POST /zones/$zone_id/dns_records CNAME $name -> $target (DNS only)"
    cloudflare POST "/zones/$zone_id/dns_records" \
      "$(jq -nc --arg name "$name" --arg target "$target" '{type: "CNAME", name: $name, content: $target, proxied: false, ttl: 300}')" >/dev/null
  elif [ "${content%.}" = "${target%.}" ] && [ "$proxied" = "false" ]; then
    say "$name CNAME $target is there already."
  else
    say "+ cloudflare PATCH /zones/$zone_id/dns_records/$record_id CNAME $name -> $target (DNS only; it read $content, proxied $proxied)"
    cloudflare PATCH "/zones/$zone_id/dns_records/$record_id" \
      "$(jq -nc --arg target "$target" '{content: $target, proxied: false, ttl: 300}')" >/dev/null
  fi
}

say "== 3b. The company site's certificate: its validation record at Cloudflare, then its issue"
# The site's name is a record in this zone, not a delegation: the apex
# cannot be delegated, and a delegation of staging.<domain> would hide the
# app.staging and api.staging delegations beneath it. So ACM's validation
# record goes here too, written by this run, which holds the token.
if $dry_run; then
  cloudflare_cname "<each validation record name of $site_domain_name>" "<its value>"
  say "+ aws acm wait certificate-validated --certificate-arn <site_certificate_arn> --region us-east-1"
else
  site_certificate="$(terraform -chdir="$bootstrap_root" output -raw site_certificate_arn)"
  while read -r name value; do
    cloudflare_cname "$name" "$value"
  done < <(terraform -chdir="$bootstrap_root" output -json site_certificate_validation | jq -r '.[] | "\(.name) \(.value)"')
  status="$(aws acm describe-certificate --certificate-arn "$site_certificate" --region us-east-1 --query Certificate.Status --output text)"
  if [ "$status" = "ISSUED" ]; then
    say "The certificate for $site_domain_name is issued."
  else
    say "+ aws acm wait certificate-validated --certificate-arn $site_certificate --region us-east-1  (ACM reads the record; minutes, as a rule)"
    aws acm wait certificate-validated --certificate-arn "$site_certificate" --region us-east-1
  fi
fi

say "== 3c. The company site's name at Cloudflare: a CNAME to its distribution, once a deploy has made it"
# The distribution is the environment root's, which a deploy applies, and
# its domain is known only after that. So the record is written by the first
# run of this script after a deploy that made it; every run checks it.
say "+ aws cloudfront list-distributions  (the one whose alias is $site_domain_name)"
if $dry_run; then
  cloudflare_cname "$site_domain_name" "<the distribution's domain>"
else
  site_distribution="$(aws cloudfront list-distributions --output text \
    --query "DistributionList.Items[?Aliases.Items != null && contains(Aliases.Items, '$site_domain_name')].DomainName | [0]")"
  if [ -z "$site_distribution" ] || [ "$site_distribution" = "None" ]; then
    say "No distribution serves $site_domain_name yet: the next deploy makes it. Run this script again once that deploy is green, and this step writes $site_domain_name CNAME <its domain>."
  else
    cloudflare_cname "$site_domain_name" "$site_distribution"
  fi
fi

say "== 4. The investigate profile, chained from the Identity Center profile"
aws_dir="$HOME/.aws"
aws_config="$aws_dir/config"
investigate_profile="acme-$environment-investigate"
investigate_role="$(output_of investigate_role_arn)"

has_profile() {
  [ -f "$1" ] && grep -qxF "[$2]" "$1"
}

if has_profile "$aws_config" "profile $investigate_profile"; then
  say "$aws_config already has [profile $investigate_profile]; it is left as it is."
else
  say "+ append to $aws_config:"
  say "  [profile $investigate_profile]"
  say "  role_arn = $investigate_role"
  say "  source_profile = $sso_profile"
  say "  region = $region"
  if ! $dry_run; then
    mkdir -p "$aws_dir"
    printf '\n[profile %s]\nrole_arn = %s\nsource_profile = %s\nregion = %s\n' \
      "$investigate_profile" "$investigate_role" "$sso_profile" "$region" >> "$aws_config"
  fi
fi
has_profile "$aws_config" "profile $sso_profile" || say "note: $aws_config has no [profile $sso_profile]; the investigate profile chains from it, so sign-in (aws sso login --profile $sso_profile) needs it first."

say "== 5. The GitHub environments ($github_environments) of $(config .github_repository) and their variables"
# Each environment holds its own variables under the same names, so a job
# reads AWS_ROLE_ARN and gets the role of the environment it declared, and a
# staging job never holds a production value.
#
# Each environment also deploys from one branch only. The roles' trust names
# the branch too, so this is the second of two fences: a job on any other
# branch is refused a deployment here before it reaches AWS.
create_environment() {
  local github_environment="$1" branch="$2"
  shift 2
  run gh api -X PUT "repos/{owner}/{repo}/environments/$github_environment" \
    -F 'deployment_branch_policy[protected_branches]=false' \
    -F 'deployment_branch_policy[custom_branch_policies]=true' "$@"
  if $dry_run || ! gh api "repos/{owner}/{repo}/environments/$github_environment/deployment-branch-policies" \
      -q '.branch_policies[].name' | grep -qxF "$branch"; then
    run gh api -X POST "repos/{owner}/{repo}/environments/$github_environment/deployment-branch-policies" \
      -f "name=$branch" -f type=branch
  fi
}

set_variables() {
  local github_environment="$1"
  shift
  local pair
  for pair in "$@"; do
    run gh variable set "${pair%%=*}" --env "$github_environment" --body "${pair#*=}"
  done
}

case "$environment" in
  staging)
    deploy_role="$(output_of deploy_role_arn)"
    build_role="$(output_of build_role_arn)"
    # The build jobs declare staging-build and hold a push-only role; the
    # jobs that apply declare staging. Both run on main only.
    create_environment staging-build main
    set_variables staging-build \
      "AWS_ROLE_ARN=$build_role" \
      "ARTIFACTS_BUCKET=$artifacts_bucket"
    create_environment staging main
    set_variables staging \
      "AWS_ROLE_ARN=$deploy_role" \
      "TF_STATE_BUCKET=$state_bucket" \
      "ARTIFACTS_BUCKET=$artifacts_bucket" \
      "API_DOMAIN_NAME=$api_domain_name" \
      "APP_DOMAIN_NAME=$app_domain_name" \
      "SITE_DOMAIN_NAME=$site_domain_name" \
      "ALARM_EMAIL=$alarm_email"
    ;;
  production)
    plan_role="$(output_of plan_role_arn)"
    deploy_role="$(output_of deploy_role_arn)"
    # `production-plan` carries no rule: a reviewer on the plan would hold
    # the plan the reviewer is meant to read. `production` requires the
    # owner, and that reviewer is what gates the deploy credential.
    create_environment production-plan release
    if $dry_run; then
      owner_id="<owner id>"
    else
      owner_id="$(gh api 'repos/{owner}/{repo}' -q .owner.id)"
    fi
    create_environment production release -f 'reviewers[][type]=User' -F "reviewers[][id]=$owner_id"
    set_variables production-plan \
      "AWS_ROLE_ARN=$plan_role" \
      "TF_STATE_BUCKET=$state_bucket" \
      "ARTIFACTS_BUCKET=$artifacts_bucket" \
      "API_DOMAIN_NAME=$api_domain_name" \
      "APP_DOMAIN_NAME=$app_domain_name" \
      "SITE_DOMAIN_NAME=$site_domain_name" \
      "ALARM_EMAIL=$alarm_email"
    set_variables production \
      "AWS_ROLE_ARN=$deploy_role" \
      "TF_STATE_BUCKET=$state_bucket" \
      "ARTIFACTS_BUCKET=$artifacts_bucket"
    ;;
esac

if [ "$environment" = "staging" ]; then
  say "== 5b. The ruleset on main: a merge is a staging deploy, so it lands only through a pull request whose checks passed on its merge with the current main"
  # The checks are ci.yml's job names; a job renamed there is renamed here.
  # They are strict: a pull request merges only on a branch up to date with
  # main, so they ran on its merge with the current main. A merge queue does
  # the same with less waiting, but only an organization's repository has one.
  ruleset="$(jq -n '{
    name: "main: a pull request whose checks passed",
    target: "branch",
    enforcement: "active",
    conditions: {ref_name: {include: ["refs/heads/main"], exclude: []}},
    bypass_actors: [],
    rules: [
      {type: "pull_request", parameters: {
        required_approving_review_count: 0, dismiss_stale_reviews_on_push: false,
        require_code_owner_review: false, require_last_push_approval: false,
        required_review_thread_resolution: false}},
      {type: "required_status_checks", parameters: {
        strict_required_status_checks_policy: true,
        required_status_checks: [
          "fast gate",
          "integration over the compose stack",
          "the telemetry round trip over the devx profile",
          "images build (api)", "images build (maintenance)", "images build (portal)",
          "terraform format and validate (bootstrap/staging)",
          "terraform format and validate (bootstrap/prod)",
          "terraform format and validate (environments/staging)",
          "terraform format and validate (environments/prod)"
        ] | map({context: .})}}
    ]}')"
  if $dry_run; then
    existing=""
  else
    existing="$(gh api 'repos/{owner}/{repo}/rulesets' -q '.[] | select(.name == "main: a pull request whose checks passed") | .id')"
  fi
  if [ -n "$existing" ]; then
    say "+ gh api -X PUT repos/{owner}/{repo}/rulesets/$existing --input <the main ruleset>"
    if ! $dry_run; then printf '%s' "$ruleset" | gh api -X PUT "repos/{owner}/{repo}/rulesets/$existing" --input - >/dev/null; fi
  else
    say "+ gh api -X POST repos/{owner}/{repo}/rulesets --input <the main ruleset>"
    if ! $dry_run; then printf '%s' "$ruleset" | gh api -X POST 'repos/{owner}/{repo}/rulesets' --input - >/dev/null; fi
  fi
fi

say "== 6. The operator's env file for $environment"
# The operator's token is empty: no operator exists until the
# grant-operator workflow has run, and a token is minted, never typed. The
# file never holds a password, a TOTP secret, or the provisioner's write
# token, which has a file of its own that `acme-ops token` writes.
api_url="https://$api_domain_name"
ops_dir="$HOME/.config/acme/ops"
ops_file="$ops_dir/$environment.env"
if [ -f "$ops_file" ]; then
  say "$ops_file exists; it is left as it is."
else
  say "+ write $ops_file (mode 600):"
  say "  ACME_API_URL=$api_url"
  say "  ACME_OPERATOR_TOKEN=         # read; uv run acme-ops token --env $environment --identity operator"
  say "  ACME_ERROR_TRACKER_URL="
  say "  ACME_ERROR_TRACKER_TOKEN="
  say "  ACME_ERROR_TRACKER_ORG=acme          # the product's one project, the same"
  say "  ACME_ERROR_TRACKER_PROJECT=acme      # in every environment; the read filters on environment:$environment"
  if ! $dry_run; then
    mkdir -p "$ops_dir"
    (umask 077; printf 'ACME_API_URL=%s\nACME_OPERATOR_TOKEN=\nACME_ERROR_TRACKER_URL=\nACME_ERROR_TRACKER_TOKEN=\nACME_ERROR_TRACKER_ORG=acme\nACME_ERROR_TRACKER_PROJECT=acme\n' "$api_url" > "$ops_file")
    chmod 600 "$ops_file"
  fi
fi
say "Filled by hand, once the product's project exists in the error tracker (one project for every environment): ACME_ERROR_TRACKER_URL and ACME_ERROR_TRACKER_TOKEN in $ops_file."
say "The provisioner's write token goes in $ops_dir/$environment.provisioner.env, never in $ops_file, which every skill that reads sources: step 8's token command writes it there."

say "== 7. The first deploy, through the pipeline like every other"
case "$environment" in
  staging)
    run gh workflow run deploy-staging.yml --ref main
    say "When that deploy is green, run this script again: step 3c finds the site's distribution and writes $site_domain_name at Cloudflare."
    ;;
  production)
    # Replication copies what staging pushes from the moment it is on, and
    # nothing before, so the first release is a commit staging built after.
    say "Production releases what staging built and replicated, so its first release waits for three things:"
    say "  1. scripts/cloud_create.sh staging, again: it finds production's bucket and turns the replication on."
    say "  2. A merge to main after that: deploy-staging builds it, and its images and portal build replicate here."
    say "  3. gh workflow run release.yml --ref main: the release, planned, approved in the production environment, applied."
    say "  4. scripts/cloud_create.sh production, again, once that release is green: step 3c writes $site_domain_name at Cloudflare."
    ;;
esac

say "== 7b. The providers, by hand, once that deploy has made their secrets (each holds \"off\" until then)"
# Printed, never run: each value is a person's to make in the provider's
# dashboard and to write under their own sign-in. The deploy made the
# secrets; a value written before it would be a secret Terraform does not own.
case "$environment" in
  staging) writer_profile="$sso_profile" ;;
  production) writer_profile="acme-prod-power" ;;
esac
cluster="acme-$environment"
say "Written under $writer_profile, with AWS_ACCESS_KEY_ID and its siblings unset, one value at a time, read with read -rs so none is shown:"
for secret in workos_api_key workos_webhook_secret sentry_dsn; do
  say "  aws secretsmanager put-secret-value --profile $writer_profile --region $region --secret-id acme/$environment/$secret --secret-string \"\$VALUE\""
done
say "  WorkOS first: the grants below sign people up through it. workos_api_key is the Acme App application's API key (Applications, Acme App, API keys), never the environment's; the API refuses to start on another. The bootstrap proves the key, adds https://$app_domain_name/auth/callback to the application's redirects, and names the Redirects tab's checks (the default redirect, https://$app_domain_name/login as the initiate login URI):"
case "$environment" in
  staging) say "    uv run acme-ops workos-bootstrap --environment staging --apply   (WORKOS_API_KEY exported, read with read -rs)" ;;
  production) say "    uv run acme-ops workos-bootstrap --environment production --apply   (WORKOS_PRODUCTION_API_KEY exported, read with read -rs)" ;;
esac
say "  workos_webhook_secret is the signing secret of the endpoint https://$api_domain_name/webhooks/identity, from its page under Webhooks in the WorkOS dashboard."
say "  sentry_dsn is the DSN of the product's one project in the error tracker, the same value in every environment."
say "  Then the tasks read the values at their next start: the next deploy, or now:"
for service in api maintenance; do
  say "    aws ecs update-service --profile $writer_profile --region $region --cluster $cluster --service $service --force-new-deployment"
done
say "  The steps, the key permissions, and the checks: docs/runbooks/providers/workos.md."

case "$environment" in
  staging) grant_branch=main ;;
  production) grant_branch=release ;;
esac
say "== 8. The first operator, the provisioner, and the smoke identity, through the pipeline"
say "Each identity signs up at https://$app_domain_name first, like any person. Then, on $grant_branch:"
say "  gh workflow run grant-operator.yml --ref $grant_branch -f environment=$environment -f email=<operator> -f permission=read"
say "    The operator enrols the second factor at the operator plane's first sign-in, then runs, in their own terminal:"
say "    uv run acme-ops token --env $environment --identity operator"
say "  gh workflow run grant-operator.yml ... -f email=<provisioner> -f permission=write -f mint_token=provisioner"
case "$environment" in
  staging) own_profile="" ;;
  production) own_profile=" --profile acme-prod-power" ;;
esac
say "    then, under your own sign-in and never an agent's: uv run acme-ops token --env $environment --identity provisioner$own_profile"
say "    (the token lasts an hour: mint it again, with mint_token alone, before each traffic run)"
say "  gh workflow run grant-operator.yml ... -f email=<smoke identity> -f permission=read"
say "    then: gh variable set SMOKE_EMAIL --env $environment --body <smoke identity>"
say "Until SMOKE_EMAIL is set, every deploy's smoke step is skipped, and says so."

say "== 9. When a deploy is green after that, the smoke test: the deploy ran it; one request by hand, then its signals by request id"
say "id=\$(curl -s -o /dev/null -D - $api_url/v1/me | awk 'tolower(\$1) == \"x-request-id:\" { print \$2 }' | tr -d '\\r')"
say "uv run acme-ops signals check --env $environment --request-id \"\$id\""
say "The environment root is $environment_root; the operator's file is $ops_file."
