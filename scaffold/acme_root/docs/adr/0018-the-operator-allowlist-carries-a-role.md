# ADR 0018: The operator allowlist carries a role, and an operator signs in as a person

**Status**: accepted (2026-09-28)
## Context

The Operator Context admits an identity to the operator plane when it
is on the operator allowlist. Operator Roles says the supporter reads a
named tenant's rows through that plane with an entry that says read,
and the provisioner that makes tenants for a traffic run holds one that
says write. An allowlist that only says "operator" gives every agent
with a support credential the power to delete an org.

## Decision

**The entry is a role.** `Identity.operator_role` is
`OperatorRole | None`: `read` or `write`, and write includes read.
`admit_operator` fills `OperatorContext.permissions` from it, and every
operator operation requires one. The reads (`get_orgs`, `get_org`,
`get_members`, `get_events`, `size`) require read. The writes
(`create_org`, `add_member`, `delete_org`, a work item's requeue)
require write.

**One writer of the allowlist.** In a deployed environment
`acme-api grant-operator --email <e> --permission read|write`, or
`--disable`, is the only path that changes an entry. It runs as a
one-off task on the grant task definition, started by
`grant-operator.yml`, which a person dispatches on the environment's
branch; production's run waits for the same approval as its apply. No
person's cloud profile and no route of the plane writes an entry.
Locally `make seed` grants the two local operators the same way.

**An operator signs in as the person they are.** A person signs in
through the identity provider with `POST /v1/auth/sign-in` and the
callback, or the device flow, like anyone. The plane admits that
sign-in only after a second factor: an operator enrols a TOTP secret
with `POST /v1/admin/me/totp` and `/confirm`, and until then the plane
admits the enrolment and nothing else. A sign-in that verified a code
mints one operator token and does nothing else
([ADR 0068](0068-an-operator-credential-ends-by-itself.md)).

**An agent holds an operator token.** A token is a `sessions` row of
kind `operator_token` under the system scope: one permission, an hour
at most, stored as its digest, capped by the entry on every request. A
person mints one from a sign-in with a code. The grant job mints the
provisioner's and the smoke identity's into the secret store. So the
env file under `~/.config/acme/ops/` holds `ACME_OPERATOR_TOKEN` (read)
and `ACME_PROVISIONER_TOKEN` (write), and never a password.

**The operator creates are idempotent.** `POST /v1/admin/orgs` and
`POST /v1/admin/orgs/{org_id}/members` take an `Idempotency-Key` like
every creating route. Their markers have no tenant, so they are recorded
under the system scope, keyed by the operator's identity id, through
`begin_for_operator`, `finish_for_operator`, and `release_for_operator`.
The system-scope sweep that purges the platform's own markers purges
them too. The seeding commands and the operator's creates share one
private path, `tenancy/impl/creates.py`, so the seed and the API make
the same rows.

## Consequences

A support agent cannot delete an org, create one, or add a member,
whatever it is told, because its token does not carry write.

Every operator read of a tenant's rows logs one line with the org id
and the operator's identity id, and no tenant data.

An operator credential produces an identity stage, never a tenant one,
and lives in `sessions` under the system scope, never in a tenant's
`api_keys`.

Operator markers live under the system scope by design. An operator
namespace of its own would move them, and nothing else.
