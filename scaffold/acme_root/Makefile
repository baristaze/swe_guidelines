# Acme developer entry points. `make help` lists them.
.DEFAULT_GOAL := help
SHELL := /bin/bash

# The knobs live in one place: .env.example carries every default and .env
# (which `make up` copies from it) the developer's overrides. Make reads both
# so `make seed` and `make urls` say what the compose stack does, and compose
# reads the same two files for the host ports.
#
# Both files are defaults. A variable exported in the shell wins over them,
# as it does for the settings, for compose, and for scripts/dev.sh, so the
# four ACME_DATABASE_* URLs exported at another database take migrate,
# migrate-check, seed, and test-integration there. Make lets an included
# file beat the environment, so each name the files set and the shell
# exports is kept aside before the includes and put back after them.
ENV_FILES := .env.example $(wildcard .env)
EXPORTED := $(foreach name,$(sort $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(ENV_FILES))),$(if $(filter environment,$(origin $(name))),$(name)))
$(foreach name,$(EXPORTED),$(eval EXPORTED.$(name) := $$(value $(name))))
include $(ENV_FILES)
$(foreach name,$(EXPORTED),$(eval $(name) := $$(EXPORTED.$(name))))
COMPOSE_ENV := $(foreach file,$(ENV_FILES),--env-file $(file))
# The one switch between hosts: on Linux, host.docker.internal is the bridge
# gateway, which a process on 127.0.0.1 never answers, so the devx collector
# that scrapes the host processes joins the host's network there.
COMPOSE_LINUX := $(if $(filter Linux,$(shell uname -s)),-f deployment/local/docker-compose.linux.yml)
COMPOSE ?= docker compose $(COMPOSE_ENV) -f deployment/local/docker-compose.yml $(COMPOSE_LINUX)
COMPOSE_FULL := $(COMPOSE) -f deployment/local/docker-compose.full.yml
ROLES := core activity queue admin
# GlitchTip's seed is a one-shot that no service depends on, and `up --wait`
# counts a one-shot's exit as a failure even when it exits 0. So the seed
# sits in the devx-seed profile, which `up` leaves out, and runs here, after
# the wait has seen GlitchTip healthy: in the foreground, removed when done,
# and with its own exit code as the step's. It is idempotent, so every `up`
# runs it again. Prefixed with the compose command of the target that runs it.
GLITCHTIP_SEED := --profile devx --profile devx-seed run --rm --no-deps -T glitchtip-seed
# The traffic run's knobs: `make traffic PROFILE=light DURATION=30`.
PROFILE ?= light
DURATION ?= 30
# Where `make collector-scrape` points the devx collector's api target; the
# default is the knob the compose stack reads, so with no argument the target
# goes back to where `make devx-up` put it.
SCRAPE_PORT ?= $(ACME_COLLECTOR_SCRAPE_PORT)

# The guideline's static checker, at the tag of the guideline this project
# follows, on the project's Python: the checker refuses a Python older than
# .python-version. Offline, point it at a checkout:
# `make arch-check ARCH_CHECK="python3 ../swe_guidelines/checkers/arch_check.py"`.
ARCH_CHECK ?= uvx --python "$(shell cat .python-version)" --from "git+https://github.com/baristaze/swe_guidelines@v0.40.0\#subdirectory=checkers" arch-check

.PHONY: help setup up down reset urls infra-up buckets devx-up stack-up infra-down infra-reset collector-scrape migrate seed migrate-check benchmark-boot check lint format-check typecheck arch-check test-unit test-integration test-telemetry traffic openapi

# This Makefile alone, never $(MAKEFILE_LIST): the includes above put
# .env.example and .env in that list, and grep prefixes every match with the
# file it came from once it is given more than one, so every target would be
# printed under the name "Makefile".
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

# A tree copied under a longer name has longer lines and its imports in
# another order, so setup formats the Python once; on a formatted tree it
# changes nothing.
setup: ## Install every Python and TypeScript dependency, and format the Python
	uv sync --all-packages
	@if [ -f pnpm-workspace.yaml ] && [ -d apps ]; then pnpm install; fi
	uv run ruff check --fix --quiet --select I .
	uv run ruff format --quiet .

# The one-command session. `up` starts the data services first, migrates and
# seeds them, then starts the app containers and the dashboards; it is safe
# to rerun and keeps data. `down` keeps data; `reset` wipes it and starts over.
# `up --wait` waits on the long-running services only, and GlitchTip's seed
# runs to completion after it as a step of its own (see GLITCHTIP_SEED), so
# either target exits 0 when every service is up and the seed succeeded.
up: .env ## Everything: stack, migrations, seed, app containers, dashboards; keeps data
	$(COMPOSE) up -d --wait
	$(MAKE) --no-print-directory buckets
	$(MAKE) --no-print-directory migrate seed
	$(COMPOSE_FULL) --profile devx up -d --build --wait
	$(COMPOSE_FULL) $(GLITCHTIP_SEED)
	@$(MAKE) --no-print-directory urls

down: ## Stop every local container; the data stays for the next `make up`
	$(COMPOSE_FULL) --profile devx --profile devx-seed down --remove-orphans

# Names every file and profile so no container of any of them is left behind.
reset: ## Wipe every container and all local data, then `make up`
	$(COMPOSE_FULL) --profile devx --profile devx-seed down -v --remove-orphans
	$(MAKE) --no-print-directory up

urls: ## Print the local URLs and the seeded sign-ins
	@echo ""
	@echo "  Portal         http://localhost:$(ACME_PORTAL_PORT)   /login through WorkOS; /login/dev as $(SEED_EMAIL) or $(SEED_MEMBER_EMAIL)"
	@echo "                 two orgs: $(SEED_ADMIN_EMAIL) at /login/dev   owner of $(SEED_SECOND_ORG), admin of $(SEED_ORG)"
	@echo "  API docs       http://127.0.0.1:$(ACME_PORT)/docs"
	@echo "  pgweb          http://localhost:$(ACME_PGWEB_PORT)"
	@echo "  Valkey Admin   http://localhost:$(ACME_VALKEY_ADMIN_PORT)"
	@echo "  ElasticMQ UI   http://localhost:$(ACME_ELASTICMQ_UI_PORT)"
	@echo "  Grafana        http://localhost:$(ACME_GRAFANA_PORT)   metrics dashboards, no sign-in"
	@echo "  Prometheus     http://localhost:$(ACME_PROMETHEUS_PORT)"
	@echo "  Jaeger         http://localhost:$(ACME_JAEGER_PORT)"
	@echo "  GlitchTip      http://localhost:$(ACME_GLITCHTIP_PORT)   admin@example.test / acme-local"
	@echo "  MinIO console  http://localhost:$(ACME_MINIO_CONSOLE_PORT)   acme / acme-minio-local"
	@echo ""

.env:
	cp .env.example .env

infra-up: ## Start Postgres, the cache, the queue, and the object store
	$(COMPOSE) up -d --wait
	$(MAKE) --no-print-directory buckets

# One bucket per member of acme.infra.buckets.Buckets, named <prefix>-<bucket>
# as the S3 impl names them; the cloud's are Terraform's. MinIO ships `mc`.
buckets: ## Create the object store's buckets in MinIO, if they are missing
	$(COMPOSE) exec -T minio sh -c 'mc alias set local http://127.0.0.1:9000 acme acme-minio-local >/dev/null && mc mb --ignore-existing local/acme-local-user-file-uploads local/acme-local-exports'

devx-up: ## The local stack plus developer dashboards (pgweb, Valkey Admin, ElasticMQ UI, Prometheus and its collector, Grafana, Jaeger, GlitchTip)
	$(COMPOSE) --profile devx up -d --wait
	$(COMPOSE) $(GLITCHTIP_SEED)

stack-up: ## The local stack plus the api, maintenance, and portal containers
	$(COMPOSE_FULL) up -d --build --wait

# The shortcuts above are the developer's path; these are the steps they wrap,
# which is what CI and a developer debugging one of them run one at a time.
infra-down: ## Stop the dependencies; the data stays, as after `make down`
	$(COMPOSE) down --remove-orphans

infra-reset: ## Recreate the dependencies with their volumes removed, and nothing else
	$(COMPOSE) down -v --remove-orphans
	$(MAKE) --no-print-directory infra-up

# The devx collector scrapes one host target for the api, and a collector
# reads a changed config only when it is recreated, so the two go together
# here. With no argument it puts the collector back on the .env port, which
# is what the telemetry round trip runs when it is done with the free port
# it took.
collector-scrape: ## Point the devx collector at SCRAPE_PORT on the host and recreate it
	ACME_COLLECTOR_SCRAPE_PORT=$(SCRAPE_PORT) $(COMPOSE) --profile devx up -d --wait --no-deps otel-collector

# The local targets (migrate, migrate-check, seed, test-integration) refuse a
# database whose host is not local, so a stray .env never points them at a
# shared one; the cloud runs `acme-api migrate` without --local. The master
# makes the three logins first, and every migration runs as the migration
# login; both steps are safe to repeat.
migrate: ## Make the database logins, then apply every role's migration chain to the local database
	uv run --package acme-om python -m acme.om.storage.migrate ensure-logins --local
	uv run --package acme-om python -m acme.om.storage.migrate upgrade --all --local

# The SEED_* values come from .env (or .env.example); override any of them
# there or on the command line, e.g. `make seed SEED_EMAIL=me@example.test`.
# The admin owns the second org and joins the first as an admin: one person
# with two memberships, each under a different role.
# Then the two local operators, the platform's own identities of
# acme.ops.environments.LOCAL_OPERATORS: a read operator and the
# provisioner, which writes. Their tokens go into the owner-only
# ~/.config/acme/ops/local.env beside the local stack's addresses, when that
# file is absent; `uv run acme-ops token --env local --identity
# operator|provisioner` mints a fresh one into it, since each lasts an hour.
seed: ## Create two local orgs with an owner, a member, and an admin of both to sign in as, and the two local operators; a no-op once they exist
	uv run --package acme-api acme-api bootstrap --if-absent \
		--org "$(SEED_ORG)" --slug "$(SEED_SLUG)" --name "$(SEED_NAME)" \
		--email "$(SEED_EMAIL)"
	uv run --package acme-api acme-api add-member --slug "$(SEED_SLUG)" \
		--name "$(SEED_MEMBER_NAME)" --email "$(SEED_MEMBER_EMAIL)"
	uv run --package acme-api acme-api bootstrap --if-absent \
		--org "$(SEED_SECOND_ORG)" --slug "$(SEED_SECOND_SLUG)" --name "$(SEED_ADMIN_NAME)" \
		--email "$(SEED_ADMIN_EMAIL)"
	uv run --package acme-api acme-api add-member --slug "$(SEED_SLUG)" --role admin \
		--name "$(SEED_ADMIN_NAME)" --email "$(SEED_ADMIN_EMAIL)"
	uv run --package acme-api acme-api grant-operator --permission read \
		--email operator@platform.acme.invalid
	uv run --package acme-api acme-api grant-operator --permission write \
		--email provisioner@platform.acme.invalid
	@if [ -e "$$HOME/.config/acme/ops/local.env" ]; then \
		echo "~/.config/acme/ops/local.env is there already; \`uv run acme-ops token --env local --identity operator|provisioner\` writes a fresh token into it"; \
	else \
		uv run --package acme-ops acme-ops token --env local --identity operator && \
		uv run --package acme-ops acme-ops token --env local --identity provisioner; \
	fi

# The ORM-versus-schema check needs a migrated database, which the fast gate
# cannot reach, so `check` does not run it; CI's integration job runs it
# right after `make migrate`, and the downgrade-then-upgrade round trip stays
# in the integration tests.
migrate-check: ## Compare every role's ORM metadata with the migrated schema
	uv run --package acme-om python -m acme.om.storage.migrate check --all --local

# What a process pays to boot: imports once, then microseconds per root (ADR 0007).
benchmark-boot: ## Time the imports and the construction of every root
	uv run --package acme-api python scripts/benchmark_boot.py

check: lint format-check typecheck arch-check test-unit ## The fast local gate
	@if [ -d apps ]; then pnpm run lint && pnpm run typecheck && pnpm run test; fi

lint: ## Ruff lint
	uv run ruff check .

format-check: ## Ruff format, check only
	uv run ruff format --check .

typecheck: ## Pyright over every distribution and the scripts
	uv run pyright

arch-check: ## The guideline's static checks, configured in pyproject.toml
	$(ARCH_CHECK)

test-unit: ## Unit tests over the memory impls
	uv run pytest -q -m "not integration and not e2e and not telemetry"

test-integration: ## Integration tests over the compose stack
	uv run pytest -q -m integration

# The round trip: a real API process on a free port, the devx collector aimed
# at that port while it runs, the exporter and the DSN set, one session of
# traffic, then every signal read back by request id through the devx stores.
# Needs `make devx-up` and `make migrate seed`; skips, naming what is missing,
# when it cannot run. With ACME_TELEMETRY_REQUIRED=1 the same miss is a
# failure, which is how CI runs it.
test-telemetry: ## The telemetry round trip over the devx profile
	uv run pytest -q -m telemetry

# The gate's sanity run: the light profile for thirty seconds over the seeded
# org, against the API on ACME_PORT. Proves the wiring, says nothing about capacity.
traffic: ## Drive light traffic at the local API (PROFILE=light DURATION=30)
	uv run acme-ops traffic --env local --profile $(PROFILE) --duration $(DURATION) --orgs 0

# One committed document, clients/typescript/openapi.json; both generated type
# sets come from it: the TypeScript client's schema.d.ts and the Python
# client's schema.py.
openapi: ## Emit the API document and regenerate the TypeScript and the Python client's types
	uv run --package acme-api acme-api openapi --out clients/typescript/openapi.json
	pnpm --filter @acme/client generate
	uv run datamodel-codegen --input clients/typescript/openapi.json --input-file-type openapi \
		--output clients/python/src/acme/client/schema.py \
		--output-model-type pydantic_v2.BaseModel --target-python-version 3.14 \
		--use-standard-collections --use-union-operator --use-annotated \
		--enum-field-as-literal none --use-schema-description --disable-timestamp \
		--formatters ruff-format --formatters ruff-check
