# The API process

The one HTTP process of Acme. It serves every route of the product
under `/v1`, the live channel, and the operational routes outside
`/v1`. Every replica is the same process; replicas share nothing but
the database, the cache, and the topic bus.

By default it mounts every namespace's routes. `ACME_NAMESPACES`
names a subset (`["media"]`, say), which is how one namespace becomes
a service of its own: the same image with another value, and no code
change. A name the image does not host refuses the boot.

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
- **Tasks.** Open and done lists a page at a time, one task, create,
  edit, move, delete. (`/v1/tasks`, `/v1/tasks/{task_id}`,
  `/v1/tasks/{task_id}/move`)
- **Many tasks at once.** How many tasks the open or the done list
  shows in a scope; completing or reopening the tasks named, at most a
  thousand, or every task of one list, under an Idempotency-Key. The
  answer names what changed and what was left alone, with the reason;
  a reopen past the plan's active tasks changes up to the bound and
  carries `plan_limit` beside what it did. (`/v1/tasks/count`,
  `/v1/tasks/bulk`)
- **The archive.** The done tasks the daily cleanup archived, a page at
  a time, and restoring one to the done list, naming the version read.
  (`/v1/tasks/archived`, `/v1/tasks/{task_id}/restore`)
- **Imports.** Starting the upload of a CSV file to import (the bytes and
  the confirm are the file routes'); starting its import, answered 202
  while the worker reads it a hundred rows a step; the org's newest
  imports; one import; resuming one parked on the plan.
  (`/v1/tasks/imports/files`, `/v1/tasks/imports`,
  `/v1/tasks/imports/{import_id}`, `/v1/tasks/imports/{import_id}/resume`)
- **Attachments.** A task's files a page at a time, starting an
  upload of one, removing one. (`/v1/tasks/{task_id}/attachments`,
  `/v1/tasks/{task_id}/attachments/{file_id}`)
- **Files.** A file; the form that posts its bytes straight to the
  store, or the bytes through the API where the store cannot take a
  form; the confirm once the upload is done; the link a download
  follows, or the bytes through the API; and the org's storage used.
  (`/v1/media/files/{file_id}`, `.../upload`, `.../content`,
  `.../confirm`, `.../download`, `/v1/media/usage`)
  edit, move, delete. A task's due date (`due_on`, `YYYY-MM-DD`, never a
  time) is set on the create and set, moved, or cleared (`null`) on the
  edit.
  (`/v1/tasks`, `/v1/tasks/{task_id}`, `/v1/tasks/{task_id}/move`)
- **Slack.** The org's installation (its workspace, its channel, and
  whether it works), any member; the install, which answers Slack's
  page to send the browser to with a one-time state, and the
  uninstall, an owner or an admin. (`/v1/slack/installation`: `GET`,
  `POST`, `DELETE`)
- **Events.** The org's diary after a sequence number. (`/v1/events`)
- **Billing.** The org's plan, where it comes from, its seats and
  active tasks against the plan's bounds, whether the latest payment
  failed, and the plans on offer; a checkout for a paid plan and the
  processor's own portal (its home, or its flow for a new payment
  method), each coming back only to a page of the portal; cancel at the period's end, and
  taking that back. Everyone in the org reads; an owner or an admin
  changes. (`/v1/billing`, `/v1/billing/checkout`,
  `/v1/billing/portal`, `/v1/billing/cancel`, `/v1/billing/resume`)
- **The payment processor's deliveries.** Outside `/v1`, since their
  shape is the processor's, pinned on the endpoint it delivers to. No
  credential: the route checks the processor's signature over the body
  and its timestamp, within a five-minute window, and queues the
  delivery for the worker; one that fails the check is a 400 and
  nothing is queued. (`/webhooks/stripe`)
- **Slack's calls.** Outside `/v1` too, since their shape is Slack's.
  No credential: a slash command and an event are each checked against
  the Slack app's signing secret over the body and its timestamp,
  within five minutes, acknowledged at once, and queued for the worker;
  one that fails the check is a 401 (`slack_signature_invalid`) and
  nothing is queued. With no Slack app configured, each answers 503
  (`slack_unavailable`). The events route answers Slack's URL check.
  The install's return is where Slack sends the browser back; it is
  not signed, the one-time state is its check, and it redirects to the
  portal's Settings with the outcome. (`/webhooks/slack/commands`,
  `/webhooks/slack/events`, `GET /webhooks/slack/oauth`)
- **Realtime.** The live channel, a websocket opened with a
  single-use ticket. (`/v1/realtime`)
- **The operator plane.** For an identity on the operator allowlist,
  across every org: create an org with its owner, add a member, read
  an org, its members, its tasks, and its events, list every org a
  page at a time, delete a team org as its owner does (closed for
  everyone in it at once; the worker ends its providers and then
  deletes it; a personal org is refused), read
  an org's plan and grant it one with no payment, send one of an org's
  failed work items back to the queue, and read the
  platform's size: the tenant count, the
  user count, and the tasks and events of twenty-four hours, as the
  maintenance worker last counted them, with the moment it did (`404`
  before its first count; the route counts nothing).
  (`/v1/admin/orgs`, `/v1/admin/orgs/{org_id}`,
  `/v1/admin/orgs/{org_id}/members`, `/v1/admin/orgs/{org_id}/tasks`,
  `/v1/admin/orgs/{org_id}/events`, `/v1/admin/orgs/{org_id}/billing`,
  `/v1/admin/orgs/{org_id}/plan`,
  `/v1/admin/orgs/{org_id}/work/{item_id}/requeue`, `/v1/admin/size`). A read route
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
  bulk change of tasks is a write of many rows and carries one too: a
  retried "Mark all" answers what the first call did, and never reaches
  a task that joined the list since. The same key with the same
  request gets the first answer back, marked `Idempotent-Replayed:
  true`; the same key with a different request is refused. A secret
  is in the first answer only. A 429 and a plan's bound (402) are
  answers about now and are not kept: the retry of the same create runs
  again, and lands once the org has room.
- **The calling app and its version.** `x-app` and `x-app-version`
  name the client, and travel into the provenance of every write.
- **One error envelope.** Every refusal, the framework's own included,
  is one shape: a code, a message, and the request id, under the HTTP
  status. A 5xx says `internal error` and the real message goes to the
  log under the request id; the admission 503 alone says which bound it
  hit, with a `Retry-After`. A plan's bound is a 402,
  `plan_limit_reached`, and its envelope also carries `plan_limit`: the
  lever, the org's plan, the bound, and the plan that lifts it, which
  is what a client turns into an offer to upgrade.
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
  every call it makes to WorkOS, Stripe, Slack, or AWS shares it. A
  provider that hangs costs the request its deadline, not the call's
  timeouts times its retries, and the answer is the one a provider that
  does not answer gets: `503 unavailable`, or, on Slack's install
  callback, the settings page with `slack=failed`. It sits under
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
  `ACME_REALTIME_RECHECK_SECONDS` and closes when it is refused. A
  socket an api key opened is refused, too, once the org's plan has no
  keys; a plan change wakes that recheck at once. Each
  of these closes with code 4401, which every client reads as "sign in
  again". A change of the member's role closes the socket with 1012,
  which every client reads as "reconnect", and the new socket carries
  the new role.
- **The socket ticket is never logged.** Access logging is the
  gateway's own, by route template, with no query string.

## The subcommands

`acme-api` is one entry point with subcommands. Each boots the same
container the server does.

| Subcommand | Does |
|------------|------|
| `serve` | Runs the process. |
| `migrate` | Applies every role's migration chain (`--all`) or one role's. Idempotent per revision. `migrate ensure-logins` makes the database logins, as the master. |
| `bootstrap` | Seeds a fresh local environment with one org and its owner; `--operator` puts the owner on the operator allowlist with write, and `--plan` grants the org a plan with no payment. Local only, like every seed. |
| `grant-operator` | The grant job: `--email <e> --permission read\|write` puts an identity on the operator allowlist, `--email <e> --disable` takes it off, and `--email <e> --mint-token provisioner\|smoke [--expires-in N]` mints that identity's operator token into the secret store as `acme-<env>-<holder>-token`, never printed in the cloud. On a local database it prints the token instead, and `--mint-token operator` mints the local read operator's `read` token, which `make seed` writes into `~/.config/acme/ops/local.env`; a cloud refuses it. An operator signs up first; the platform's own identities (`@platform.acme.invalid`) are made by their first grant. |
| `add-member` | Seeds a person into an existing org; a no-op for a member. |
| `openapi` | Emits the OpenAPI document the clients are generated from. |

The migration runs inside the cloud deploy that brings one, as a one-off
task before the service rolls; a failed migration leaves the old tasks
serving. A deploy whose release changes none of the files
`deployment/migration-inputs.json` names runs no migrate task.
