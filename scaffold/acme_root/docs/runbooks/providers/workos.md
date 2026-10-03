# WorkOS: the identity provider

People sign in through WorkOS AuthKit: an email code or link, Google,
GitHub, or their team's own single sign-on. WorkOS proves who the person
is; the identity, its orgs, and its sessions are Acme's own
([ADR 0028](../../adr/0028-sign-in-is-the-identity-providers.md)). This
page says what lives where, what is set by hand once, what a command
checks, and what to do when it breaks.

## The rule

**The key is the application's, never the environment's.** Acme signs in
through one WorkOS application, the "Acme App", and holds one API key made
on that application's own API keys tab. A key from the environment's API
Keys page belongs to the default application: WorkOS refuses it as the
Acme App's (`invalid_client`), and the API refuses to start on it
([ADR 0033](../../adr/0033-the-workos-key-is-the-applications.md)).

## Environments

| Acme environment | WorkOS environment | Client id | Key name | Callback |
|------------------|--------------------|-----------|----------|----------|
| `local` | Staging | `client_STAGING_PLACEHOLDER` | `acme-local` | `http://localhost:55173/auth/callback`, `http://localhost:5173/auth/callback` |
| `staging` | Staging | `client_STAGING_PLACEHOLDER` | `acme-staging` | `https://app.staging.acme.example/auth/callback` |
| `production` | Production | `client_PRODUCTION_PLACEHOLDER` | `acme-production` | `https://app.acme.example/auth/callback` |

The client ids are committed in `deployment/workos/environments.yaml`,
in each Terraform root (`workos_client_id`), and in `.env.example`. They
are not secrets. Local and staging share the Staging environment and its
application, and each takes only its own callback
(`ACME_SIGN_IN_REDIRECT_URIS`) and its own sign-out return
(`ACME_SIGN_OUT_RETURN_URIS`). Each holds a key of its own, so one can be
revoked without the other.

## First-time setup, once per WorkOS environment

1. **Find the Acme App.** In the dashboard, pick the environment, open
   Applications, and open "Acme App" (not the one marked Default). Its
   client id must match `environments.yaml`.
2. **Make the key.** On the Acme App's API keys tab, create `acme-<env>`
   with no expiry, and keep it in a password manager. Never take one from
   Developer, API Keys.
3. **Run the bootstrap.** It proves the key, adds the missing redirect
   URIs, checks the webhook endpoint, and prints what the dashboard alone
   sets:

   ```bash
   unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
   read -rs WORKOS_API_KEY && export WORKOS_API_KEY    # a Staging Acme App key
   uv run acme-ops workos-bootstrap --environment staging --apply
   unset WORKOS_API_KEY
   ```

   Production reads `WORKOS_PRODUCTION_API_KEY` with
   `--environment production`.
4. **Set the Redirects tab by hand**, as the bootstrap prints: the default
   redirect URI (the deployed callback), the app homepage URL
   (`<portal>`), the initiate login URI (`<portal>/login`), and the
   sign-out URIs (`<portal>/signed-out`, plus the two local ports in
   Staging), with the deployed one as the default
   ([ADR 0036](../../adr/0036-sign-out-ends-the-providers-session.md)).
   Leave the sign-up, invitation, and password reset URLs unset.
5. **Set the Sessions tab by hand**: maximum session length 30 days,
   inactivity timeout 14 days, the same bounds as Acme's own session
   ([ADR 0063](../../adr/0063-sessions-last-weeks.md)).
6. **Turn on the sign-in methods**: email, Google, and GitHub, with
   passwords off. Production needs OAuth credentials of its own at Google
   and GitHub.
7. **Add the webhook endpoint** (below). Until it exists and is
   enabled, the bootstrap names it as a dashboard step and fails.

Run the bootstrap again: it ends `nothing to change`.

## The webhook

WorkOS delivers its events to `https://<api host>/webhooks/identity`.
The API checks the `WorkOS-Signature` header (an HMAC over the timestamp
and the body, inside a three-minute window) before anything is queued. A
checked delivery goes onto the `webhooks` queue, and the maintenance
worker records each one whose organization names one of Acme's orgs as the
audit event `identity.event.received` in that org's stream, once per
delivery. A delivery that names no org is dropped.

1. In the WorkOS environment, open Webhooks and create an endpoint at
   `https://api.staging.acme.example/webhooks/identity` (production:
   `https://api.acme.example/webhooks/identity`).
2. Subscribe it to the organization events. Those carry the Acme org's
   id as the organization's `external_id`.
3. Copy the endpoint's signing secret, and write it into the environment
   (below). The API reads it as `ACME_WORKOS_WEBHOOK_SECRET`.

Until the secret is set, every delivery is refused with `503`, and
WorkOS retries it. A delivery whose signature does not check out is
refused with `400 webhook_signature_invalid`.

## The secrets of a deployed environment

The first deploy makes two secrets in the environment's account, each
holding `off`, which the API reads as not set:

| Secret | Setting | Read by |
|--------|---------|---------|
| `acme/<env>/workos_api_key` | `ACME_WORKOS_API_KEY` | the API and the worker |
| `acme/<env>/workos_webhook_secret` | `ACME_WORKOS_WEBHOOK_SECRET` | the API |

Write each value under your own sign-in, then roll the services so their
tasks start with it:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sso login --profile acme-staging
read -rs VALUE
aws secretsmanager put-secret-value --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/workos_api_key --secret-string "$VALUE"   # or workos_webhook_secret
unset VALUE
for service in api maintenance; do
  aws ecs update-service --profile acme-staging --region us-west-2 \
    --cluster acme-staging --service "$service" --force-new-deployment
done
```

Production's everyday profile only reads. Writing there takes
`acme-prod-power`, and only for a change a person authorized.

**Check.** The API's start line in `/acme/<env>/api` names
`WorkOS (https://api.workos.com, client <client id>)`. Then sign in at the
portal with an email code.

## Local development

The laptop signs in through the Staging Acme App with its `acme-local`
key, exported in the shell or set in `.env`:

```bash
read -rs ACME_WORKOS_API_KEY && export ACME_WORKOS_API_KEY
make up
```

Without a key the stack still runs: a sign-in through WorkOS answers
`503`, and the local sign-in by address (`/login/dev`) works. The tests
and CI run the twin (`ACME_IDENTITY_PROVIDER=twin`), which signs its own
webhook deliveries and needs no secret. A key is never committed.

## Rotation

1. Make a new key on the same tab.
2. Write it, and roll the API and the worker.
3. Check the start lines, then revoke the old key.

Both keys work until the last step, so nobody is signed out. The webhook
secret rotates the same way, from the endpoint's page, and rolls the API
alone.

## When it breaks

| What you see | Why | Where to look |
|--------------|-----|---------------|
| The API stops at start: `ACME_WORKOS_API_KEY is not the API key of the WorkOS application` | The key is the environment's, another application's, or the other environment's | Make a key on the Acme App's tab, and write it |
| Every sign-in through WorkOS answers `503` | The key is `off`, revoked, or expired, or WorkOS is down | `/acme/<env>/api`: the start line names the provider |
| AuthKit shows an invalid redirect page | The callback is not on the Redirects tab | Run the bootstrap with `--apply` |
| After a sign-out, WorkOS does not return to `/signed-out` | The page is not among the sign-out URIs | The Redirects tab |
| Signing out answers `422` | The return is not in `ACME_SIGN_OUT_RETURN_URIS` | The environment's Terraform root, or `.env` |
| Deliveries fail with `503` in the WorkOS dashboard | `acme/<env>/workos_webhook_secret` is `off` | Write it, and roll the API |
| Deliveries fail with `400` | The secret is another endpoint's | Copy it again from this endpoint's page |
| No `identity.event.received` in an org's stream | The event names no Acme org, or the worker is down | `/acme/<env>/maintenance`, and the queue's dead-letter twin |
| A deleted account's or org's work parks | The worker's key is `off`, or WorkOS is down | It goes on once WorkOS answers; see [operate.md](../operate.md) for a failed item |
| An invited person lands outside Acme | The invitation came from the WorkOS dashboard | Invite from Acme's settings |

## When an environment is torn down

`scripts/cloud_nuke.sh` does not touch WorkOS. The organizations and
users a staging run made stay, harmless: a new staging makes orgs with new
ids. Keep the Acme App, its tabs, and the endpoint for the next staging.
Revoke the key if the environment is not coming back.
