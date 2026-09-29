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

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

uv run --package acme-api acme-api serve --port "${ACME_PORT:-8000}" &
pids+=($!)
uv run --package acme-maintenance acme-maintenance serve &
pids+=($!)
pnpm --filter @acme/portal dev &
pids+=($!)

wait
