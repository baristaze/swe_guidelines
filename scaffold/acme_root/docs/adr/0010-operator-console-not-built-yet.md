# ADR 0010: The operator console is not built yet; operators use the API

**Status**: accepted (2026-09-19)

## Context

DEL-16 (Client App Architecture, The Operator Console) says: "The
operator console shares the portal's stack and design, never its
security context." The section describes it as a separate application
that shares the portal's stack, design tokens, component kit, sign-in
flow, and API client, with its own origin (`admin.` under the
environment's base domain), its own bundle, its own routes under
`/v1/admin/*`, and no realtime socket. The `apps/admin` entry of
"Monorepo Folder Structure" places it beside the portal, same stack.

Acme has the server side of this and none of the client side. The
operator routes exist under `/v1/admin/*` in `services/api`
(`routers/admin.py`): they take `OperatorContext`, which only
`admit_operator` produces, and cannot reach a tenant manager because no
`OpContext` exists on that path. No browser console consumes them, no
screen in `apps/portal` is an operator screen, no `apps/admin` package
exists, and no `admin.` domain exists in `deployment/terraform`; the
terraform modules serve `api.` and `app.` only. The operator tasks that
exist today (list the organizations, delete one) are done against the
API with an operator credential.

## Decision

The console lands as its own app in `apps/admin` when an operator task
needs a screen. It takes the portal's stack (React, Vite, TanStack
Query, Zustand, the generated types behind a facade, the shared
transport client), its own origin (`admin.`) and its own bundle, and
opens no realtime socket, as the section describes. Until then
operators use the API: the routes, the allowlist, and the
credential-provenance check are in place, and nothing about them
changes when the console arrives. The deviation ends when the first
operator screen ships.

## Consequences

DEL-16 reads as a recorded deviation, not a finding: a review that
looks for `apps/admin` and finds none cites this record. What Acme
gives up is a screen; the security context the rule protects is
already separate, since the operator path is `OperatorContext` and
nothing in the portal's bundle can reach it. The routes are the
contract the console is built against, so a change to `/v1/admin/*`
keeps its wire types in `services/api/types` like every other route.
A second operator route that needs a screen is the trigger to build
the console, not a reason to put the screen into the portal.
