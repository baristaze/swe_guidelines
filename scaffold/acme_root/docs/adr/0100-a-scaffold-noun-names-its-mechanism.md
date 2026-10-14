# ADR 0100: A scaffold noun names its mechanism, and the bare noun is the product's

**Status**: accepted (2026-10-14)

## Context

A product starts as a copy of this tree and builds its domain on top,
so Acme's nouns and the product's share one codebase, one database, one
API, one portal, and one generated client. A bare everyday noun that
Acme takes is a noun the product cannot have: a clinic has sessions, a
ticketing product events, a staffing tool resources, and a factory
lines and steps. The guideline's "Naming Entities" says a noun the
scaffold brings is qualified by what it does, and the bare noun is the
product's. This records the names Acme holds.

## Decision

**Each of Acme's own nouns says its mechanism.**

| The thing | The name | Its surfaces |
|---|---|---|
| A signed-in visit to one org | `AuthSession` | `core.auth_sessions`, `/v1/auth/sessions`, `AuthSessionView`, `tenancy.auth_session.*`, `auth_session_token`, `ACME_AUTH_SESSION_*` |
| One line of an org's stream | `ChangeEvent` | `om/change_events`, `activity.change_events`, `activity.change_event_cursors`, `/v1/change-events`, `ChangeEventView`, the push frame `change_event`, `ACME_CHANGE_EVENT_RETENTION_DAYS` |
| One leasable thing | `LeasableResource` | `core.leasable_resources`, `leasable_resource_id`, `LeasableResourceKind`, `leases.leasable_resource.*`, the park reason `leasable_resource` |
| Its waiting requests | `WaitLine` | `WaitLineView`, `/v1/leases/leasable-resources/{id}/wait-line` |
| What a step lands beside its effect | `OrchestrationStep` | `OrchestrationStepLandingInterface`, `land_orchestration_step` |
| The root of the object model | `OmBase` | `OmException`, its code `om_error` |
| The operator plane's size tally | `SystemSize` | `admin.system_sizes`, `SystemSizeView` |

**The name holds on every surface.** A class, its table and its
columns, its routes, its event and outbox kinds, its stored values, its
settings, its tests, and the prose that names it take the same name.
Prose that means the running product says "the system".

**A rename moves in place.** A migration renames the table, its
columns, its constraints, and its indexes, and keeps every row. A
stored value that names the noun moves in the same migration, under a
counted lift of the fence, and its downgrade moves it back.

**A qualifier names the mechanism.** No name says "domain", and none
names a product that adopts the tree.

## Consequences

A product names its own nouns plainly and never meets one of Acme's.
A noun Acme adds later takes a qualifier on the day it lands (OM-19).

The words that stay bare are mechanism words with no product meaning,
or the same meaning in any product: org, member, identity, invitation,
outbox, orchestration, lease, idempotency, and file.
