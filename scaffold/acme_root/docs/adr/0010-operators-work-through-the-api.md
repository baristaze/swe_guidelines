# ADR 0010: Operators work through the API

**Status**: accepted (2026-09-28)

## Context

DEL-16 (Client App Architecture, The Operator Console): "The operator
console shares the portal's stack and design, never its security
context." The section describes a separate application under
`apps/admin`, with the portal's stack, design tokens, component kit,
sign-in flow, and API client, its own origin (`admin.` under the
environment's base domain), its own bundle, its routes under
`/v1/admin/*`, and no realtime socket.

The tree has the server side of this and not the client side. The
operator routes live under `/v1/admin/*` in `services/api`
(`routers/admin.py`). They take `OperatorContext`, which only
`admit_operator` produces, so no tenant manager is reachable from them.
No `apps/admin` exists, and the Terraform serves no `admin.` name.

## Decision

Operators work through the API. `acme-ops` calls `/v1/admin/*` with an
operator token ([ADR 0068](0068-an-operator-credential-ends-by-itself.md)):
the platform's size, the orgs and their members and events, an org's
deletion, and the requeue of a failed work item.

The console lands in `apps/admin` when operators need a screen.
It takes the portal's stack (React, Vite, TanStack Query, Zustand, the
generated types behind a facade, the shared transport client), its own
origin (`admin.`) and its own bundle, and opens no realtime socket.

## Consequences

DEL-16 reads as a recorded deviation. A review that looks for
`apps/admin` and finds none cites this record.

What the tree gives up is a screen. The security context the rule
protects is already separate: the operator path is `OperatorContext`,
and nothing in the portal's bundle can reach it. The routes are the
contract the console is built against, so their wire types live in
`services/api/types` like every other route's.

A screen operators need is the trigger to build the console, never a
reason to put the screen into the portal.
