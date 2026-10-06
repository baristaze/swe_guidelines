# ADR 0083: Each database role runs on its own instance locally, and all four share one in the cloud

**Status**: accepted (2026-10-06)

## Context

Nothing crosses a database role: each has its own schema, its own URL,
its own pool, and its own migration chain (STO-19), and the migration
runner refuses a statement that names another role's table. On one
instance, a statement or a test that leans on two roles together still
works, and nothing shows it until a role moves out.

A login, a grant, and a schema's owner live on one instance. A role on
an instance of its own finds none of them there unless the migrate
command makes them.

An instance costs money in the cloud and nothing locally.

## Decision

- **Locally, each role runs on its own Postgres.** The compose stack
  runs `postgres-core`, `postgres-activity`, `postgres-queue`, and
  `postgres-admin`, each with its own port and volume, and
  `.env.example` sets each role's URL to its instance. Every run of the
  integration suite, and every run end to end, proves the roles apart.
- **In the cloud, one instance serves all four,** for its price. The
  tasks set only the shared URLs, and with no role URL set every role
  reads the one URL. A role moves to an instance of its own when its
  metrics demand it, and the cut-over is its URL.
- **`ensure-logins` runs once on each database a role lives on,** as the
  master there, for the roles it holds: the three logins, the schema of
  each of those roles owned by the migration login, and the serving
  logins' grants. Each role's URL names its database, and the master's
  login reaches it there.
- **An audit's database holds every role,** as the cloud's instance
  does, on the master's instance: core's, locally.

## Consequences

- A statement or a test that needs two roles on one instance fails on
  the first local run, as it would after a role moves out.
- A `.env` that sets no role URL runs every role on the core instance,
  the cloud's shape, and leaves the other three empty. The role URLs in
  `.env.example` are what put each role on its own.
- A command aimed at another database names it in the four role URLs
  as well as the shared ones. `ops/audit/auditdb.py` exports all eight.
- `om/tests/integration/test_migrations.py` holds that every database a
  role lives on carries the three logins and the schemas of its own
  roles, and no other role's. `om/tests/unit/test_storage_settings.py`
  holds that with no role URL set every role reads the one URL.
