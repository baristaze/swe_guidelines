# Acme Python client

The one Python client of the API: every Python consumer (the CLI, the demo
recorders, a service that calls this one) goes through it, and nothing
else in Python calls `/v1/*`.

- `schema.py` is generated from the committed OpenAPI document
  (`apps/portal/openapi.json`, one document for both type sets) by
  `make openapi`; never hand-edited, excluded from lint, checked current
  in CI.
- `types.py` is the facade consumers import: the views and enums by name.
- `client.py` is the one transport client: bearer, app header, the error
  envelope parsed into `ApiError` with the request id, an idempotency key
  on every creating call, a 401 that clears the token, and the operating
  system's trust store. Request bodies are built by the operation methods
  from keyword arguments; the API validates them. A file's bytes go to
  the object store and come from it through the URL the API signed
  (`attach`, `import_tasks`, `download`), on a plain `httpx` client with the same
  timeout and nothing of ours on it, since the form or the link is the
  credential; where the store cannot sign, they go through the API's
  content route instead.
- The retry lives here and nowhere above it: the base URL, the credential,
  the timeout, the count (`retries`, default 2) and the first delay
  (`backoff_seconds`, default 0.25) all arrive through the constructor, so
  no caller wraps this client in a second retry. Only a failure that can
  differ on a second attempt goes again: a timeout, a connection refused,
  reset, or lost, and a 502, 503, or 504. A refusal is a decision the API
  made and is raised once. Only a read and a creating call under its
  idempotency key may be sent twice; a `PATCH`, a `DELETE`, and a `POST`
  with no key are sent once, because nothing records their outcome and a
  second attempt could write twice. The delay doubles per attempt up to a
  cap and half of each wait is jitter, so callers that failed together do
  not return together; a `Retry-After` the server sends lengthens a wait
  to it, never past the cap.
- `envelopes.py` mirrors the socket's frames by hand, as the portal's
  `envelopes.ts` does; they are not in the OpenAPI document.
- `stream.py` is the pure placement rule (next, seen, gap), the same cases
  the portal's `stream.ts` pins.
- `realtime.py` is the channel: a ticket, one subscription, pings at the
  interval the hello names from a timer of their own, gaps replayed from `/v1/events`, reconnects
  with backoff. The reconnect delay follows the curve to a cap and half of
  each wait is jitter, because a socket drops for a shared reason: a bare
  curve would bring every listener back at the same instant. `async for
  change in Channel(client)` yields every change once, in stream order.
  A replay refused as `stream_truncated` runs `on_resync`, where that
  consumer reads its state again, and the cursor moves to the head the
  refusal names.
  A consumer that keeps its own copy of the state reads it in
  `on_first_open`, which runs once after the first hello and before any
  change, so nothing committed after that read is missed.

```python
from acme.client.client import ApiClient
from acme.client.realtime import Channel

api = ApiClient("http://127.0.0.1:8000", app="cli", app_version="cli@0.7.0", token=token)
async with api:
    task = await api.create_task("Migrate DB")
    async for change in Channel(api):
        print(change.kind, change.target_id, change.actor_id)
```

```bash
uv run pytest -q clients/python/tests
```
