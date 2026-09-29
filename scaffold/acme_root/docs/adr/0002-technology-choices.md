# ADR 0002: Technology choices as adopted

**Status**: accepted (2026-09-28)

## Context

The guideline names technologies as defaults. It asks a project to
record, once, every technology it uses and every substitution it makes.

## Decision

Acme adopts every technology the guideline names:

| Choice in the guideline | Adopted |
|-------------------------|---------|
| Object model language and validation | Python 3.14, Pydantic 2 |
| Relational store, ORM, migration runner | Postgres 18, SQLAlchemy 2 (asyncpg), Alembic (runner only) |
| Cache | Valkey 9.1 (ElastiCache in the cloud), GLIDE client |
| Object store | S3 (MinIO locally) |
| Inbound queue | SQS (ElasticMQ locally) |
| Topic bus | Valkey pub/sub (an in-process dispatcher in tests) |
| Secret store | AWS Secrets Manager (the environment and a file locally) |
| Browser apps | React, TypeScript, Vite, TanStack Query, Zustand; served from S3 through CloudFront in the cloud |
| Workspaces | uv, pnpm |
| Local stack | Docker Compose |
| Cloud and infrastructure as code | AWS, Terraform |
| Traces and metrics | OpenTelemetry, the Prometheus client (CloudWatch and X-Ray through an ADOT collector in the cloud; Prometheus, Grafana, and Jaeger locally) |
| Logging | Python `logging`; errors to a Sentry-compatible backend through the Sentry SDK (GlitchTip locally) |

Substitutions: none.

One name differs and is not a substitution. The guideline writes
`rediss://` for TLS to the cache. The cache URL here uses `valkeys://`,
the Valkey client's TLS scheme: the same transport under the client's
own name.

## Consequences

Reviews treat the names above as the guideline's names. A substitution
gets a row here: the choice, the substitute, the reason, and the rules
the substitute must still satisfy. A change of shape, not of name, is a
deviation and gets an ADR of its own.
