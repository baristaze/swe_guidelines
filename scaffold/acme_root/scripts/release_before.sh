#!/usr/bin/env bash
# The release before, on this branch's schema (ADR 0084). A rollout runs the
# release before beside this one, on this one's schema, and the fast rollback
# returns to it. So a migration the release before cannot run on fails its
# writes for the minutes of the roll, and for good after a rollback.
#
#   scripts/release_before.sh [<base>]    # <base> defaults to origin/main
#
# A branch that changes nothing under om/migrations since its merge base with
# <base> ends here, green, with no database. Otherwise it brings the compose
# stack up, migrates every chain to this branch's head, and runs the
# integration suite of each release before against that schema:
# - the merge base, the release staging runs while this branch rolls out;
# - the tip of origin/release, the release production runs and the fast
#   rollback's target, when that branch exists and is not the merge base.
# Each release is checked out beside this one with its own environment. Each
# role's version record is stamped to that release's head first: its suite
# migrates to its own head, and its chains hold no revision of this branch.
# Its suite reads this branch's .env over its own, so it reaches this stack.
# It runs without its own migration tests, which hold its chain to its ORM and
# fail on any newer schema, and without each test
# scripts/release_before_deselect.txt names. On the way out every record is
# stamped back to this branch's heads, which `make migrate` reads next.
#
# Like `make test-integration`, it empties every table of the local stack.
set -euo pipefail
cd "$(dirname "$0")/.."

base="${1:-origin/main}"
merge_base="$(git merge-base "$base" HEAD)"
changed="$(git diff --name-only "$merge_base" HEAD -- om/migrations)"
if [ -z "$changed" ]; then
  echo "release before: no migration changed since ${merge_base:0:12}, so there is nothing to run"
  exit 0
fi
printf 'release before: migrations changed since %s:\n%s\n' "${merge_base:0:12}" "$changed"

releases=("$merge_base")
labels=("the merge base")
if release="$(git rev-parse -q --verify 'origin/release^{commit}')"; then
  if [ "$release" = "$merge_base" ]; then
    echo "release before: origin/release is the merge base, so one release runs"
  else
    releases+=("$release")
    labels+=("origin/release")
  fi
else
  echo "release before: there is no origin/release yet, so the merge base runs alone"
fi

deselect=(--deselect om/tests/integration/test_migrations.py)
while read -r line || [ -n "$line" ]; do
  case "$line" in '' | '#'*) continue ;; esac
  deselect+=(--deselect "$line")
done < scripts/release_before_deselect.txt

migrate=(uv run --package acme-om python -m acme.om.storage.migrate)
here="$PWD"
work="$(mktemp -d)"
stamped=""
cleanup() {
  if [ -n "$stamped" ] && ! "${migrate[@]}" stamp --all --heads-of "$here"; then
    echo "release before: the version records still name a release before; run this again, or make infra-reset" >&2
  fi
  rm -rf "$work"
  git worktree prune
}
trap cleanup EXIT

make --no-print-directory infra-up
make --no-print-directory migrate

failed=()
for i in "${!releases[@]}"; do
  commit="${releases[$i]}"
  label="${labels[$i]}"
  tree="$work/${commit:0:12}"
  echo "release before: ${label}, ${commit:0:12}"
  git worktree add -q --detach "$tree" "$commit"
  (cd "$tree" && uv sync --all-packages --quiet)
  cat "$tree/.env.example" .env > "$tree/.env"
  stamped=1
  "${migrate[@]}" stamp --all --heads-of "$tree"
  if (cd "$tree" && uv run pytest -q -m integration -p no:cacheprovider "${deselect[@]}"); then
    echo "release before: ${label} passes on this schema"
  else
    echo "release before: ${label} fails on this schema" >&2
    failed+=("$label")
  fi
done

if [ "${#failed[@]}" -gt 0 ]; then
  printf -v names '%s, ' "${failed[@]}"
  echo "release before: failed on this schema: ${names%, }. A test it cannot pass by design is named in scripts/release_before_deselect.txt, with its reason." >&2
  exit 1
fi
echo "release before: every release before passes on this schema"
