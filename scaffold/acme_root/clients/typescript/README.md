# Acme TypeScript client

The one TypeScript client of the Acme API, `@acme/client`. Every browser
app imports it, and nothing else in TypeScript calls `fetch` or reads the
generated schema.

- `../openapi/<namespace>.json` are the API's committed documents, one
  per namespace and one for what they share, `common.json`. `make
  openapi` writes them, and the Python client generates from them too.
- `src/schema/<namespace>.d.ts` is generated from each document by `make
  openapi`. Never edit it by hand.
- `src/types/<namespace>.ts` is the facade of one namespace: the views
  and requests an app reads, by name. `src/index.ts` re-exports each
  under its namespace's name, so an app names a type by its owner:
  `tenancy.MeView`.
- `src/client.ts` is the one transport. Every call carries the bearer, the
  app headers, and a deadline. A refusal is an `ApiError` with its code and
  request id, and a 401 to the bearer the tab holds hands it back to the
  app. `src/retry.ts` is the one retry: only a failure that can differ, and
  a write only under an idempotency key.
- `src/store.ts` reaches the object store through a URL the API signed, and
  `src/page.ts` reads a file of the page's own origin, such as
  `/config.json`. Neither is the API, so neither carries its headers.

What only one app needs stays in that app: its auth session store, its query
client, its realtime channel, and the one client instance it builds.

```ts
import { createClient, type tenancy } from "@acme/client";

const api = createClient({ baseUrl, app: "portal", appVersion, timeoutMs, getToken, onUnauthorized });
const me = await api.get<tenancy.MeView>("/v1/me");
```

```bash
pnpm --filter @acme/client test
make openapi                      # regenerates the documents and src/schema/
```
