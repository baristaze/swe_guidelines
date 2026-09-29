# ADR 0030: The company site is HTML and CSS, not React

**Status**: accepted (2026-09-22)

## Context

`DEL-12`, Client App Architecture, Stack: "**Principle:** One React +
TypeScript stack for every browser app. The CLI stays Python." The
checker reads it as: every `apps/*/package.json` depends on `react` and
`vite`.

`apps/site` is the company site: one page that says what Acme is, the
plans, and a link into the portal, plus a not-found page. It is not an
app in the guideline's sense. It holds no state, calls no API, opens no
socket, and has nothing to render in a client. React would bring a
script to a page that needs none, and "Client Rendering" rules out the
other way to use React here, rendering it on the server or at build
time.

## Decision

The company site is HTML and CSS built by Vite, with no framework and no
script. It keeps what the stack is for elsewhere: Vite builds it, it is
static files behind CloudFront, it reaches no origin but its own, and
its look comes from the portal's own `theme.css`, not a copy. The
exception covers `apps/site/package.json` alone; every app that holds
state or calls the platform stays React on Vite. It ends when the site
needs a script: then it becomes a React app like the portal.

## Consequences

The site ships no JavaScript, so its Content-Security-Policy names its
own origin and nothing else, and it has no client code to review or
update. What gives way is one stack for everything under `apps/`: a
second, smaller way to build a page exists. Its tests stand in for the
rule's intent: `apps/site/src/site.test.ts` fails when a page gains a
`<script>` or loads anything from another origin, so the site cannot
grow into an app without this record being revisited.
`pyproject.toml` names `apps/site/package.json` as an exception to
`DEL-12`, citing this record.
