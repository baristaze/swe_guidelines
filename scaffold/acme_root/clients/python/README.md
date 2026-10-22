# Acme Python client

The one Python client of the Acme API. Every Python consumer goes through
it, and nothing else in Python calls `/v1/*`.

- `schema/` is generated from the committed documents,
  `clients/openapi/`, by `make openapi`: one module per namespace, which
  imports another's module where it uses its types. Never edit it by
  hand.
- `types/` is the facade consumers import: one module per namespace, the
  views and enums by name (`acme.client.types.tenancy`).
- `transport.py` is the one transport. Every call carries the bearer, the
  app headers, and a timeout. A creating call carries an idempotency key.
  A failure that can differ is retried with jittered backoff. A refusal is
  an `ApiError` with its code and request id. TLS uses the system trust
  store.
- `calls/` holds each namespace's operations, one module per namespace,
  and `client.py` composes them onto the transport as `ApiClient`.
- `envelopes.py`, `stream.py`, and `realtime.py` are the socket's frames,
  the placement rule, and the channel, which yields every change once, in
  stream order.

```python
async with ApiClient(url, app="cli", app_version="cli@0.1.0", token=token) as api:
    started = await api.start_upload("spec.pdf", "application/pdf", path.stat().st_size)
    stored = await api.upload(started, path)  # the parts go straight to the store
    async for change in Channel(api):
        print(change.kind, change.target_id, change.actor_id)
```

```bash
uv run pytest -q clients/python/tests
```
