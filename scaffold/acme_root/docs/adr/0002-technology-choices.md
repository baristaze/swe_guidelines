# ADR 0002: Technology choices as adopted

Date: 2026-09-16

## Context

The guideline names technologies as defaults and asks a project to
record, once, every technology it uses and every substitution it makes.

## Decision

Acme adopts every technology the guideline names, with the one
substitution recorded below the table:

| Choice in the guideline               | Adopted                         |
|---------------------------------------|---------------------------------|
| Object model language and validation  | Python 3.14, Pydantic 2         |
| Relational store, ORM, migration runner | Postgres 18, SQLAlchemy 2 (asyncpg), Alembic (runner only) |
| Cache                                 | Valkey 9.1 (ElastiCache in the cloud), GLIDE client |
| Object store                          | S3 (MinIO locally)              |
| Inbound queue                         | SQS (ElasticMQ locally)         |
| Topic bus                             | Valkey pub/sub (in-process dispatcher in tests) |
| Secret store                          | AWS Secrets Manager (env and file locally) |
| Browser apps                          | React, TypeScript, Vite, TanStack Query, Zustand; served from S3 through CloudFront in the cloud |
| Workspaces                            | uv, pnpm                        |
| Local stack                           | Docker Compose                  |
| Cloud and infrastructure as code      | AWS, Terraform                  |
| Traces and metrics                    | OpenTelemetry, Prometheus client (CloudWatch and X-Ray through an ADOT collector in the cloud; Prometheus, Grafana, and Jaeger locally) |
| Logging                               | Python `logging`; errors to a Sentry-compatible backend through the Sentry SDK (GlitchTip locally) |

Substitutions. None. Valkey was recorded here as a substitute for Redis
(ElastiCache offers no Redis OSS past 7.1; Valkey is its current engine,
protocol-compatible, and open source) until guideline v0.4.0 named
Valkey as the cache and the topic bus; from that release on it is an
adopted name, and the GLIDE client is a plain library choice. A future
substitution gets a row here naming the choice, the substitute, the
reason, and the rules the substitute must still satisfy.

## Consequences

Reviews treat the names above as the guideline's names. A change of
shape, not of name, is a deviation and is recorded separately.
