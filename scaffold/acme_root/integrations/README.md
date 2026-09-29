# Integrations

The third-party providers Acme talks to. Each is one interface with a
real client and a deterministic twin, and a caller never knows which it
holds. Nothing here imports the object model; a provider's errors root
at infra's exception family, since a provider is a dependency the way a
backend is.

## Payments

The payment processor is Stripe. It owns money, customers, and
subscriptions; Acme owns what a plan entitles an org to.
`PaymentsInterface` is the one door to it:

- a customer per org, made once, carrying the org in its metadata;
- a hosted checkout that starts a subscription to a price named by its
  lookup key, at a quantity;
- a Billing Portal session, the processor's own page for payment
  methods, invoices, a change of plan, and cancellation;
- the two changes Acme makes to a subscription itself: cancel at the
  period's end (or take that back), and the seat count, with no
  proration;
- the end of an org whose person deleted their account, or whose owner
  deleted it: its subscription canceled at once and its customer
  deleted. A
  subscription or a customer the processor no longer holds is done
  already. Stripe keeps the invoices;
- a read of a subscription as the processor holds it now. Deliveries
  arrive in any order, so the mirror is written from this read and
  never from a delivery's payload;
- the check of an inbound delivery: the SDK's own signature check over
  the body and its timestamp, inside a five-minute window, answering the
  event's id, type, and the ids it names, and a key for it, a UUID v5
  over the processor's name and the event id.

| Implementation | What it is |
|----------------|------------|
| `PaymentsStripeImpl` | The SDK over one client opened at start, with the account in `Stripe-Context`, `Stripe-Version` pinned to the SDK's release, and a timeout on every call. At start it reads one item under each permission of the runtime key, and names in the start line and the log any resource the key cannot read. Without a key it is unconfigured: every call answers `billing_unavailable` (503). A refusal of the key on a call (401 revoked, 403 without the permission) is `PaymentsKeyRefused` (503, `payments_key_refused`), a throttle is `ProviderUnavailable` (503), and a refusal of the request itself (400, 402, 404) is `PaymentsRefused` (502). |
| `PaymentsTwinImpl` | Customers, checkouts, and subscriptions in memory. It signs its own deliveries with the processor's scheme, so they pass the same check, and it lets a test complete a checkout, move a subscription, end a period, and have any event delivered. Every id it makes carries `twin`. |

## The catalog

`PaymentsCatalogInterface` is what the operator's bootstrap reconciles:
products, prices by lookup key, the webhook endpoint, and the Billing
Portal configuration. Its caller is `acme-ops stripe-bootstrap`, never
a serving process, and it runs under a key of its own: the bootstrap
key the person holds, never the runtime key. `payments.permissions`
lists what each key may touch, as Stripe's editor names it.
`CatalogStripeImpl` is the real one, under the same headers and
timeout; `CatalogTwinImpl` keeps the processor's rules that
matter to a reconcile (one price holds a lookup key, a transfer moves
it, an endpoint's secret is shown once) and is what the bootstrap's
tests run against. [The runbook](../docs/runbooks/providers/stripe.md) says how it
is run.

## What a process refuses at boot

- The twin anywhere but `local` and `test`.
- A key that is not a restricted key. An organization key reaches
  every account of the organization, and a secret key may do
  everything in one.
- A key whose mode is not the environment's: production takes a live
  key, every other environment a test key. The prefix says which.

Each refusal names the setting. The settings are `ACME_BILLING_BACKEND`
(`twin` or `stripe`), `ACME_STRIPE_ACCOUNT_ID`, `ACME_STRIPE_RUNTIME_KEY`,
`ACME_STRIPE_WEBHOOK_SECRET`, and `ACME_STRIPE_TIMEOUT_SECONDS`; the
two secrets reach a deployed process at start, and `off` or empty leaves
billing unconfigured.

## Slack

Acme is a Slack app that each org installs into its own workspace.
`acme.integrations.slack.SlackInterface` is the one door to it:

- the install: Slack's page for a workspace, asking for `BOT_SCOPES`
  and carrying a one-time state (`authorize_url`); the code Slack sends
  back, traded with the client id and secret for the workspace's token
  (`exchange_code`, `oauth.v2.access`); a token renewed (`refresh`), and
  a token or an install ended (`revoke`, `uninstall`);
- the check of a call in (`verify_request`): the Slack SDK's signature
  check over the raw body and its timestamp, inside five minutes, in
  constant time. `requests.py` turns a checked call into the
  `SlackInbound` the API queues, keyed on a UUID v5 over the event's
  `event_id` or the command's `trigger_id`;
- the Web API under the token the caller hands in, one call at a time:
  a post, in a thread or not; the email on a person's profile
  (`users.info`); the App Home; and the answer to a command through the
  `response_url` Slack sent with it.

The client never holds a workspace's token. The caller, the slack
manager, resolves it from the org's own secrets for the one call.

Its errors are the decisions a caller needs, not Slack's whole
vocabulary:

| Error | Means | What the caller does |
|-------|-------|----------------------|
| `SlackRateLimited` | Slack asked to wait (429), `retry_after` says how long | The worker parks the item for that long |
| `SlackChannelUnusable` | `channel_not_found`, `not_in_channel`, `is_archived` | Marks the installation broken; `/acme connect` says to invite the bot. The bot's join to the channel (`member_joined_channel`) mends it |
| `SlackTokenRevoked` | `invalid_auth`, `token_revoked`, `invalid_refresh_token`, and the like: the install is gone | Marks the installation broken; a new install mends it |
| `SlackRequestRefused` | A call in whose signature, timestamp, or body did not check out (`401`) | The API refuses it and queues nothing |
| `SlackNotConfigured` | This process holds none of the app's credentials (`503`) | Nothing reaches Slack, and nothing from Slack is accepted |
| `SlackFailed` | Anything else, a transport failure included | The item or the queued call is tried again |

Three impls:

- `SlackWebImpl`, the real client: the app's client id, client secret,
  and signing secret, one aiohttp session opened at start, every call
  under `ACME_SLACK_TIMEOUT_SECONDS`, to the fraction of a second. A
  call that times out is not tried again. A reply goes only to a URL
  under `https://hooks.slack.com/`.
- `SlackTwinImpl`, the twin: Slack's side in memory. It approves an
  install at once for its own workspace, issues tokens that expire and
  refresh tokens that work once, signs requests with Slack's scheme under
  a secret of its own, records every post, refuses a channel its bot was
  removed from and hands back the `member_joined_channel` event Slack
  sends when the bot is invited again, fails on request (one method or
  any), and stamps each message `twin.<n>`. A token names its
  workspace, so a token the local API's twin issued works in the local
  worker's. It refuses to run outside `local` and `test`.
- `SlackOffImpl`: what a process holds when any of the three credentials
  is unset. Every call answers `slack_unavailable`.

`ACME_SLACK_BACKEND` picks `twin` or `slack`, and `slack` with the
three credentials is the real client. The two manifests in
`deployment/slack/` name the same scopes, and a test holds them together.
The app's settings, its credentials, and each environment's app are in
[the Slack runbook](../docs/runbooks/providers/slack.md).

## The identity provider

| What | Interface | Real client | Twin |
|------|-----------|-------------|------|
| Proves who a person is: the hosted sign-in, the code exchange, the device sign-in for the command line, and the logout address that ends the hosted sign-in's session in the browser; holds an org's side of it: its organization, its single sign-on, its invitations; deletes a person who deleted their account, and the organization of a team org its owner or an operator deleted | `identity.IdentityProviderInterface` | `identity/workos.py`: WorkOS AuthKit through the `workos` SDK, pinned | `identity/twin.py`: in memory, for tests and CI |

`identity/absent.py` is the provider of a process that signs nobody in:
every call answers `ProviderUnavailable`, which the API presents as
`503`. An API or a worker whose WorkOS application key is not set holds
it. The worker holds the real client beside the API's: it signs nobody
in, and deletes the WorkOS user of a person who deleted their account
and the WorkOS organization of a team org its owner or an operator deleted.

The provider hands Acme an issuer, a subject, and a verified email,
and for a sign-in through the hosted page, the id of the provider's own
session in that browser (the `sid` claim of the access token WorkOS
answers the exchange with), which the sign-out ends. Everything else (the identity row, its id, the sessions, the
memberships) is Acme's own; the tenancy namespace's README says how a
sign-in becomes a person.

## Settings

| Setting | What |
|---------|------|
| `ACME_IDENTITY_PROVIDER` | `workos`, `twin`, or `none` (the default). `twin` is refused at boot outside `local` and `test`. |
| `ACME_WORKOS_CLIENT_ID` | The Acme App application's client id. Not a secret: every authorization URL carries it. Staging's Acme App serves the local stack and staging; production has its own. |
| `ACME_WORKOS_API_KEY` | The Acme App application's API key, made on that application's own API keys tab, never the environment's API Keys page. A secret, and the process's one WorkOS credential: the client secret of the code exchange (which sends the PKCE verifier as well), and the key of every management call (organizations, invitations, the admin portal), so an invitation carries the application's context. The device sign-in sends no secret. At start the client proves it is the application's key and refuses to boot on any other (ADR 0033). Locally from `.env` or the shell; deployed, injected into the API from the secret store (`<prefix>workos_api_key`). Empty or `off` means not configured: the process starts, says so, and every sign-in through WorkOS answers `503`. |
| `ACME_WORKOS_BASE_URL`, `ACME_WORKOS_TIMEOUT_SECONDS` | Where the client calls, and the timeout every call is sent with (10 seconds). The SDK takes whole seconds, so a fraction is rounded up. |

The WorkOS environments, the application's redirects, and the key are
set up as [the WorkOS runbook](../docs/runbooks/providers/workos.md)
says.

## What every integration holds to

- **A timeout on every call.** The real client builds its own HTTP
  client with the timeout from settings and hands it to the SDK. An SDK
  that names a timeout on each request as well gets the same one, since
  the request's wins over the client's. WorkOS's does, and falls back to
  60 seconds. A test reads the timeout a request is sent with, for each
  provider.
- **A request's deadline on every call a request makes.** A method a
  request can call takes the request's `deadline`, the instant its time
  runs out, which every call of the request shares (ADR 0069). The call
  ends by then, as a call the provider does not answer ends. A worker's
  calls carry none: its item's lease bounds them.
- **One exception family.** Every provider error is translated into an
  integration exception, each a leaf of infra's (`ProviderUnavailable`
  is a `503`, `ProviderRefused` a `400`, `ProviderConflict` a `409`),
  and the one caller translates the sign-in leaves into the platform's
  own. The key never appears in a message.
- **A refused key is unavailable, a refused request is refused.** A
  provider that refuses the process's own credential (revoked, or
  without the permission) answers unavailable: nothing is wrong with
  the call, and it goes through once a person fixes the key. Only a
  refusal of the request itself is a refusal, since the same request
  gets the same answer. Work parks on the first and fails for good on
  the second.
- **A twin that says it is one.** Every id the twin mints starts with
  `twin_`, and the configured root refuses it in a deployed
  environment.

## What a provider that hangs costs a call

Each SDK keeps its own retries. WorkOS's and Stripe's retry a timeout,
so a provider that takes a call and never answers holds it for the
timeout on every attempt, plus the waits between them. Slack's SDK
retries only a dropped connection, never a timeout. At the default
timeout of 10 seconds:

| Provider | Attempts | Waits between them, at most | A call gives up after, at most |
|----------|----------|-----------------------------|--------------------------------|
| WorkOS | 4 | 1.5, 3, and 6 seconds | 50.5 seconds |
| Stripe | 3 | 0.5 and 1 second | 31.5 seconds |
| Slack | 1 | none | 10 seconds |

For WorkOS and Stripe the timeout bounds each wait on the network (to
connect, to send, and between bytes of the answer), not an attempt as a
whole; for Slack it bounds the attempt. WorkOS also waits as long as a
`Retry-After` on a 429 or a server error asks, with no cap of its own.

That is what a call costs a worker. A request makes its calls in turn,
and they share its deadline, `ACME_REQUEST_DEADLINE_SECONDS` (20
seconds) from when it was admitted, so a provider that hangs costs a
request that and no more (ADR 0069):

- a call that starts with no time left does not start;
- a call still waiting at the deadline, on an attempt, on the wait
  before the next, or on a wait the provider asked for, is cut there;
- WorkOS sends each attempt with the smaller of the timeout and what is
  left, and a `Retry-After` longer than what is left ends the call at
  once instead of being slept;
- the call then raises what it raises when the provider does not
  answer: `ProviderUnavailable` for WorkOS, `BackendUnreachable` for
  Stripe (both `503 unavailable`), and `SlackFailed` for Slack.
