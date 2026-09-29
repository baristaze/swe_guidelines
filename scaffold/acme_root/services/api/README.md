# The API process

The one HTTP process of Acme. It serves the product's routes under `/v1`,
the realtime socket, the identity provider's webhook at
`/webhooks/identity`, and the probes `/healthz`, `/readyz`, and
`/metrics`. Every replica is the same process. `ACME_NAMESPACES` mounts a
subset of the namespaces, so one namespace can run as a service of its own
with no code change.

A request passes four layers, each in its own folder under
`src/acme/services/api/`:

- **Gateway** (`gateway/`). The middleware and dependencies every request
  meets: the request id and access log, the trusted proxy hops, admission
  and the request's deadline, the credential, rate limits, idempotency,
  the error envelope, and the webhook's signed body.
- **Routers** (`routers/`). One module per namespace: tenancy with the
  operator plane, events, media, and webhooks. Each route makes one call
  into a service.
- **Services** (`services/`). One interface per namespace, and its impl in
  `services/impl/`. An impl translates the request, calls a manager or a
  provider, and returns a view from `types/`.
- **Realtime** (`realtime/`). The socket, its ticket, its bounded send
  lanes, and the recheck that closes it when its credential ends.

`container.py` builds everything once per process, `app.py` assembles the
app, and `main.py` is the `acme-api` command: `serve`, `migrate`,
`bootstrap`, `add-member`, `grant-operator`, and `openapi`.
