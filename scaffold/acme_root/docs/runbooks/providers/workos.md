# WorkOS: operating the sign-in

People sign in to Acme through WorkOS AuthKit: an email code or link,
Google, GitHub, or their team's own single sign-on. WorkOS proves who
the person is. Everything after that (the person, their orgs, their
sessions) is Acme's own. This page is for a person who has never opened
the WorkOS dashboard: what lives where, what is set by hand once, what a
command checks, and what to do when it breaks.

The two other providers have pages of their own:
[Stripe](stripe.md) (billing) and [Slack](slack.md).

## The rule

**The key is the application's, never the environment's.** Acme signs
in through one WorkOS application, the "Acme App", and holds one WorkOS
credential: an API key made on that application's own API keys tab. The
environment's API Keys page (under Developer) is a different thing. Its
keys belong to the environment's default application, and WorkOS refuses
them as the Acme App's (`invalid_client`). The API refuses to start on
one ([ADR 0033](../../adr/0033-the-workos-key-is-the-applications.md)).

## The levels, and what lives at each

WorkOS nests these levels. Every Acme setting sits on exactly one.

```text
WorkOS team                                          the people who may open the dashboard
|
+-- environment  Staging                             serves local and staging
|   +-- Developer > API Keys                         the default application's keys: not used by Acme
|   +-- Developer > Redirects                        the default application's list: not used by Acme
|   +-- authentication methods                       email code or link, Google, GitHub
|   +-- application  "gmail.com's Application"       the default one WorkOS made: not used by Acme
|   +-- application  "Acme App"  client_STAGING_PLACEHOLDER
|   |   +-- API keys tab                             acme-staging, acme-local: the keys Acme holds
|   |   +-- Redirects tab                            redirect URIs, the initiate login URI, and the rest
|   |   +-- Sessions tab                             the AuthKit session: 30 days, 14 idle
|   +-- organizations                                one per Acme team org, external_id = the org's id
|   |   +-- single sign-on, verified domains         set up by the org's admin
|   +-- users and invitations                        made by sign-ins and by Acme
|
+-- environment  Production                          serves production alone
    +-- the same, with application client_PRODUCTION_PLACEHOLDER and key acme-production
```

| Level | What it is | What Acme keeps there | Who sets it |
|-------|------------|------------------------|-------------|
| Team | The WorkOS account. It holds the people who may use the dashboard. | Two environments | A person, once |
| Environment | A world of its own: its own applications, users, and organizations. Staging and Production share nothing. | Everything below | WorkOS makes both |
| The default application | The application WorkOS made with the environment, first in the Applications list. The environment's API Keys and Redirects pages are its. Invitations sent from the dashboard go to it. | Nothing | Nobody |
| The Acme App | The application Acme signs in through. Its **client id** is in every sign-in URL. It is not a secret. | The client id is committed in `deployment/workos/environments.yaml`, in each Terraform root (`workos_client_id`), and in `.env.example` for local | A person makes it, once per environment |
| The Acme App's API keys tab | The application's own secret keys. Acme's API uses one as the client secret of the sign-in's code exchange, and for every management call: organizations, invitations, the Admin Portal link. The worker holds the same key and deletes the user of a person who deleted their account, and the organization of a team org its owner or an operator deleted. An invitation sent with it lands its person in the Acme App. | One key per Acme environment: in the secret store, or a laptop's shell | A person, once per Acme environment |
| The Acme App's Redirects tab | Where AuthKit may send a person back to, where it sends a person whose sign-in did not start at Acme, and the links on AuthKit's pages and emails. | See [the Redirects tab](#the-redirects-tab-field-by-field) | The bootstrap adds the redirect URIs; a person sets the rest by hand |
| Authentication methods | Which ways to sign in AuthKit offers. | Email, Google, GitHub | A person, once per environment |
| Organizations | One WorkOS organization per Acme team org, carrying the Acme org's id as its `external_id`. Made the first time an owner or an admin invites someone or opens single sign-on. | Made by the API | Acme |
| Admin Portal | WorkOS's own pages where an org's admin connects their company's identity provider and verifies a domain. Acme opens it with a link it asks the API for. | Nothing to set | The org's admin, from Acme's settings page |

## Which Acme environment uses which WorkOS environment

| Acme environment | WorkOS environment | Acme App client id | Its key | Its redirect URI | Its sign-out URI |
|-------------------|--------------------|---------------------|---------|------------------|------------------|
| `local` | Staging | `client_STAGING_PLACEHOLDER` | `acme-local` | `http://localhost:55173/auth/callback` (the stack's portal), `http://localhost:5173/auth/callback` (the Vite dev server) | `http://localhost:55173/signed-out`, `http://localhost:5173/signed-out` |
| `staging` | Staging | `client_STAGING_PLACEHOLDER` | `acme-staging` | `https://app.staging.acme.example/auth/callback` | `https://app.staging.acme.example/signed-out` |
| `production` | Production | `client_PRODUCTION_PLACEHOLDER` | `acme-production` | `https://app.acme.example/auth/callback` | `https://app.acme.example/signed-out` |

Local and staging share one WorkOS environment and one application. So
the Staging Acme App's Redirects tab holds the three staging-side
redirect URIs and the three staging-side sign-out URIs. Each Acme
environment still takes only its own: the API refuses a sign-in that
asks to come back anywhere else (`ACME_SIGN_IN_REDIRECT_URIS`), and a
sign-out that asks WorkOS to send the person anywhere else
(`ACME_SIGN_OUT_RETURN_URIS`). Each gets a key of its own, so one can
be revoked without the other.

Sharing has two consequences. The WorkOS organizations and users that
local runs make sit in the same Staging environment as staging's.
Neither sees the other's, because Acme finds an organization by the
Acme org's id. And an invitation sent from the local stack carries the
Staging Acme App's initiate login URI, so its link lands on staging's
`/login`. To accept it on the laptop, open
`http://localhost:55173/login?invitation_token=<token>` with the token
from the link.

## The Redirects tab, field by field

The Acme App's **Redirects** tab shows seven fields. This is what
Acme sets in each, and why. `<portal>` is
`https://app.staging.acme.example` in Staging and `https://app.acme.example`
in Production.

| Field | What Acme sets | Why |
|-------|-----------------|-----|
| Redirect URIs | Staging: `http://localhost:55173/auth/callback`, `http://localhost:5173/auth/callback`, `https://app.staging.acme.example/auth/callback`. Production: `https://app.acme.example/auth/callback`. The default is `<portal>/auth/callback`. | Where AuthKit sends a person back with a code. Acme names one in every sign-in it starts. WorkOS requires one default; the deployed callback is it, so nothing WorkOS sends on its own lands on a laptop's port. |
| App homepage URL | `<portal>` | WorkOS shows it as the link to the app on AuthKit's pages and in the emails it sends, the invitation email among them. |
| Initiate login URI | `<portal>/login` | Where AuthKit sends a person whose sign-in did not start at Acme: an invitation link, a bookmark of the AuthKit page. The portal's `/login` starts a sign-in at once. WorkOS keeps the invitation through this redirect. |
| Sign-out URIs | Staging: `http://localhost:55173/signed-out`, `http://localhost:5173/signed-out`, `https://app.staging.acme.example/signed-out`. Production: `https://app.acme.example/signed-out`. The default is `<portal>/signed-out`. | Where WorkOS's logout sends a person once it has ended their AuthKit session. A sign-in through AuthKit leaves that session in the browser, and while it lives, the next sign-in there goes through with no prompt. So the portal's sign-out ends Acme's session, then sends the browser to WorkOS's logout with a `return_to` of its own `/signed-out`. WorkOS sends a person only to a URI on this list. The default is where it sends one whose logout names no return. |
| Sign-up URL | Not set | Only for an app that hosts its own sign-up page. AuthKit hosts it, and the portal asks for it with `/login?screen_hint=sign-up`. |
| User invitation URL | Not set | Only for an app that hosts its own sign-in page. Not set, the invitation email links to AuthKit's own page for it, which accepts the invitation and sends the person on through the initiate login URI. The portal's `/login` also takes `?invitation_token=`, so a link made by hand works too. |
| Password reset URL | Not set | Acme turns on no password sign-in, so nothing sends a reset. |

## The Sessions tab, field by field

A sign-in through AuthKit leaves an AuthKit session in the browser.
While it lives, the next sign-in there goes through with no prompt. The
portal keeps its Acme session in the tab, so a new tab signs in again,
and this session decides whether the person sees a prompt. The Acme
session itself is Acme's own: WorkOS's settings never end it early or
keep it alive. Acme never refreshes the AuthKit session.

| Field | What Acme sets | Why |
|-------|-----------------|-----|
| Maximum session length | 30 days | A Acme session's absolute lifetime (`ACME_SESSION_LIFETIME_SECONDS`, [ADR 0063](../../adr/0063-sessions-last-weeks.md)). A new tab asks for a sign-in once a month, as an open one does. |
| Inactivity timeout | 14 days | A Acme session's idle lifetime (`ACME_SESSION_IDLE_LIFETIME_SECONDS`). A person away for two weeks signs in again in a new tab too. |
| Access token duration | WorkOS's default | Acme reads the access token once, at the exchange, for its session id, and never presents it. |

## First-time setup, by hand

Do these once per WorkOS environment. Staging first.

### 1. Find the Acme App

1. Sign in at [dashboard.workos.com](https://dashboard.workos.com).
2. Pick the environment in the switcher (top left): **Staging**.
3. Open **Applications**. The first row, marked **Default**, is the
   application WorkOS made. Acme does not use it. Open **Acme App**.
   If it is not there, choose **Create application** and name it
   "Acme App".
4. Its client id is under its name. It must be the one the table above
   names.

**Check.** The client id matches `deployment/workos/environments.yaml`.
If WorkOS made a new application with a new id, the committed id is
wrong: change it in that file, in the environment's Terraform root, and
in `.env.example`, in one pull request.

### 2. Make the API key

1. In the Acme App, open the **API keys** tab.
2. Choose **Create key**.
3. **API key name**: `acme-<env>`, the Acme environment that will hold
   it: `acme-staging`, `acme-local`, or in Production
   `acme-production`.
4. **Expiration**: **Never**. A date is the owner's choice, for a forced
   rotation. If you pick one, put the rotation on a calendar before that
   day: on it, the key stops working, every sign-in answers `503`, and a
   restarting API refuses to start.
5. Choose **Create key**. WorkOS shows the key once. Keep it in your
   password manager.

Never take a key from **Developer > API Keys**. That page is the
environment's, and its keys are the default application's.

### 3. The Redirects and Sessions tabs

The bootstrap adds the redirect URIs and checks the key. It needs a key
from step 2:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
read -rs WORKOS_API_KEY && export WORKOS_API_KEY    # a Staging Acme App key
uv run acme-ops workos-bootstrap --environment staging --apply
unset WORKOS_API_KEY
```

Then, in the Acme App, open the **Redirects** tab and set by hand what
no API writes, as [the table above](#the-redirects-tab-field-by-field)
says:

1. **Redirect URIs**: open it and mark
   `https://app.staging.acme.example/auth/callback` as the default.
2. **App homepage URL**: `https://app.staging.acme.example`.
3. **Initiate login URI**: `https://app.staging.acme.example/login`.
4. **Sign-out URIs**: add each of these, one at a time:
   `http://localhost:55173/signed-out`,
   `http://localhost:5173/signed-out`, and
   `https://app.staging.acme.example/signed-out`. Mark
   `https://app.staging.acme.example/signed-out` as the default.
5. Leave **Sign-up URL**, **User invitation URL**, and **Password reset
   URL** not set.

Then open the **Sessions** tab, as
[its table](#the-sessions-tab-field-by-field) says:

1. **Maximum session length**: 30 days.
2. **Inactivity timeout**: 14 days.
3. Leave **Access token duration** at its default.

Do not add redirects on **Developer > Redirects**. That list is the
default application's, so an entry there changes nothing for Acme.

**Check.** Run the bootstrap again. It ends `nothing to change`.

### 4. Turn on the ways to sign in

1. In the environment, open **Authentication**.
2. Turn on email (a one-time code or a magic link), Google, and
   GitHub. Leave passwords off.

In Staging, WorkOS can use its own demo credentials for Google and
GitHub, so nothing more is needed. In Production, each needs an OAuth
app of your own at Google and at GitHub, and its credentials entered
here.

### 5. Let the first deploy make the secret container

The first deploy that carries sign-in makes
`acme/<env>/workos_api_key` in the environment's AWS account, holding
`off`. A secret in AWS cannot be empty, so Acme uses the plain word `off`
as a secret's value to mean "not set". Turning a secret off means
replacing its value with that word, and nothing else.

With `off`, the API starts, says so in its log, and every sign-in
through WorkOS answers `503`.

**Check**, under your own sign-in:

```bash
aws secretsmanager describe-secret --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/workos_api_key --query '{name: Name, changed: LastChangedDate}'
```

`ResourceNotFoundException` means the deploy has not run yet. Wait for
it. A value written before then makes a secret Terraform does not own,
and the next deploy fails on it.

### 6. Write the key into the secret store

The key is `acme-staging` from step 2. Keys exported in the shell
outrank a profile, so clear them first. `read -rs` takes the value
without showing it:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sso login --profile acme-staging
read -rs VALUE    # paste the key, press Enter; nothing is shown
aws secretsmanager put-secret-value --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/workos_api_key --secret-string "$VALUE"
unset VALUE
```

### 7. Let the API and the worker pick it up

The API and the worker read the key when a task starts. The next deploy
does it. To pick it up now:

```bash
for service in api maintenance; do
  aws ecs update-service --profile acme-staging --region us-west-2 \
    --cluster acme-staging --service "$service" --force-new-deployment
done
```

**Check.** The API's start line in `/acme/staging/api` names the
provider as `WorkOS (https://api.workos.com, client
client_STAGING_PLACEHOLDER)`. Before the key, it names `none`
and a warning says every sign-in answers `503`. A key that is not the
Acme App's stops the task at start with `ACME_WORKOS_API_KEY is not
the API key of the WorkOS application`. Then open
`https://app.staging.acme.example`, choose to sign in, and finish with an
email code. You land in Acme, signed in.

## What is automated, and by what

| What | Done by | When |
|------|---------|------|
| The secret container, holding `off` | Terraform, `deployment/terraform/modules/secrets` | The first deploy that carries it |
| The key inside the API and the worker | The deploy: each API and worker task gets it at start | Every task start |
| The proof that the key is the Acme App's | The API at start; the bootstrap, first thing | Every task start; every bootstrap run |
| The client id and the allowed callback | Terraform, from the environment's root | Every deploy |
| The Acme App's redirect URIs | `acme-ops workos-bootstrap --apply` | A person runs it after a change to `environments.yaml` |
| The Redirects tab's other fields, the sign-out URIs among them | A person, by hand; the bootstrap prints each as a check | Once, and after a change to `environments.yaml` |
| The Sessions tab's two lifetimes | A person, by hand; the bootstrap prints each as a check | Once, and after a change to a Acme session's lifetimes |
| The allowed sign-out return | Terraform, from the environment's root (`ACME_SIGN_OUT_RETURN_URIS`) | Every deploy |
| Organizations, invitations, Admin Portal links | The API | When an owner or an admin invites or opens single sign-on |
| A deleted account's WorkOS user, deleted | The worker (`DELETE_ACCOUNT`) | When a person deletes their account |
| A deleted team org's WorkOS organization, deleted, with its connections, domains, and invitations | The worker (`DELETE_ORG`) | When an owner deletes their team org in Settings, or an operator deletes it |

### The bootstrap

`acme-ops workos-bootstrap` reads `deployment/workos/environments.yaml`
and works on the Acme App with the key the file's variable holds.

1. It proves the key. It exchanges a code WorkOS never issued, with the
   key as the client secret. WorkOS answers `invalid_grant` for the
   Acme App's own key and `invalid_client` for any other. Any other key
   stops the run with exit 2, before anything is read.
2. It reads the Acme App's redirect list and asks AuthKit about each
   redirect URI, with the same request a browser makes to start a
   sign-in. That answer is the truth.
3. A missing URI is added to the list under `--apply` and asked about
   again. Without `--apply` it says `would create`.
4. It checks which URI is the default. The API does not set it, so a
   wrong default is a dashboard step.
5. It prints the tab's other fields as checks to make by eye. The
   sign-out URIs are among them: WorkOS has no API for them, and its
   logout answers a made-up session the same whatever the return, so
   no request can probe them either. It prints the Sessions tab's two
   lifetimes the same way.

A URI or a default only the dashboard can fix makes the run exit 1.
When everything is in place, the output ends:

```text
default redirect: https://app.staging.acme.example/auth/callback
login initiation URI: https://app.staging.acme.example/login (check it on the Redirects tab; no API reads or writes it)
app homepage URL: https://app.staging.acme.example (check it on the same tab)
sign-out URIs: http://localhost:55173/signed-out, http://localhost:5173/signed-out, https://app.staging.acme.example/signed-out, default https://app.staging.acme.example/signed-out (check them on the same tab; no API reads or writes them)
sign-up URL: not set: AuthKit hosts the sign-up page
user invitation URL: not set: AuthKit's page takes it, then the login initiation URI
password reset URL: not set: no password sign-in is on
maximum session length: 30 days, a Acme session's absolute lifetime (check it on Applications, application client_STAGING_PLACEHOLDER, its Sessions tab; no API reads or writes it)
inactivity timeout: 14 days, a Acme session's idle lifetime (check it on the same tab)
webhooks: none
nothing to change
```

A second run says `nothing to change` again.

For production the variable is `WORKOS_PRODUCTION_API_KEY`, and the
flag `--environment production`.

## Local development

The laptop signs in through the Staging Acme App, whose Redirects tab
holds the local callbacks. Its key is `acme-local` (step 2).

```bash
read -rs ACME_WORKOS_API_KEY && export ACME_WORKOS_API_KEY    # the Staging Acme App's acme-local key
make up
```

`.env.example` names the client id and leaves `ACME_WORKOS_API_KEY`
commented out: a key is never committed. What the shell exports wins
over `.env` and `.env.example` alike.

A sign-out after a sign-in through WorkOS goes through WorkOS's logout
and comes back to the portal's `/signed-out` on the port it left from.
Both local ports are on the Staging Acme App's sign-out URIs for that.

Without the key, the stack still runs. Sign-in through WorkOS answers
`503`, and the local sign-in by address (`/login/dev`) still works. Its
sign-out stays on the portal: there is no WorkOS session to end.
With a key that is not the Acme App's, the API container stops at
start and says why.

## Rotation

1. Make a new key on the Acme App's **API keys** tab (step 2), under
   the same name.
2. Write it (step 6).
3. Roll the API and the worker (step 7), and check the start lines.
4. Revoke the old key on the same tab.

Both keys work until the last step, so nobody is signed out. Sessions
Acme already issued are Acme's own and outlive any key.

## When it breaks

| What you see | Why | Where to look |
|--------------|-----|---------------|
| The API's tasks stop at start: `ACME_WORKOS_API_KEY is not the API key of the WorkOS application` | The secret holds a key WorkOS refuses as the Acme App's: one from Developer > API Keys, another application's, or the other WorkOS environment's | Make a key on the Acme App's API keys tab (step 2), write it (step 6) |
| `the WorkOS credential check did not finish` in the start log | WorkOS could not be reached at start. The API started anyway | WorkOS's status page; the next start checks again |
| Every sign-in through WorkOS answers `503` | `acme/<env>/workos_api_key` is `off`, or the task started before it was written, or the key was revoked or expired since the start | `/acme/<env>/api`: the start line names `identity provider: none (ACME_WORKOS_API_KEY is not set)`, or a request logs `refused ACME_WORKOS_API_KEY as the application's` |
| AuthKit shows an "invalid redirect URI" page | The callback is not on the Acme App's Redirects tab | Run the bootstrap with `--apply` |
| After signing out, WorkOS does not send the person back to `/signed-out` | The portal's `/signed-out` is not among the Acme App's sign-out URIs | The Acme App's Redirects tab, **Sign-out URIs** (step 3) |
| Signing out answers `422` | The portal asked to come back to a page the API does not name in `ACME_SIGN_OUT_RETURN_URIS` | The environment's Terraform root, or `.env` locally |
| After signing out, the next sign-in goes straight through with no prompt | The session came from a sign-in that left no AuthKit session Acme knows of: a device sign-in, or a sign-in made before the release that keeps it | Nothing to fix: the next sign-in and sign-out end both |
| A sign-in that did not start at Acme lands on a WorkOS error page | The initiate login URI is missing or wrong | The Acme App's Redirects tab |
| A deleted account's work sits parked, its note `a provider is out of reach` | The worker's key is `off`, or WorkOS is down | `/acme/<env>/maintenance`: the start line names the identity provider; the item goes on by itself once WorkOS answers |
| A deleted account's work fails with `deleting the user` | WorkOS refused the Acme App's key the deletion | WorkOS's request log for the key; the item fails for good after its attempts, and the person's personal org stays until it runs again by hand |
| A deleted org's work sits parked, or fails with `deleting the organization` | As for an account: the key is `off` or WorkOS is down; or WorkOS refused the call | As for an account; the org stays live and empty until the item runs again |
| An invited person lands somewhere other than Acme | The invitation was sent from the WorkOS dashboard, which always uses the default application | Invite from Acme's settings page |
| An invitation or the Admin Portal link fails | WorkOS is down, or the key was revoked | `/acme/<env>/api`, then WorkOS's status page |
| A sign-in waits 20 seconds, then answers `503`: `sign-in is not available: exchanging the sign-in code: the request's deadline passed` | WorkOS does not answer. The request's deadline, `ACME_REQUEST_DEADLINE_SECONDS` (20 seconds), ends it (ADR 0069); each call alone waits `ACME_WORKOS_TIMEOUT_SECONDS` (10 seconds) an attempt, three more times | WorkOS's status page, then `/acme/<env>/api` |
| A person signs in at WorkOS but Acme refuses them | Their address is not verified at WorkOS, or single sign-on is not theirs to join (their domain is not verified by the org) | `/acme/<env>/api`, by the request id the portal shows |

## When an environment is torn down

`scripts/cloud_nuke.sh` does not touch WorkOS. What stays:

- **The organizations** staging's team orgs made, each with the old
  Acme org's id as its `external_id`. A recreated staging makes new
  orgs with new ids, so the old ones are never found again. They are
  harmless. Delete them under **Organizations** if you want a tidy
  environment; local runs make organizations in the same place, so
  check the `external_id` before you delete.
- **The users** who signed in. Harmless for the same reason.
- **The Acme App and its Redirects tab.** Keep them: the next staging
  uses the same names.
- **The API key.** The secret that held it is gone, so a recreated
  environment needs it written again (step 6). Revoke it on the Acme
  App's API keys tab if the environment is not coming back.

## Production (parked)

Production is not running yet. When it is, the steps are the same, in
the **Production** WorkOS environment, with application
`client_PRODUCTION_PLACEHOLDER` and its key `acme-production`:

- Its Redirects tab holds one redirect URI,
  `https://app.acme.example/auth/callback`, the default, and one sign-out
  URI, `https://app.acme.example/signed-out`, the default. The App homepage
  URL is `https://app.acme.example` and the initiate login URI
  `https://app.acme.example/login`. Never a `localhost` one.
- Google and GitHub need your own OAuth credentials (step 4).
- The everyday production profile, `acme-prod`, only reads. Writing
  the secret and rolling the API need `acme-prod-power`, and only when
  that change is explicitly authorized.
- The bootstrap reads `WORKOS_PRODUCTION_API_KEY`:

  ```bash
  read -rs WORKOS_PRODUCTION_API_KEY && export WORKOS_PRODUCTION_API_KEY
  uv run acme-ops workos-bootstrap --environment production --apply
  unset WORKOS_PRODUCTION_API_KEY
  ```

What must never happen:

- **A key from the environment's API Keys page.** It is the default
  application's. The API refuses to start on it, and it would send
  every invitation to the default application.
- **A Staging key in production, or a Production key in staging or on
  a laptop.** Each is another application's key, so the API refuses to
  start on it.
- **A key in the repository, a chat, a ticket, or a log.** The names
  are enough everywhere.
