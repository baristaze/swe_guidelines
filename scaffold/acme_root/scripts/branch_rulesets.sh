#!/usr/bin/env bash
# Set the rulesets that keep the long-lived branches: `main`, `release`, and
# `scaffold` are never deleted and never rewritten. Each branch gets a ruleset
# of its own, "<branch>: never deleted, never rewritten", that blocks a
# deletion and a force push, with no bypass actor. It holds for a branch that
# does not exist yet, from its first push.
#
# These sit beside the rulesets scripts/cloud_create.sh sets, which hold how
# `main` and `release` move: a ruleset adds its rules to the others on the
# same branch. So a reset of `release` (the deploy runbook) turns off both of
# its rulesets for that push.
#
# Run it once per repository, by a repository administrator, from its
# checkout; a run again updates each ruleset in place. `--dry-run` prints each
# call and each ruleset, reads nothing, and writes nothing.
#
# Usage: scripts/branch_rulesets.sh [--dry-run]
set -euo pipefail

usage() {
  # The header comment, up to the first line that is not one.
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0" >&2
  exit 2
}

dry_run=false
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
  shift
done

for branch in main release scaffold; do
  name="$branch: never deleted, never rewritten"
  ruleset="$(jq -n --arg name "$name" --arg ref "refs/heads/$branch" '{
    name: $name,
    target: "branch",
    enforcement: "active",
    conditions: {ref_name: {include: [$ref], exclude: []}},
    bypass_actors: [],
    rules: [{type: "deletion"}, {type: "non_fast_forward"}]
  }')"
  if $dry_run; then
    existing=""
  else
    existing="$(gh api 'repos/{owner}/{repo}/rulesets' -q ".[] | select(.name == \"$name\") | .id")"
  fi
  if [ -n "$existing" ]; then
    echo "+ gh api -X PUT repos/{owner}/{repo}/rulesets/$existing --input <the $branch ruleset>"
    if ! $dry_run; then printf '%s' "$ruleset" | gh api -X PUT "repos/{owner}/{repo}/rulesets/$existing" --input - >/dev/null; fi
  else
    echo "+ gh api -X POST repos/{owner}/{repo}/rulesets --input <the $branch ruleset>"
    if ! $dry_run; then printf '%s' "$ruleset" | gh api -X POST 'repos/{owner}/{repo}/rulesets' --input - >/dev/null; fi
  fi
  if $dry_run; then printf '%s\n' "$ruleset" | jq -c .; fi
done
