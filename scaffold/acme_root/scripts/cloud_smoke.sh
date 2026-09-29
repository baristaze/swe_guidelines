#!/usr/bin/env bash
# The deploys' smoke step: mint the smoke identity's operator token through
# the grant task, read it from its secret into this shell alone, and call
# one authenticated route of the operator plane with it through the edge.
# deploy-staging.yml and deploy-production.yml run it after the rollout,
# from the repo root with the deploy role's session and the root
# initialized. The secret is the root's `operator_token_secret_names.smoke`,
# `acme-<env>-smoke-token`; the deploy role reads it and no other secret
# value.
#
#   scripts/cloud_smoke.sh deployment/terraform/environments/staging staging api.staging.acme.example
#
# SMOKE_EMAIL names the smoke identity. While it is empty no grant has named
# one yet, and the step is skipped with a notice rather than failed. The
# token is masked before anything could print it, and the answer's body,
# which names the identity, is never printed; the request id is.
set -euo pipefail

root="${1:?usage: cloud_smoke.sh <terraform environment dir> <environment> <api domain>}"
environment="${2:?usage: cloud_smoke.sh <terraform environment dir> <environment> <api domain>}"
api_domain_name="${3:?usage: cloud_smoke.sh <terraform environment dir> <environment> <api domain>}"
summary="${GITHUB_STEP_SUMMARY:-/dev/stdout}"

if [ -z "${SMOKE_EMAIL:-}" ]; then
  echo "::notice::the smoke test is skipped: $environment's SMOKE_EMAIL variable is empty. Grant the smoke identity read with grant-operator.yml, then set SMOKE_EMAIL on the $environment environment."
  echo "The smoke test is skipped: no smoke identity is named yet (the \`$environment\` environment's \`SMOKE_EMAIL\`)." >> "$summary"
  exit 0
fi
echo "::add-mask::$SMOKE_EMAIL"

scripts/cloud_grant.sh "$root" --email "$SMOKE_EMAIL" --mint-token smoke

secret="$(terraform -chdir="$root" output -json operator_token_secret_names | jq -er .smoke)"
token="$(aws secretsmanager get-secret-value --secret-id "$secret" \
  --query SecretString --output text)"
echo "::add-mask::$token"
if [ -z "$token" ] || [ "$token" = "None" ]; then
  echo "::error::$secret holds no token after the mint"
  exit 1
fi

headers="$(mktemp)"
trap 'rm -f "$headers"' EXIT
status="$(curl -sS --max-time 20 -o /dev/null -D "$headers" -w '%{http_code}' \
  -H "Authorization: Bearer $token" -H "X-App: admin" -H "X-App-Version: smoke@${GITHUB_SHA:-local}" \
  "https://$api_domain_name/v1/admin/me")"
request_id="$(awk 'tolower($1) == "x-request-id:" { print $2 }' "$headers" | tr -d '\r')"

if [ "$status" != "200" ]; then
  echo "::error::GET https://$api_domain_name/v1/admin/me with the smoke identity's token answered $status (request id ${request_id:-none})"
  exit 1
fi
echo "The smoke test passed: the smoke identity's token reads \`/v1/admin/me\` through the edge (request id \`$request_id\`)." >> "$summary"
