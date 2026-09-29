# Acme TypeScript client

The one TypeScript client of the Acme API, `@acme/client`. Every browser
app imports it, and nothing else in TypeScript calls `fetch` or reads the
generated schema.

- `openapi.json` is the API's committed document. `make openapi` writes
  it, and the Python client generates from it too.
- `src/schema.d.ts` is generated from it by `make openapi`. Never edit it
  by hand.
- `src/types.ts` is the facade an app imports: the views and requests by
  name.
- `src/client.ts` is the one transport. Every call carries the bearer, the
  app headers, and a deadline. A refusal is an `ApiError` with its code and
  request id, and a 401 to the bearer the tab holds hands it back to the
  app. `src/retry.ts` is the one retry: only a failure that can differ, and
  a write only under an idempotency key.
- `src/store.ts` reaches the object store through a URL the API signed, and
  `src/page.ts` reads a file of the page's own origin, such as
  `/config.json`. Neither is the API, so neither carries its headers.

What only one app needs stays in that app: its session store, its query
client, its realtime channel, and the one client instance it builds.

```ts
import { createClient, type MeView } from "@acme/client";

const api = createClient({ baseUrl, app: "portal", appVersion, timeoutMs, getToken, onUnauthorized });
const me = await api.get<MeView>("/v1/me");
```

```bash
pnpm --filter @acme/client test
make openapi                      # regenerates openapi.json and src/schema.d.ts
```
