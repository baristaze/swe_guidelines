# Acme Python client

The one Python client of the Acme API. Every Python consumer goes through
it, and nothing else in Python calls `/v1/*`.

- `schema.py` is generated from `apps/portal/openapi.json` by
  `make openapi`. Never edit it by hand.
- `types.py` is the facade consumers import: the views and enums by name.
- `client.py` is the one transport. Every call carries the bearer, the app
  headers, and a timeout. A creating call carries an idempotency key. A
  failure that can differ is retried with jittered backoff. A refusal is an
  `ApiError` with its code and request id. TLS uses the system trust store.
- `envelopes.py`, `stream.py`, and `realtime.py` are the socket's frames,
  the placement rule, and the channel, which yields every change once, in
  stream order.

```python
async with ApiClient(url, app="cli", app_version="cli@0.1.0", token=token) as api:
    stored = await api.upload(await api.start_upload("spec.pdf", "application/pdf", size), data)
    async for change in Channel(api):
        print(change.kind, change.target_id, change.actor_id)
```

```bash
uv run pytest -q clients/python/tests
```
