# Local stack

Everything that runs locally, from two compose files and a profile. Every
command below runs from the repository root.

| File or profile | Adds |
|-----------------|------|
| `docker-compose.yml` | Postgres, Valkey, ElasticMQ (its queues in `elasticmq/elasticmq.conf`), MinIO |
| `docker-compose.full.yml` | the `api`, `maintenance`, and `portal` containers, built from the working tree |
| profile `devx` | pgweb, Valkey Admin, ElasticMQ UI, Prometheus, the OpenTelemetry collector for host processes, Grafana, Jaeger, GlitchTip |
| `docker-compose.linux.yml` | Linux only, added by the Makefile: the collector on the host's network |

The compose project is `acme`, so containers are named `acme-<service>-1`
and volumes `acme_<volume>`. `COMPOSE_PROJECT_NAME` sets another.

MinIO serves no public image, so the object store is `pgsty/minio`, the
community fork of the same server. It reads the same settings and data
directory, bundles `mc`, and keeps the web console. The compose file pins
a dated release tag and its digest; a bump takes the newest `RELEASE.*`
tag and its digest from `docker buildx imagetools inspect pgsty/minio:<tag>`.

## Ports

Every host port is a knob in `.env.example`, on 127.0.0.1 only. Set one in
`.env` when it clashes, or to run a second stack beside this one. A URL
knob names its port as a number, so it changes with the port
(`.env.example` lists which). `make urls` prints the browser URLs.

| Service | Knob, default | Inside the network | Sign-in |
|---------|---------------|--------------------|---------|
| portal | `ACME_PORTAL_PORT`, 55173 | `portal:8080` | `/login/dev` by address after `make seed`; `/login` through WorkOS once `ACME_WORKOS_API_KEY` is set |
| api (`/docs`, `/metrics`, `/healthz`) | `ACME_PORT`, 8000 | `api:8000` | |
| postgres | `ACME_POSTGRES_PORT`, 55432 | `postgres:5432` | database `acme`; the logins are below |
| valkey | `ACME_VALKEY_PORT`, 56379 | `valkey:6379` | none |
| elasticmq (SQS) | `ACME_ELASTICMQ_PORT`, 59324 | `elasticmq:9324` | any key |
| minio (S3) | `ACME_MINIO_PORT`, 59000 | `minio:9000` | `acme` / `acme-minio-local` |
| MinIO console | `ACME_MINIO_CONSOLE_PORT`, 59001 | | `acme` / `acme-minio-local` |
| pgweb | `ACME_PGWEB_PORT`, 58081 | | none |
| Valkey Admin | `ACME_VALKEY_ADMIN_PORT`, 58080 | | none; connect to host `valkey`, port `6379` |
| ElasticMQ UI | `ACME_ELASTICMQ_UI_PORT`, 53000 | | none |
| Prometheus | `ACME_PROMETHEUS_PORT`, 59090 | `prometheus:9090` | none |
| Grafana | `ACME_GRAFANA_PORT`, 53001 | | none (anonymous admin) |
| Jaeger UI | `ACME_JAEGER_PORT`, 56686 | | none |
| Jaeger OTLP | `ACME_JAEGER_OTLP_PORT`, 54318 | `jaeger:4318` | none |
| GlitchTip | `ACME_GLITCHTIP_PORT`, 58000 | `glitchtip:8000` | `admin@example.test` / `acme-local` |

The worker serves its metrics on `ACME_METRICS_PORT` (9464), inside the
network or on the host process. The collector publishes no port.

## Postgres logins

Only `postgres` is a superuser, and nothing of Acme connects as it.
`acme_runtime` serves every request, with DML only. `acme_system` is its
twin for the system scope. `acme_migration` owns the schemas and runs the
migrations. `acme` is the local master: the init script in
`postgres/initdb/` makes it, and `make migrate` has it make the other
three. Each login's password is its name; the superuser's is `postgres`.
None of the four carries `BYPASSRLS`, so the row-level security policies
hold for all of them. The init script runs on an empty data directory only.

## Commands

| Goal | Command |
|------|---------|
| Everything up, migrated and seeded; keeps data | `make up` |
| Stop everything, keep data | `make down` |
| Wipe all data and start over | `make reset` |
| Only the backing services, for `scripts/dev.sh` | `make infra-up` |
| Backing services plus dashboards | `make devx-up` |
| Backing services plus app containers | `make stack-up` |
| Apply new migrations | `make migrate` |
| Seed again, or other people | `make seed SEED_EMAIL=me@example.test`, or the `SEED_*` knobs in `.env` |

For one service at a time, name both files, both env files, and the
profile:

```bash
alias dc='docker compose --env-file .env.example --env-file .env -f deployment/local/docker-compose.yml -f deployment/local/docker-compose.full.yml --profile devx'
dc ps                        # what runs, with health
dc logs -f api               # one service's logs
dc up -d --build --wait api  # rebuild and restart the API
dc exec postgres psql -U acme
```

On Linux, add `-f deployment/local/docker-compose.linux.yml` after the
first file, as the Makefile does.

`make up` and `make devx-up` exit 0 only when every service is healthy and
every one-shot succeeded. GlitchTip's seed is a one-shot in a profile of
its own, `devx-seed`, which the Makefile runs after the wait.

ElasticMQ keeps nothing between restarts; it declares `acme-webhooks` and
its `-dead` queue at start. Valkey snapshots on shutdown.

## Signals

- **Metrics.** Prometheus scrapes the app containers. For host processes,
  the collector scrapes the API on `ACME_COLLECTOR_SCRAPE_PORT` and the
  worker on 9464, and remote-writes into Prometheus, as the cloud's
  sidecar does. `make collector-scrape SCRAPE_PORT=<port>` points it
  elsewhere. Grafana opens on the provisioned overview dashboard, which
  the cloud's CloudWatch dashboard mirrors by panel title.
- **Traces.** The app containers export to Jaeger. A host process exports
  when `ACME_OTEL_ENDPOINT` is set in `.env`.
- **Errors.** GlitchTip is Sentry-compatible. Its seed makes one project
  with a fixed DSN key, so the DSNs in `.env.example` and the compose
  files work as they are. Every event carries its `environment`, which is
  `local` here.

## When something is off

| Symptom | Likely cause and fix |
|---------|----------------------|
| `port is already allocated` | Another process or stack holds the port: `lsof -nP -iTCP:<port> -sTCP:LISTEN`, then set its knob in `.env` |
| `postgres` exits right after start | A volume from another Postgres major version: `make reset` |
| `make up` fails right after `glitchtip-seed` | The seed's last line says why; fix it and rerun, since the seed is idempotent |
| The portal loads but every request fails | The API is down, or the portal was built for another `VITE_API_URL`: `dc ps api`, then `dc up -d --build --wait portal` |
| The API refuses to start, naming a setting | `.env` predates a change; compare it with `.env.example` |
| `scripts/dev.sh` fails on the API's port | The `api` container runs: `dc stop api maintenance portal` |
| `scripts/dev.sh` says `pnpm is not on PATH` | Node 25 and later ship no corepack, so a Node installed or switched to has no pnpm: `make setup` |
| `scripts/dev.sh` stops, naming a status | One process exited and the script stopped the others; the lines above it say why |
