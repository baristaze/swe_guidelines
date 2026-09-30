# Integrations

The third-party providers Acme talks to. Each is one interface with a
real client and a twin, and a caller never knows which it holds.
Nothing here imports the object model: a provider's errors root at
infra's exception family, since a provider is a dependency the way a
backend is.

## The identity provider

`identity.IdentityProviderInterface` proves who a person is and holds
an org's side of it:

- the hosted sign-in, the code exchange, the device sign-in for the
  command line, and the logout address that ends the provider's session
  in the browser;
- an org's organization at the provider, its single sign-on link, and
  its invitations;
- the deletion of a deleted account's user and a deleted team org's
  organization;
- the check of an inbound delivery (`verify_delivery`).

The provider hands Acme an issuer, a subject, and a verified email.
Everything else (the identity row, its sessions, its memberships) is
Acme's own; [the tenancy namespace](../om/src/acme/om/tenancy/README.md)
says how a sign-in becomes a person.

| Implementation | What it is |
|----------------|------------|
| `identity/workos.py` | WorkOS AuthKit through the pinned `workos` SDK. At start it proves the key is the application's, and refuses to boot on any other ([ADR 0033](../docs/adr/0033-the-workos-key-is-the-applications.md)). |
| `identity/twin.py` | The provider in memory, for tests and CI. It signs its own deliveries with the provider's scheme. Every id it mints starts with `twin_`. |
| `identity/absent.py` | The provider of a process with none configured, a local stack with no key among them: every call is `ProviderUnavailable`, which the API answers as `503`. The local sign-in by address still works. |

## The webhook check

The provider delivers its events to `POST /webhooks/identity`. The
route takes no credential; the signature is the authentication.
`identity/deliveries.py` checks the `WorkOS-Signature` header, an
HMAC-SHA256 over `<timestamp>.<body>` under `ACME_WORKOS_WEBHOOK_SECRET`,
inside a three-minute window, in constant time. A delivery that fails
is `400 webhook_signature_invalid`, and nothing is queued. One that
passes is queued on `webhooks` with a key that is a UUID v5 over the
provider's event id, so a redelivery carries the same key. The
maintenance worker applies it.

## Settings

| Setting | What |
|---------|------|
| `ACME_IDENTITY_PROVIDER` | `workos`, `twin`, or `none` (the default). The twin is refused at boot outside `local` and `test`. |
| `ACME_WORKOS_CLIENT_ID` | The application's client id. Not a secret. |
| `ACME_WORKOS_API_KEY` | The application's own API key: the exchange's client secret and the key of every management call. Empty or `off` leaves WorkOS unconfigured. |
| `ACME_WORKOS_WEBHOOK_SECRET` | The webhook endpoint's signing secret, injected at start. Unset, every delivery is refused as unavailable. |
| `ACME_WORKOS_BASE_URL`, `ACME_WORKOS_TIMEOUT_SECONDS` | Where the client calls, and the timeout of every call. |

[The WorkOS runbook](../docs/runbooks/providers/workos.md) sets them up.

## What every integration holds to

- **A timeout on every call**, from settings.
- **A request's deadline on every call a request makes**, shared by
  every call of the request; a worker's calls carry none
  ([ADR 0069](../docs/adr/0069-a-request-has-a-deadline-its-provider-calls-share.md)).
- **One exception family.** `ProviderUnavailable` is `503`,
  `ProviderRefused` `400`, `ProviderConflict` `409`. The key never
  appears in a message.
- **A refused key is unavailable; a refused request is refused.** Work
  parks on the first and fails for good on the second
  ([ADR 0051](../docs/adr/0051-a-refused-key-is-unavailable-a-refused-request-is-refused.md)).
- **A twin that says it is one**, and is refused in a deployed
  environment.

## What a provider that hangs costs a call

Each SDK keeps its own retries. WorkOS's SDK retries a timeout, so a
provider that takes a call and never answers holds it for the timeout
on every attempt, plus the waits between them. At the default timeout
of 10 seconds:

| Provider | Attempts | Waits between them, at most | A call gives up after, at most |
|----------|----------|-----------------------------|--------------------------------|
| WorkOS | 4 | 1.5, 3, and 6 seconds | 50.5 seconds |

The timeout bounds each wait on the network (to connect, to send, and
between bytes of the answer), not an attempt as a whole. WorkOS also
waits as long as a `Retry-After` on a 429 or a server error asks, with
no cap of its own.

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
  answer: `ProviderUnavailable` (`503 unavailable`).
