# ADR 0097: A purge across tenants acts in a tenant, and a new member passes a gate

**Status**: accepted (2026-10-09)

## Context

A purge across tenants runs once a pass, in the system scope
([ADR 0045](0045-retention-purges-run-once-a-pass-across-tenants.md)).
Most of them delete their rows in one statement. Some must act in the
tenant of a row they found before the row goes, such as asking another
manager to erase the files attached to it. That is a tenant operation,
and it takes the tenant's context. The pass has minted one per tenant
already, deleted tenants included (`service_contexts`). The claim's
`service_context` refuses a deleted tenant, and a context minted per
row would read the org row once a row.

A person joins an org on four paths: an accepted invitation, a single
sign-on, the operator plane, and the seeding. All of them land through
one create, `add_member_to`. A system that bounds who may join an org,
as a cap on its members does, must ask its bound on every path, at one
point: once the person is known to be new to the org, before anything
is written, and with what the bound keeps in the same commit.

## Decision

**`sweep_context` gives a purge across tenants one tenant's context.**
The tenancy manager's `sweep_context(rctx, org_id)` answers the service
context the pass minted for the tenant: the service role, the system
user `EMPTY_UUID`, and the credential kind `INTERNAL`. Under the pass's
request stage it reads nothing, since `service_contexts` read the org
rows. Under any other stage, or for a tenant made since the list was
read, it reads the org row. A deleted tenant has one, since its rows
are the sweep's to settle. A tenant marked purged, before the pass or
during it, has none, and neither has an id with no org row. The
tenant's own purges took everything of it, so the caller lets the row
go as it is. The system scope always has one. It is a transition, so
the stage construction sites and the request-stage methods name it.

**`add_member_to` asks a member's admission.** `Admission` is a gate
with no arguments that answers outbox rows. The add asks it after it
answers a member already there as stored, and after the person's own
bound on orgs. So a repeated add never asks it, and a gate that counts
members never counts one twice. The gate refuses by raising, and
nothing is written: the identity a new person would get lands with the
create too. Its rows land in `create_member` with the user, the
membership, and the row that announces them, so they move only with a
member who landed. A caller passes a gate that reads what it needs: a
sign-in's join under the org's service context, the operator plane
under the operator.

**The scaffold wires neither.** None of its purges across tenants acts
in a tenant: the media purge erases an object by the tenant and the key
its row holds. And it bounds nobody, so its callers pass no gate. A
system that keeps either wires it where its own rows are.

## Consequences

A purge across tenants that acts in a tenant costs no read for a tenant
the pass listed.

A gate reads before the add writes. Two adds of different people that
race can both pass it, so a cap counted from the members holds to
within the adds in flight.
