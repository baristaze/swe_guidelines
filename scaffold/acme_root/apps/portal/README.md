# Acme portal

The browser app a signed-in person uses: React, TypeScript, Vite, TanStack
Query, and Zustand. It holds the shell every product screen sits in:
sign-in, the org chip and switch, settings, and one realtime channel.

## Layout

- `src/api/`: the one transport client and the types generated from
  `openapi.json`. Nothing else calls `fetch` or reads `schema.d.ts`.
- `src/app/`: routes, the nav, the query cache, and the session.
- `src/features/<name>/`: one folder per screen, a pure model, a
  view-model hook, and a page. `home/` is where the product's screens start.
- `src/queries/`: query keys and hooks, one file per API namespace.
- `src/realtime/`: the socket; a push invalidates the queries of its entity.
- `src/store/`, `src/design/`: client state and the design kit.

## Run

```bash
pnpm --filter @acme/portal dev    # http://localhost:5173, /v1 goes to 127.0.0.1:8000
pnpm --filter @acme/portal test
make openapi                      # regenerates src/api/schema.d.ts
```

On the local stack, `/login/dev` signs in by address alone.
