#!/usr/bin/env bash
# Publish a built static site, the portal or the company site, to the bucket
# and distribution an environment root just applied. Used by
# deploy-staging.yml and deploy-production.yml after `terraform apply`, with
# the build every environment of that commit receives; runs from the repo root
# with the AWS session that applied the environment.
#
#   scripts/deploy_static.sh portal deployment/terraform/environments/staging apps/portal/dist
#   scripts/deploy_static.sh site deployment/terraform/environments/staging apps/site/dist/staging
#
# The root's outputs name the bucket, the distribution, and the address:
# <name>_bucket, <name>_distribution_id, <name>_url.
set -euo pipefail

usage="usage: deploy_static.sh <portal|site> <terraform environment dir> <built dir>"
name="${1:?$usage}"
root="${2:?$usage}"
dist="${3:?$usage}"
case "$name" in
  portal|site) ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
[ -f "$dist/index.html" ] || { echo "no index.html in $dist" >&2; exit 1; }

bucket="$(terraform -chdir="$root" output -raw "${name}_bucket")"
distribution="$(terraform -chdir="$root" output -raw "${name}_distribution_id")"

# Hashed assets first, cached for a year; the previous build's assets stay, so
# a tab still running the old index.html can load its lazy chunks. A hashed
# name holds one content, so a key the bucket has is the same file: only the
# size is compared. The files come fresh from a download, so a comparison by
# time would upload every one of them again.
aws s3 sync "$dist/assets" "s3://$bucket/assets" --size-only \
  --cache-control "public, max-age=31536000, immutable"

# Then the entry points, which browsers revalidate on every load: every file
# outside assets/, but config.json, which is in no build (Terraform writes the
# portal's per environment). One is uploaded only when its content differs
# from the bucket's copy. The bucket encrypts with S3-managed keys, so a
# single-part object's ETag is the MD5 of its content; an entry point is far
# below the CLI's multipart threshold, and an ETag of any other shape counts
# as a difference.
etags="$(aws s3api list-objects-v2 --bucket "$bucket" \
  --query "Contents[?!starts_with(Key, 'assets/')].[Key, ETag]" --output text)"

changed=()
while IFS= read -r file; do
  key="${file#./}"
  local_md5="$(openssl dgst -md5 -r "$dist/$key" | cut -d' ' -f1)"
  remote_etag="$(awk -F'\t' -v key="$key" '$1 == key { gsub(/"/, "", $2); print $2 }' <<<"$etags")"
  if [ "$local_md5" != "$remote_etag" ]; then
    aws s3 cp "$dist/$key" "s3://$bucket/$key" --cache-control "no-cache"
    changed+=("$key")
  fi
done < <(cd "$dist" && find . -type f -not -path "./assets/*" -not -name config.json | LC_ALL=C sort)

# Edges drop their copy of each entry point that changed, and of the root
# when index.html did. An unchanged one is left alone: its content at the
# edge is already this build's. The entry points carry `no-cache`, which the
# distribution's cache policy caps at its one-second minimum TTL, so the
# invalidation is what makes a change reach every edge at once, and the
# wait is what lets the run say it has.
if [ "${#changed[@]}" -eq 0 ]; then
  echo "$name: every entry point is already this build's; nothing to upload or invalidate"
else
  paths=()
  for key in "${changed[@]}"; do
    [ "$key" = "index.html" ] && paths+=("/")
    paths+=("/$key")
  done
  echo "$name: invalidating ${paths[*]}"
  invalidation="$(aws cloudfront create-invalidation --distribution-id "$distribution" \
    --paths "${paths[@]}" --query 'Invalidation.Id' --output text)"
  aws cloudfront wait invalidation-completed --distribution-id "$distribution" --id "$invalidation"
fi

echo "$name published to $(terraform -chdir="$root" output -raw "${name}_url")"
