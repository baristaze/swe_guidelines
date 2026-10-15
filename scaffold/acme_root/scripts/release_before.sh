#!/usr/bin/env bash
# The release before, on this branch's schema (ADR 0084). A rollout runs the
# release before beside this one, on this one's schema, and the fast rollback
# returns to it. So a migration the release before cannot run on fails its
# writes for the minutes of the roll, and for good after a rollback.
#
#   scripts/release_before.sh [--running-stack] [<base>]    # <base> defaults to origin/main
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
#
# With --running-stack it starts nothing and runs no compose command, for a
# stack another process started and keeps: from this checkout, `make
# infra-up` would recreate that stack under the same compose project. Each
# database URL, the environment's, else .env's, else .env.example's, is then
# this run's own, never the stack's, since every table it runs on is emptied:
# one that names the database .env.example gives, the stack's own, is refused.
# Without a .env, the URLs come from the environment.
set -euo pipefail
cd "$(dirname "$0")/.."

running_stack=""
reset="make infra-reset"
if [ "${1:-}" = "--running-stack" ]; then
  running_stack=1
  reset="make this run's databases anew"
  shift
fi
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
    echo "release before: the version records still name a release before; run this again, or ${reset}" >&2
  fi
  rm -rf "$work"
  git worktree prune
}
trap cleanup EXIT

if [ -n "$running_stack" ]; then
  while read -r name; do
    stack="$(sed -n "s/^${name}=//p" .env.example | tail -n 1)"
    url="${!name:-}"
    if [ -z "$url" ] && [ -f .env ]; then
      url="$(sed -n "s/^${name}=//p" .env | tail -n 1)"
    fi
    url="${url:-$stack}"
    url="${url%%\?*}"
    stack="${stack%%\?*}"
    if [ "${url##*/}" = "${stack##*/}" ]; then
      echo "release before: ${name} names ${url##*/}, the stack's own database; with --running-stack, name a database of this run's own in each ACME_DATABASE_* URL, since the run empties every table it reaches" >&2
      exit 1
    fi
  done < <(sed -n 's/^\(ACME_DATABASE_[A-Z_]*URL[A-Z_]*\)=.*/\1/p' .env.example)
  echo "release before: the stack is running, so it starts nothing and runs on the databases this run names"
else
  make --no-print-directory infra-up
fi
make --no-print-directory migrate

failed=()
for i in "${!releases[@]}"; do
  commit="${releases[$i]}"
  label="${labels[$i]}"
  tree="$work/${commit:0:12}"
  echo "release before: ${label}, ${commit:0:12}"
  git worktree add -q --detach "$tree" "$commit"
  (cd "$tree" && uv sync --all-packages --quiet)
  if [ -f .env ]; then
    cat "$tree/.env.example" .env > "$tree/.env"
  else
    cp "$tree/.env.example" "$tree/.env"
  fi
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
