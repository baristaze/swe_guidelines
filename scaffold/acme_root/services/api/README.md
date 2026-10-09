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

## Routes, by area

- **Sign-in.** Start a sign-in at the identity provider, WorkOS
  AuthKit, for this environment's portal callback, and finish it with
  the code the browser brought back, which the API exchanges
  server-side; a person nobody knew is signed up by it, with their
  personal org (`/v1/auth/sign-in`, `/v1/auth/callback`). Start and
  finish a device sign-in for the command line (`/v1/auth/device`,
  `/v1/auth/device/token`). Confirm an operator's second factor on a
  sign-in (`/v1/auth/second-factor`). List my places; exchange the
  login, once, or switch a session, for a session in one org; sign out
  (`/v1/auth/memberships`, `/v1/auth/sessions`, `/v1/auth/logout`).
  The sign-out ends the credential presented, whichever a person holds:
  a session, a login (with or without its second factor), or an
  operator token; an API key has none and is `401`. It takes an optional
  `return_to`, one of this environment's `ACME_SIGN_OUT_RETURN_URIS`,
  and answers the row it ended with `provider_logout_url`: WorkOS's
  logout for the AuthKit session the sign-in left in the browser, or
  null when there is none (the device sign-in, the local sign-in, an
  operator token).
  Locally and in the tests only, sign in by address alone
  (`/v1/auth/dev-sign-in`, ADR 0029); a deployed process refuses to
  start with it on.
- **Me.** The current org, my user, my identity, and my display name;
  a new team org I own. (`/v1/orgs/current`, `/v1/me`,
  `/v1/me/identity`, `/v1/orgs`)
- **Delete my account.** From a session, with the account's email typed
  to confirm: the account goes at once, in every org, and the answer
  names the identity provider's logout, as a sign-out does. `409
  last_owner` names each team org the person is the last owner of, in
  the envelope's `last_owner.orgs`; `403 operator_role_held` refuses an
  operator. (`POST /v1/me/deletion`, ADR 0041)
- **Delete this organization.** From an owner's session, with the
  org's name typed to confirm: everyone in the team org loses it at
  once, and the answer carries the owner's new session in their
  personal org. `403 not_authorized` refuses an admin, a member, and an
  api key; `409 personal_org_fixed` refuses a personal org.
  (`POST /v1/orgs/current/deletion`, ADR 0042)
- **Members.** The org's members and their roles a page at a time,
  a member's role, removing a member. (`/v1/users`, `/v1/memberships`,
  `/v1/memberships/{user_id}`)
- **Invitations and single sign-on.** Invite a person by email with a
  role, under an Idempotency-Key; the identity provider sends the
  email. The org's pending invitations a page at a time, sending one
  again, revoking one. A short-lived link to the provider's admin
  portal, where an owner or an admin of a team org sets up single
  sign-on or proves a domain. (`/v1/invitations`,
  `/v1/invitations/{invitation_id}/resend`,
  `/v1/invitations/{invitation_id}`, `/v1/orgs/current/sso-link`)
- **Credentials.** My live sessions and revoking one; the org's API
  keys, creating one, revoking one; a ticket for the live channel.
  (`/v1/sessions`, `/v1/api-keys`, `/v1/realtime/tickets`)
- **Files.** Starting an upload, under an Idempotency-Key; the org's
  stored files a page at a time; a file, and deleting it; the form that
  posts its bytes straight to the store, or the bytes through the API
  where the store cannot take a form; the confirm once the upload is
  done; the link a download follows, or the bytes through the API; and
  the org's storage used. (`/v1/media/files`,
  `/v1/media/files/{file_id}`, `.../upload`, `.../content`,
  `.../confirm`, `.../download`, `/v1/media/usage`)
- **Events.** The org's diary after a sequence number. (`/v1/events`)
- **Leases.** Ask for a resource, by its id or by a selector, under an
  idempotency key; read where the request stands, its place and its
  estimate, or its lease; cancel it; read a resource's line; renew a
  lease, for a length it names or its term again, and release it, as
  its holder. The worker that runs a lease's job acts through the
  manager, not a route. A manager reorders a request and
  revokes a lease. (`/v1/leases/requests`,
  `/v1/leases/requests/{request_id}`, `.../cancel`, `.../reorder`,
  `/v1/leases/resources/{resource_id}/line`, `/v1/leases/{lease_id}`,
  `.../renew`, `.../release`, `.../revoke`, ADRs 0086 and 0094)
- **The identity provider's deliveries.** Outside `/v1`, since their
  shape is the provider's. No credential: the route checks the provider's
  signature over the body and its timestamp, and queues the delivery for
  the worker; one that fails the check is refused and nothing is queued.
  (`/webhooks/identity`)
- **Realtime.** The live channel, a websocket opened with a
  single-use ticket. (`/v1/realtime`)
- **The operator plane.** For an identity on the operator allowlist,
  across every org: create an org with its owner, add a member, read
  an org, its members, and its events, list every org a page at a
  time, delete a team org as its owner does (closed for everyone in it
  at once; the worker ends its providers and then deletes it; a
  personal org is refused), send one of an org's failed work items back
  to the queue, set, read, or clear an org's own cap on a lane (the
  most items it holds claimed there at once, in place of the lane's
  cap; `404` where it has none), and read the platform's size: the
  tenant count, the user count, and the events of twenty-four hours,
  as the maintenance worker last counted them, with the moment it did
  (`404` before its first count; the route counts nothing).
  (`/v1/admin/orgs`, `/v1/admin/orgs/{org_id}`,
  `/v1/admin/orgs/{org_id}/members`, `/v1/admin/orgs/{org_id}/events`,
  `/v1/admin/orgs/{org_id}/work/{item_id}/requeue`,
  `/v1/admin/orgs/{org_id}/work/lanes/{lane}/cap`, `/v1/admin/size`). A read route
  needs an operator who may read; a write route one who may write.
  Every read and write takes an operator token. An operator enrols the
  second factor once, at the first sign-in to the plane, and until then
  only the two enrolment routes answer (`/v1/admin/me/totp`,
  `/v1/admin/me/totp/confirm`). A sign-in that verified a TOTP code
  mints one operator token, one permission and an hour at most, and
  ends in that mint; every other route refuses it `403
  operator_token_required` (`POST /v1/admin/me/tokens`). An operator
  lists their own live tokens and revokes one by its id, refused from its
  next request; another operator's is `404` (`GET /v1/admin/me/tokens`,
  `DELETE /v1/admin/me/tokens/{token_id}`, ADR 0068).
- **Operational.** Liveness (`/healthz`, the process alone),
  readiness (`/readyz`, asks the database under a deadline shorter
  than the probe's interval), metrics (`/metrics`), and the OpenAPI
  document with its UI (`/docs`). The first three are never refused
  by admission.

## What the gateway guarantees

- **A request id on every answer.** The `x-request-id` header carries
  it. A caller may send one; otherwise the gateway mints it. Every
  log line of the request, its trace, and its error report carry the
  same id, and a refusal quotes it.
- **An idempotency key on every create.** A creating `POST` may carry
  `Idempotency-Key`, one to 255 characters; every client this repository
  ships sends one, and a create without it runs once per request. The
  same key with the same request gets the first answer back, marked
  `Idempotent-Replayed: true`; the same key with a different request is
  refused. A secret is in the first answer only. A 429 is an answer
  about now and is not kept: the retry of the same create runs again.
- **The calling app and its version.** `x-app` and `x-app-version`
  name the client, and travel into the provenance of every write.
- **One error envelope.** Every refusal, the framework's own included,
  is one shape: a code, a message, and the request id, under the HTTP
  status. A 5xx says `internal error` and the real message goes to the
  log under the request id; the admission 503 alone says which bound it
  hit, with a `Retry-After`.
- **Bearer by prefix.** The credential's prefix says what it is: a
  session token, an API key, a login, a ticket, or an operator token. A missing or
  invalid one is a 401; a route asked with the wrong kind is refused.
- **The answer never waits for the relay.** A write commits its
  outbox rows before it answers. Their relay, the push and the queued
  work, runs once the answer is sent, under the same request id and
  trace. A relay that fails, or whose push the bus drops, is logged,
  and the maintenance sweep relays the rows. The latency a route reports is the time to its
  answer.
- **Admission.** The process bounds what it has in flight. Past the
  bound a request is refused at once with a 503 and a `Retry-After`,
  so a saturated process answers and says why instead of queueing
  work it cannot start. Admission fails closed and is counted in the
  process's own memory.
- **A deadline on every admitted request.** A request gets one when it
  takes its slot, `ACME_REQUEST_DEADLINE_SECONDS` (20) from then, and
  every call it makes to WorkOS or AWS shares it. A
  provider that hangs costs the request its deadline, not the call's
  timeouts times its retries, and the answer is the one a provider that
  does not answer gets: `503 unavailable`. It sits under
  the 30 seconds the portal and the command line wait
  ([ADR 0069](../../docs/adr/0069-a-request-has-a-deadline-its-provider-calls-share.md)).
- **Rate limits.** Counted in the shared cache, so every replica
  shares one budget. Past a budget the answer is a 429,
  `rate_limited`, in the one envelope, with a `Retry-After` in
  seconds: the time left on the window. There are three budgets.
  - **Per credential.** Every session and API key has a budget of
    reads (`GET`, `HEAD`) and one of writes, spent once the credential
    resolves (`ACME_CREDENTIAL_RATE_LIMIT_READS`, `_WRITES`, over
    `ACME_CREDENTIAL_RATE_WINDOW_SECONDS`). Two people behind one
    address never share it.
  - **Per address, on failed authentications.** A bearer that is
    unknown, expired, or revoked answers 401 and counts against the
    client address (`ACME_FAILED_AUTHENTICATION_LIMIT`). Once that is
    spent, every request from the address answers 429, a live
    credential's included, and nothing is looked up until the window
    ends. The refusal says so in its message.
  - **Per address, on sign-in.** The sign-in routes share
    `ACME_LOGIN_RATE_LIMIT`, since no credential exists yet.

  Rate limits fail open: they are fairness, not a security boundary
  ([ADR 0059](../../docs/adr/0059-authenticated-routes-have-limits.md)).
- **The client address behind a load balancer.** The forwarded address
  is trusted only from the peers the settings name, never from
  everyone, so a caller cannot pick its own address. Through the
  portal's CDN edge it is one hop further in, and only a request that
  carries the edge's secret (`ACME_EDGE_SECRET`, in `X-Acme-Edge`)
  gets that hop; the header never reaches a route.
- **A socket's trust is bounded.** It closes at once when the
  credential behind its ticket is revoked, and at its expiry whatever
  the client does. The revocation rides a bus that may lose it, so the
  socket also re-checks the credential every
  `ACME_REALTIME_RECHECK_SECONDS` and closes when it is refused. Each
  of these closes with code 4401, which every client reads as "sign in
  again". A change of the member's role closes the socket with 1012,
  which every client reads as "reconnect", and the new socket carries
  the new role.
- **A socket carries the org's whole stream.** A subscription names no
  audience, and a narrower view is the client's filter. Every push carries the
  org's stream position, and each client holds a contiguous cursor, so
  a push the server withheld would be a gap. The replay of
  `/v1/events` that fills a gap is unscoped, and it would hand back
  what was withheld. Filtering at the server needs a scoped replay and
  a frame counter per subscription, a second protocol, and it waits
  until the channel's traffic makes the filter worth that protocol.
- **The socket ticket is never logged.** Access logging is the
  gateway's own, by route template, with no query string.

## The subcommands

`acme-api` is one entry point with subcommands. Each boots the same
container the server does.

| Subcommand | Does |
|------------|------|
| `serve` | Runs the process. |
| `migrate` | Applies every role's migration chain (`--all`) or one role's. Idempotent per revision. `migrate ensure-logins` makes the database logins, as the master, on each database a role lives on. |
| `bootstrap` | Seeds a fresh local environment with one org and its owner; `--operator` puts the owner on the operator allowlist with write. Local only, like every seed. |
| `grant-operator` | The grant job: `--email <e> --permission read\|write` puts an identity on the operator allowlist, `--email <e> --disable` takes it off, and `--email <e> --mint-token provisioner\|smoke [--expires-in N]` mints that identity's operator token into the secret store as `acme-<env>-<holder>-token`, never printed in the cloud. On a local database it prints the token instead, and `--mint-token operator` mints the local read operator's `read` token, which `make seed` writes into `~/.config/acme/ops/local.env`; a cloud refuses it. An operator signs up first; the platform's own identities (`@platform.acme.invalid`) are made by their first grant. |
| `add-member` | Seeds a person into an existing org; a no-op for a member. |
| `openapi` | Emits the OpenAPI document the clients are generated from. |

The migration runs inside the cloud deploy that brings one, as a one-off
task before the service rolls; a failed migration leaves the old tasks
serving. A deploy whose release changes none of the files
`deployment/migration-inputs.json` names runs no migrate task.
