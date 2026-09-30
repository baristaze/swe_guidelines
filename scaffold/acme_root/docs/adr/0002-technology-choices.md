# ADR 0002: Technology choices as adopted

**Status**: accepted (2026-09-28)

## Context

The guideline names technologies as defaults. It asks a project to
record, once, every technology it uses and every substitution it makes.

## Decision

Acme adopts every technology the guideline names, save one substitution:

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

The substitution:

| Choice in the guideline | Substitute | Reason | Rules it still satisfies |
|-------------------------|------------|--------|--------------------------|
| Browser apps are React on Vite (DEL-12) | The company site, `apps/site`: HTML and CSS on Vite, with no script | A page with no state, no API call, and no socket has nothing to render in a client | Vite builds it into static files behind CloudFront; it reaches no origin but its own; its look is the portal's `theme.css`; every app that holds state or calls the platform stays React on Vite |

The company site is one page that says what Acme is, with a link into
the portal, and a not-found page. React would bring a script to a page
that needs none, and Client Rendering rules out rendering it on the
server or at build time. The page keeps the shape the stack is for, and
only the technology it is written in changes, so it is a substitution
and not a deviation. It covers `apps/site/package.json` alone, which
`pyproject.toml` names as an exception to DEL-12: the rule's options
substitute a framework for every browser app, not for one page.
`apps/site/src/site.test.ts` fails when a page gains a `<script>` or
loads anything from another origin, and the site becomes a React app
like the portal the day it needs a script.

One name differs and is not a substitution. The guideline writes
`rediss://` for TLS to the cache. The cache URL here uses `valkeys://`,
the Valkey client's TLS scheme: the same transport under the client's
own name.

## Consequences

Reviews treat the names above as the guideline's names. A substitution
gets a row here: the choice, the substitute, the reason, and the rules
the substitute must still satisfy. A change of shape, not of name, is a
deviation and gets an ADR of its own.

The site ships no JavaScript, so its Content-Security-Policy names its
own origin and nothing else, and it has no client code to review or
update. What gives way is one stack for everything under `apps/`: a
second, smaller way to build a page exists.
