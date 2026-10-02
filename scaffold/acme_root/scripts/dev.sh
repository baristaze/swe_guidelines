#!/usr/bin/env bash
# Start every Acme application process on the host against the local
# compose stack. Ctrl-C stops them all.
set -euo pipefail
shopt -s extglob  # the dotenv reader below trims with +(...)
cd "$(dirname "$0")/.."

# .env is a dotenv file, not a shell script: `make up` copies .env.example
# over, and values there are unquoted words (SEED_NAME=Local Owner) and JSON
# (ACME_CORS_ORIGINS=["http://..."]). Sourcing it runs the second word as a
# command and strips the quotes out of the first. Read it the way
# pydantic-settings and compose do instead: one KEY=VALUE per line, comments
# and blanks skipped, one layer of matching quotes removed, no expansion. The
# file is defaults: a variable the shell exports wins over it, as it does for
# the settings, for compose, and for the Makefile.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    [ "${line#*=}" = "$line" ] && continue
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in ''|*[!A-Za-z0-9_]*) continue ;; esac
    [ -n "${!key+set}" ] && continue
    case "$value" in
      \"*\") value="${value:1:${#value}-2}" ;;
      \'*\') value="${value:1:${#value}-2}" ;;
      # An unquoted value ends where an inline comment starts, and carries no
      # trailing blanks, as dotenv reads it. .env.example carries such
      # comments (`...:54318  # Jaeger in the devx profile`) and these values
      # are exported, so without this the comment travels into the setting and
      # beats the .env the process reads for itself.
      *) value="${value%%+([[:space:]])#*}"; value="${value%%+([[:space:]])}" ;;
    esac
    export "$key=$value"
  done < .env
fi

# A tool that is missing stops the script here, before anything starts.
# Node 25 and later ship no corepack, so a Node installed or switched to has
# no pnpm until `make setup` installs it.
for tool in uv pnpm; do
  if ! command -v "$tool" >/dev/null; then
    echo "dev.sh: $tool is not on PATH; run \`make setup\`" >&2
    exit 1
  fi
done

# A process stops with every process under it: uv and pnpm each start the
# server as a child, and a signal to the parent alone leaves the child
# running once the script exits.
pids=()
stop_tree() {
  local child
  for child in $(pgrep -P "$1" || true); do
    stop_tree "$child"
  done
  kill "$1" 2>/dev/null || true
}
cleanup() {
  local pid
  for pid in ${pids[@]+"${pids[@]}"}; do
    stop_tree "$pid"
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

uv run --package acme-api acme-api serve --port "${ACME_PORT:-8000}" &
pids+=($!)
uv run --package acme-maintenance acme-maintenance serve &
pids+=($!)
pnpm --filter @acme/portal dev &
pids+=($!)

# A process that fails stops the others and the script, with its status, so
# it never leaves the rest running without it; one that exits 0 is done. A
# poll, never `wait -n`, which the bash a macOS ships does not have.
while [ ${#pids[@]} -gt 0 ]; do
  running=()
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      running+=("$pid")
      continue
    fi
    status=0
    wait "$pid" || status=$?
    if [ "$status" -ne 0 ]; then
      echo "dev.sh: a process exited with status $status; stopping the others" >&2
      exit "$status"
    fi
  done
  pids=(${running[@]+"${running[@]}"})
  if [ ${#pids[@]} -gt 0 ]; then
    sleep 1
  fi
done
