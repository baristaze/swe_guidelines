# ADR 0003: The metadata-vs-schema check runs in the integration job

**Status**: superseded (2026-09-19). Guideline v0.13.0 rewrote "The
Storage Layer" ("Migrations"): the metadata-versus-schema check is a
target of its own, `make migrate-check`, run against the local stack
after migrating and in CI's integration job, and it is explicitly not
part of the fast gate, which has no database. What this record called a
deviation is now what the guideline asks for. The record stays for the
interval it covers.

## Context

STO-18 (The Storage Layer, Migrations) says: "A metadata-vs-schema check
for every role is in the fast test gate; a downgrade-then-upgrade is in
CI." The check compares each role's ORM metadata with the schema the
role's migration chain produced, so it needs a database those chains
have run against. The migrations are hand-written, schema-qualified
Postgres DDL; no in-process stand-in runs them, and the fast gate (`make
check`: lint, format, types, unit tests) runs without the compose stack
by design, on a laptop and in CI's first job.

## Decision

`make migrate-check` is not part of `make check`. CI's integration job
runs it right after `make migrate`, before the integration tests, and
`om/tests/integration/test_migrations.py` runs the same comparison per
role plus the downgrade-then-upgrade round trip of every head. The fast
gate keeps the checks that need no database: every table named in the
role map has an ORM definition and is found in a fresh process
(`test_migrate_metadata.py`), and every SQL file names only its own role
and every wrapper only calls the runner (`test_migrations_sql.py`).

This holds until the fast gate can migrate a throwaway Postgres on its
own (an embedded or containerised database started by the test run);
then the check moves into `make check` and this record is superseded.

## Consequences

Schema drift between a table class and its migration surfaces one job
later than the guideline asks, in the integration job rather than the
fast gate; a pull request still cannot merge with it. Reviews treat the
`check` and `migrate-check` targets in `Makefile` and the comment beside
them as the documented exception to STO-18.
