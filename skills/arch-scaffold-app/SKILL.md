---
name: arch-scaffold-app
description: "Create a client app: the operator console, another browser app, or another command line, in the shape of the scaffold's portal and CLI, with its build, deployment, and tests. TypeScript or Python."
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(make openapi), Bash(make check), Bash(pnpm install:*), Bash(pnpm run:*), Bash(pnpm --filter:*), Bash(uv run:*), Bash(uv sync:*), Bash(git status:*), Bash(git rm:*)
---

# arch-scaffold-app

A path that starts with `../` is read from this skill's folder
as `realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../architecture.md`: The Network
Layer (Clients Live in One Place, Direction of Calls), Apps (Apps Are
Dumb, Push-First Apps), Client App Architecture (Stack; State and Data;
Views, View-Models, Models; API Access; One Tenant at a Time; Flags in
the Client; Realtime: One Channel per App; The Operator Console; The CLI
Is Different),
Deployment (Cloud: AWS).

## Input

`<app-name> --kind portal|admin|cli`

`--kind` is required; ask when missing. `portal` is a browser app for a
tenant's members, `admin` the operator console, and `cli` a command
line over REST. `<app>` is the app name in snake case. A screen for a
new entity is not this skill: `arch-scaffold-entity` adds it to the
portal.

## Created

| Kind | The shape | Under `apps/<app-name>/` |
|------|-----------|--------------------------|
| `portal` | `apps/portal/` | the package over the client of `clients/typescript/`, the config loaded from `/config.json` before the first render, the shell and routes, the sign-in gate, the query keys and hooks, the stores, the one realtime channel, the screens in the view, view-model, and model split, and the README |
| `admin` | `apps/portal/`, less its realtime channel, its org chip, and its tenant stores | the same, with its own sign-in and the operator plane's screens over `/v1/admin/*` |
| `cli` | `apps/cli/` | the distribution over `<name>-client`, its settings, its commands, the feed command when the API has a socket, and the tests |

## Changed

| File | Change |
|------|--------|
| `pnpm-workspace.yaml`, `package.json` (root) (browser app) | the package, in the workspace scripts `make check` runs |
| `pyproject.toml` (root) (CLI) | the member in `[tool.uv.workspace] members` |
| `.env.example`, `scripts/dev.sh`, `README.md` (root) (browser app) | its dev port, its `public/config.json` written and its dev server started, its local URL |
| `deployment/terraform/modules/environment/main.tf` (browser app) | a `static_site` instance beside `module "portal"`, on its own subdomain, `admin` for the console |
| `.github/workflows/deploy-staging.yml`, `deploy-production.yml` (browser app) | its bundle built once under the commit by staging and promoted to production, never built again, beside the portal's |
| `docs/adr/0010-operators-work-through-the-api.md`, `specs/architecture.md` (`admin`) | the ADR removed (`git rm`), since the console ends its deviation, and its row gone from the deviations, which holds the one link to it |

## Procedure

1. A browser app never calls `fetch` and never imports `schema.d.ts`:
   everything goes through the client package of `clients/typescript/`,
   whose one client owns the timeout and the one retry. The app builds
   its one instance of it, as the portal's `src/app/api.ts` does, and
   its ESLint config forbids `fetch` and the generated path, as the
   portal's does. The bearer lives in memory and in the tab's session
   storage, never in local storage. A view never
   re-implements a domain rule to enable an action: the service exposes
   the decision on the view, or the app acts on the error envelope.
2. The console imports the client from `clients/typescript/`, never
   through the portal, and takes the portal's design kit as a workspace
   dependency; it copies neither. It holds no socket, no tenant context,
   no membership picker, and no org chip. Its sign-in
   is the person's own, then the second factor the operator gate asks
   for, `POST /v1/auth/second-factor`. It renders the gate's refusals:
   `second_factor_required`, and `second_factor_not_enrolled`, which
   leads to the enrolment under `/v1/admin/me/totp`. The console never
   stores the enrolment's secret.
3. A second portal works in one tenant at a time, as the portal does:
   no screen past the gate renders before a membership is chosen, and a
   switch clears the query cache and every tenant store and reopens the
   socket before the new tenant's first request. It reads its flags as
   the portal's `src/queries/flags.ts` does: one snapshot of
   `GET /v1/flags` under a key of its key factory, read by the
   signed-in shell, again on focus and on an interval, and one flag
   through `useFlag`. It depends on no flag vendor's SDK. The console
   has no tenant, so it reads no flags.
4. A command line reads its settings once, at the start of `main`, and
   hands them to the client's constructor; nothing below `main` reads
   the environment. Every call goes through `<name>-client`. A followed
   operation polls at a fixed cadence and exits non-zero on failure.
5. A browser app is done when every environment serves it: its bucket,
   its distribution with its `Content-Security-Policy`, its subdomain,
   and its bundle in the deploy workflows. It calls the API on its own
   origin, as the portal does: the dev server and the distribution
   forward `/v1`, so no request is cross-origin.
6. Add the package to its workspace and install (`pnpm install` or
   `uv sync`). A browser app depends on the client package as
   `workspace:*`, under the name `clients/typescript/package.json`
   gives it. Then run `make openapi`, so the client's types are current
   before the first screen.

## Output

As `../_shared/scaffold-conventions.md` states.
