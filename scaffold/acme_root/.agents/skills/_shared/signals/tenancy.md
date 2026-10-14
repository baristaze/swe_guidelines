# Signals: tenancy

The orgs and their members, the sign-in, and the identity provider
behind it (WorkOS), whose webhook tells the system what changed there.

## Kinds

- `DELETE_ACCOUNT`: a deleted account's providers, then its personal
  org. One that failed for good leaves the deleted person's personal
  org in place until it runs.
- `DELETE_ORG`: a closed team org's providers, then the org.

## Log lines

The identity provider says in its own log when it is not configured.
Each line names the secret it points to; the secret's value is never
read.

- `identity provider: none (ACME_WORKOS_API_KEY is not set)` and
  `every sign-in through one answers 503`: nobody can sign in through
  WorkOS. `acme/<env>/workos_api_key` is `off`, or the API's tasks
  started before it was written. A laptop that sets no
  `ACME_WORKOS_API_KEY` signs people in by the local sign-in on
  purpose, so this line is not a finding locally.
- `ACME_WORKOS_API_KEY is not the API key of the WorkOS application`,
  and the API's tasks stop at start: the secret holds a key WorkOS
  refuses as the Acme App's (the environment's API key, another
  application's, or the other WorkOS environment's). The key belongs
  on the Acme App's own API keys tab. `the WorkOS credential check
  did not finish` or `answered` is a warning only: WorkOS could not
  say, and the API started.
- `ACME_WORKOS_WEBHOOK_SECRET is not set` on `POST
  /webhooks/identity`, a `503` there in the access lines of
  `/acme/<env>/api`: `acme/<env>/workos_webhook_secret` is `off`, or
  the API's tasks started before it was written. Every delivery is
  refused, and WorkOS sends it again later.
- A `400` (`webhook_signature_invalid`) on `/webhooks/identity` in
  the access lines of `/acme/<env>/api`: the secret does not match
  the signing secret of the endpoint in the WorkOS dashboard, so
  every delivery is refused.
- `identity delivery <id> names no org` or `names org <id>, which
  is gone` in `/acme/<env>/maintenance`: one delivery about an org
  this environment does not hold, dropped. Not a finding about a
  secret.

A finding about a secret names the secret and
`docs/runbooks/providers/workos.md`.

## Search

`identity provider|WorkOS application|WorkOS credential check|ACME_WORKOS_WEBHOOK_SECRET|webhooks\/identity (400|503)|identity delivery`

## Report

Locally, where a laptop sets no `ACME_WORKOS_API_KEY` by design, the
line is `- Identity provider: local sign-in`, and it names no secret
and no runbook. Otherwise:

```markdown
- Identity provider: sign-in <configured | off: acme/<env>/workos_api_key | not in the window>, webhook <configured | off: acme/<env>/workos_webhook_secret | signature refused: acme/<env>/workos_webhook_secret | not in the window>
```
