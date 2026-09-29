# Slack: operating the Slack app

Acme is a Slack app that each org installs into its own Slack
workspace. People type `/acme` in Slack, and Acme posts reminders and
task updates in the channel the org picks. This page is for a person who
has never opened Slack's app settings: what lives where, what is set by
hand once, what the deploy sets, and what to do when it breaks.

The two other providers have pages of their own:
[Stripe](stripe.md) (billing) and [WorkOS](workos.md) (sign-in).

## How it works, in one screen

```text
Slack                                        Acme (one environment)
-----                                        -----------------------
app "Acme (staging)"  A0C3MMXH2AH           API  https://api.staging.acme.example
  /acme  ------------- signed POST -------> /webhooks/slack/commands  -+
  events  ------------- signed POST -------> /webhooks/slack/events    -+-> queue acme-staging-slack
  "Allow" on install -- browser redirect --> /webhooks/slack/oauth        |
                                                                          v
workspace T...  <------ chat.postMessage --- maintenance worker <---------+
                        (the org's own token)
```

- Slack calls the API over HTTPS. Each call is signed with the app's
  **signing secret**, and the API checks the signature before it reads
  anything. A call that fails the check is refused with `401`.
- The API answers Slack at once and puts the call on the `slack` queue.
  The worker does the work: it reads tasks, adds one, binds a channel,
  and answers the person through Slack.
- An org installs the app from Acme's settings. The install is OAuth v2:
  Slack hands Acme a **bot token for that workspace alone**. Acme keeps
  it as the org's own secret, never on a row and never in a log, and
  renews it every twelve hours (token rotation).
- One workspace belongs to one org. A second org installing the same
  workspace is refused.

## The levels, and what lives at each

```text
Slack app  (one per Acme environment)             api.slack.com/apps/<app id>
+-- App Credentials: client id, client secret, signing secret
+-- the manifest: bot user, /acme, events, redirect URL, scopes, token rotation
+-- distribution: public, so any workspace can install it
|
+-- workspace  (one per org that installed it)
    +-- the bot user @acme and its token      held by Acme, per org
    +-- channels                               the org binds one with /acme connect
```

| Level | What it is | What Acme keeps there | Who sets it |
|-------|------------|------------------------|-------------|
| App | The Acme integration for one environment. Its settings are at `api.slack.com/apps/<app id>`. Staging's app is `A0C3MMXH2AH`, in the workspace `TQSHA9YBT`. | Everything below | A person, from the committed manifest |
| Manifest | The app's whole configuration as one JSON document: display name, bot user, the `/acme` command and its URL, the events URL and the events, the OAuth redirect URL, the bot scopes, Socket Mode off, token rotation on. | `deployment/slack/manifest.<environment>.json` | A person pastes it; the file is the record |
| App Credentials | On **Basic Information**: the **Client ID** (not a secret), the **Client Secret**, and the **Signing Secret**. | The client id in the environment's Terraform root; the two secrets in the environment's secret store | A person, once, and at each rotation |
| Distribution | Whether workspaces other than the app's own may install it. | Public | A person, once per app |
| Workspace | A Slack team that installed the app. It gets a bot user there, `@acme`, and Acme gets a bot token for it. | One installation per org, and its token as the org's own secret | The org's owner or admin, from Acme's settings |
| Channel | Where the org's reminders and task updates go. The bot must be a member. | The channel id on the installation | The org, in Slack: `/invite @acme`, then `/acme connect` |

### The bot's scopes, and why each

| Scope | Needed for |
|-------|------------|
| `commands` | The `/acme` slash command |
| `chat:write` | Posting reminders and task updates in the bound channel, the first line of `/acme connect`, and the reply to a mention |
| `app_mentions:read` | Receiving `app_mention`, so `@acme` answers with the usage |
| `users:read` | `users.info`: who typed the command |
| `users:read.email` | The email on that person's profile, which is how Acme matches them to a member of the org |
| `channels:read` | Receiving `member_joined_channel` from a public channel, so Acme hears that `@acme` was invited back to the channel it posts to |
| `groups:read` | The same, from a private channel |

Nothing else. The app never asks for `incoming-webhook`,
`channels:history`, or `chat:write.public`: the channel is bound by
typing a command in it, the bot posts only where it was invited, and it
reads no message. The two `:read` scopes also allow `conversations.list`
and `conversations.info`; Acme calls neither, and takes the scopes for
the event alone.

### The events, and what each does

| Event | Acme does |
|-------|------------|
| `app_mention` | Answers the usage in the mention's thread, once |
| `app_home_opened` | Publishes the Home tab: the commands and how to bind a channel |
| `app_uninstalled` | Deletes the org's installation and its token |
| `tokens_revoked` | The same, when Slack names the bot's token |
| `member_joined_channel` | When the member is `@acme` and the channel is the one it posts to, mends an installation that channel broke: posting resumes, with no `/acme connect`. Any other join is ignored |

### The commands

| Command | Answers, to the person who typed it alone |
|---------|--------------------------------------------|
| `/acme` | Your ten newest open tasks: assigned to you, or unassigned and made by you, as My tasks in Acme. When there are more, a count and a link to Acme |
| `/acme team` | The org's ten newest open tasks, anyone's or nobody's, with the same count and link |
| `/acme add <title>` | Adds a task, made by you |
| `/acme connect` | Makes this channel the one Acme posts to. An owner or an admin types it, after `/invite @acme` |
| `/acme help` | The usage; anything else answers it too |

The person is matched by the email on their Slack profile: the member of
the org whose Acme sign-in holds the same address. Someone Acme does not
know is told to ask an owner or an admin to invite that address, and to
sign in once with it. Editing and deleting stay in Acme.

## Which Acme environment uses which app

A Slack app has one set of request URLs, so each deployed environment has
its own app.

| Acme environment | Slack app | Manifest | Client id in | Secrets |
|-------------------|-----------|----------|--------------|---------|
| `local` | none: the twin | none | none | none |
| `staging` | `A0C3MMXH2AH`, "Acme (staging)" | `deployment/slack/manifest.staging.json` | `deployment/terraform/environments/staging/main.tf`, `slack_client_id` | `acme/staging/slack_client_secret`, `acme/staging/slack_signing_secret` |
| `production` (parked) | made when production opens, "Acme" | `deployment/slack/manifest.production.json` | `deployment/terraform/environments/prod/main.tf`, `slack_client_id` | `acme/production/slack_client_secret`, `acme/production/slack_signing_secret` |

Each org's bot token is a secret of its own, which the application
writes: `acme/<env>/app/org/<org id>/slack_bot_<installation id>`. Nobody
writes those by hand.

## First-time setup, by hand

Do these in order. Slack checks the events URL when the manifest is
saved, and the API answers that check only once it holds the signing
secret, so the secrets come before the manifest.

### 1. Merge, and let staging deploy

The deploy makes the two secrets, each holding `off`, runs the migration,
and rolls the API and the worker. A secret in AWS cannot be empty, so
Acme uses the plain word `off` as a secret's value to mean "not set".
With `off`, **Add to Slack** and every
call from Slack answer `503 slack_unavailable`, and the API's log names
`slack=off` at start.

**Check**, under your own sign-in:

```bash
aws secretsmanager describe-secret --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/slack_signing_secret --query '{name: Name, changed: LastChangedDate}'
```

`ResourceNotFoundException` means the deploy has not run yet. Wait for
it. A value written before then makes a secret Terraform does not own,
and the next deploy fails on it.

### 2. Let the tasks write an org's token

An install writes the workspace's token into the org's own secret. The
ceiling on what a task may do is the account's task boundary, the policy
`acme-task-boundary-staging`. The bootstrap root holds it, and no deploy
can change it. Apply that one policy, as the account's administrator:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sso login --profile acme-staging-admin
export AWS_PROFILE=acme-staging-admin
terraform -chdir=deployment/terraform/bootstrap/staging init -input=false -reconfigure \
  -backend-config=bucket=acme-state-111111111111 \
  -backend-config=key=bootstrap/terraform.tfstate \
  -backend-config=region=us-west-2 -backend-config=use_lockfile=true
terraform -chdir=deployment/terraform/bootstrap/staging apply \
  -target=module.account.aws_iam_policy.task_boundary \
  -var owner_email=<the owner's address>
unset AWS_PROFILE
```

`-target` keeps the apply to the one policy, so nothing else in the root
moves.

**Check.** The plan shows `module.account.aws_iam_policy.task_boundary`
updated in place, and nothing else: the statement `ItsTenantsSecrets`
(create, put, and delete under `acme/staging/app/org/`) is added.

### 3. Write the two secrets, and commit the client id

Open [api.slack.com/apps/A0C3MMXH2AH](https://api.slack.com/apps/A0C3MMXH2AH)
→ **Basic Information** → **App Credentials**. Three values are there:
**Client ID**, **Client Secret** (click **Show**), and **Signing Secret**
(click **Show**).

Keys exported in the shell outrank a profile, so clear them first.
`read -rs` takes each value without showing it:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws sso login --profile acme-staging
read -rs VALUE    # the Signing Secret
aws secretsmanager put-secret-value --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/slack_signing_secret --secret-string "$VALUE"
read -rs VALUE    # the Client Secret
aws secretsmanager put-secret-value --profile acme-staging --region us-west-2 \
  --secret-id acme/staging/slack_client_secret --secret-string "$VALUE"
unset VALUE
```

The **Client ID** is not a secret. It goes in a pull request, in
`deployment/terraform/environments/staging/main.tf`:

```hcl
  slack_client_id = "<the Client ID>"
```

Its merge deploys staging, and the new tasks start with the two secrets
too. To pick up a secret without a deploy, roll the two services:

```bash
aws ecs update-service --profile acme-staging --region us-west-2 \
  --cluster acme-staging --service api --force-new-deployment
aws ecs update-service --profile acme-staging --region us-west-2 \
  --cluster acme-staging --service maintenance --force-new-deployment
```

**Check.** `/acme/staging/api` names `slack=web` in its start line.
Until all three values are in, it names `slack=off`.

### 4. Paste the manifest

Still at [api.slack.com/apps/A0C3MMXH2AH](https://api.slack.com/apps/A0C3MMXH2AH):

1. **App Manifest** (left menu, under Features). Pick the **JSON** tab.
2. Select everything there, and paste the whole of
   `deployment/slack/manifest.staging.json`.
3. **Save Changes**. Slack lists what changes: the name becomes "Acme
   (staging)", the sample command and shortcut go, `/acme` arrives, the
   scopes and events change, Socket Mode turns off, token rotation turns
   on. Confirm.
4. Slack sends the events URL a challenge. When staging answers it, the
   page says the URL is **Verified**. If it says the URL did not respond,
   the signing secret is not in the running API yet: finish step 3, then
   open **Event Subscriptions** and click **Retry**.

Token rotation cannot be turned off once it is on. That is the intent.

Org-wide deployment (`org_deploy_enabled`) cannot be turned off either,
and the staging app has it on. So `manifest.staging.json` says `true`:
a manifest that says `false` is refused on save. The production app is
made new, so its manifest says `false`, and it stays that way.

Slack then shows a banner: "You've changed the permission scopes your
app uses. Please reinstall your app via the OAuth flow". Acme's
**Add to Slack** (step 6) is that OAuth flow. Nothing else reinstalls
it, and the old workspace-level install from before is not used.

**Check.** **Event Subscriptions** shows the request URL
`https://api.staging.acme.example/webhooks/slack/events` with a green
**Verified**. **Slash Commands** shows `/acme` with
`https://api.staging.acme.example/webhooks/slack/commands`. **OAuth &
Permissions** shows the redirect URL
`https://api.staging.acme.example/webhooks/slack/oauth` and the seven bot
scopes. **Event Subscriptions** lists the five bot events. **Socket
Mode** is off.

### 5. Let any workspace install it

**Basic Information** → **Manage Distribution**. Three of the four rows
under **Share Your App with Other Workspaces** are green on their own.
The fourth, **Remove Hard Coded Information**, stays grey until you open
it and tick its checkbox: it asks you to confirm the app keeps no token
in code, which is true. Until all four are green, **Activate Public
Distribution** is disabled. Then click it.
Without this, only the app's own workspace can install it. Public
distribution does not list the app in the Slack Marketplace; that is a
separate submission, and Acme does not make it.

### 6. Install from Acme, and bind a channel

1. Sign in to [app.staging.acme.example](https://app.staging.acme.example) as
   an owner or an admin of an org, and open **Settings**. The **Slack**
   card says "Not installed."
2. Click **Add to Slack**. Slack's page asks to install "Acme (staging)"
   in a workspace, with the seven permissions. Pick the workspace and click
   **Allow**.
3. Slack sends the browser back to the settings page, which says "Acme
   is in Slack." The card says "Installed in <workspace>. No channel gets
   posts yet."
4. In Slack, in the channel for reminders and task updates, type
   `/invite @acme`, then `/acme connect`. Acme posts a first line
   there, and the card says it is posting to that channel.

**Check.** In Slack, `/acme help` answers with the usage, `/acme add
Buy milk` answers "Added", and the task is in Acme, made by you. Give a
task yesterday's date as its due date: its morning has passed everywhere,
so the reminder arrives in the channel within a minute. If `/acme` says
you are not a member, the email on your Slack profile is not the address
you sign in to Acme with.

## What is automated, and by what

| What | Done by | When |
|------|---------|------|
| The two secret containers, holding `off` | Terraform, `deployment/terraform/modules/secrets` | The first deploy that carries them |
| The `slack` queue and its dead-letter queue | Terraform, `deployment/terraform/modules/environment` | Every deploy |
| The client id, the redirect URL, and the portal URL, inside the processes | The deploy: each task's environment | Every task start |
| The two secrets inside the processes | The deploy: each task gets them at start | Every task start |
| The tasks' right to write an org's token | The account module's task boundary (the ceiling, by a person's bootstrap apply) and the secrets module's policy (the grant, every deploy) | Once; every deploy |
| An org's installation and its token | The API, on the install's redirect | Each install |
| The token's renewal | Whichever process uses the token, one hour before it expires | Every twelve hours, on use |
| The app's configuration | Nothing: a person pastes the manifest | Once, and after a change to the manifest |

The manifest in the repository is the record. A change to the app is a
pull request to the manifest, then a paste. A change of scopes also asks
every installed workspace to approve again: an owner or an admin clicks
**Add to Slack again** in Acme.

## Local development

A laptop has no public URL, so Slack itself never calls it, and Acme
adds no tunnel. Locally Slack is the twin: `.env.example` sets
`ACME_SLACK_BACKEND=twin`.

- **Add to Slack** in the local portal installs at once into the twin's
  own workspace, `TTWIN0001` ("Twin Workspace"), with no Slack page in
  between. The settings card shows it installed.
- The worker posts to the twin, which logs each post (`slack twin posted
  twin.000001 to C…`).
- Slack's calls in are exercised by the tests, which sign each request
  with the twin's signing secret exactly as Slack signs, and by a signed
  request to the local API. The twin's secret is `TWIN_SIGNING_SECRET` in
  `integrations/src/acme/integrations/slack/twin.py`; nothing outside a
  laptop or a test accepts it. A signed `/acme help`:

```bash
uv run python - <<'EOF'
import urllib.request
from urllib.parse import urlencode
from acme.integrations.slack.requests import sign
from acme.integrations.slack.twin import TWIN_SIGNING_SECRET

body = urlencode({"command": "/acme", "text": "help", "team_id": "TTWIN0001",
                  "channel_id": "C0LOCAL", "user_id": "U0LOCAL", "trigger_id": "local.1",
                  "response_url": "https://hooks.slack.com/commands/local"}).encode()
at, signature = sign(body, TWIN_SIGNING_SECRET)
request = urllib.request.Request("http://127.0.0.1:8000/webhooks/slack/commands", data=body,
                                 headers={"X-Slack-Request-Timestamp": at, "X-Slack-Signature": signature})
print(urllib.request.urlopen(request).status)   # 200; the worker's log shows the answer
EOF
```

To try the real Slack, use staging. Never point a Slack app's URLs at a
laptop.

## Rotation

**The signing secret.** On **Basic Information** → **App Credentials**,
click **Regenerate** beside the Signing Secret. From that moment Slack
signs with the new one, so write it at once (step 3) and roll `api`.
Calls in the minutes between are refused with `401`; Slack retries an
event, and a person types a command again.

**The client secret.** **Regenerate** beside the Client Secret, write it
(step 3), roll `api` and `maintenance`. Until they roll, an install and a
token's renewal fail. A renewal is tried again on the next use, and the
token in hand works for its last hour.

**The client id** never changes for an app.

**An org's bot token** renews itself every twelve hours, and each
refresh token works once. Nothing is done by hand. To end one org's
token at once, the org clicks **Remove from Slack** in Acme, or removes
the app in Slack; either deletes it.

**A change of scopes or events.** Change the manifest in a pull request,
merge, and paste it (step 4). Each org then clicks **Add to Slack again**
to approve the new scopes; its channel stays.

## When it breaks

| What you see | Why | Where to look |
|--------------|-----|---------------|
| **Add to Slack** says the Slack app's credentials are not configured (`503`) | One of the three values is missing: a secret is still `off`, or `slack_client_id` is empty | `/acme/<env>/api` names `slack=off` at start. Step 3 |
| `/acme` answers `dispatch_failed`, or nothing | The API refused or did not answer: the signing secret is wrong or `off`, or the API is down | `/acme/<env>/api`: `slack_signature_invalid` (`401`) or `slack_unavailable` (`503`) on `/webhooks/slack/commands` |
| Event Subscriptions will not verify the URL | The same, for the events URL | As above, on `/webhooks/slack/events`. Step 3, then **Retry** |
| The settings page says the install link expired or was already used | The state works once and for ten minutes | Click **Add to Slack** again |
| It says the workspace is installed for another Acme org | One workspace belongs to one org | That org clicks **Remove from Slack**, or pick another workspace |
| It says Slack refused the install | The code exchange failed: a wrong client secret, or a redirect URL the app does not list | `/acme/<env>/api`: `slack install failed at Slack: <code>`. Step 3; the manifest's redirect URL |
| It says Slack refused the install, after about 20 seconds | Slack or Secrets Manager did not answer by the request's deadline (`ACME_REQUEST_DEADLINE_SECONDS`, ADR 0069) | `/acme/<env>/api`: `slack install failed at Slack: deadline`, or `slack install could not keep its token: ... the request's deadline passed`. Slack's status page, or the account's Secrets Manager; then **Add to Slack** again |
| An install fails with `AccessDeniedException` on `CreateSecret` in the log | The account's task boundary lacks the tenant-secret writes | Step 2 |
| `/acme` says Acme is not installed for this Slack workspace | No org installed the app in that workspace, or it was removed | Install from Acme's settings |
| `/acme` says you are not a member | Your Slack profile's email is not an address a member of the org signed in with | Ask an owner or an admin to invite that address, then sign in once |
| `/acme connect` says `@acme` is not in the channel | The bot was never invited there | `/invite @acme`, then `/acme connect` again |
| The card says `@acme` is not in the channel, or that it was removed from it | Someone removed the bot from the channel Acme posts to | `/invite @acme` in that channel. Slack sends `member_joined_channel`, and the card is well again within seconds |
| `/invite @acme` does not mend the card | The workspace approved Acme before it asked for `channels:read` and `groups:read`, so Slack does not send the join; or the manifest lacks the event | **Add to Slack again** approves the two scopes. `/acme connect` in the channel mends it meanwhile. The App Manifest page for the event |
| The card says the channel was archived, or deleted | Slack refused the channel for good | `/acme connect` in a working channel |
| The card says Slack no longer accepts the install's token | A renewal was refused: the app was removed from the workspace, or the token revoked | **Add to Slack again** |
| Reminders do not arrive, and nothing fails | No channel is bound, or the installation is broken; the worker drops the post and says why | `/acme/<env>/maintenance`: `slack post <id> dropped: …` |
| Posts are late | Slack rate limited the bot. The worker parks the post for as long as Slack asked and tries again | `/acme/<env>/maintenance`, and the worker's outcome counter |
| A command is received but never answered | The worker failed on it; after its retries it lands in `acme-<env>-slack-dead` | `/acme/<env>/maintenance`, and the queue's depth |
| `@acme` does not answer | The manifest's `app_mention` event or `app_mentions:read` scope is missing, or the org has not approved the new scope | The App Manifest page; **Add to Slack again** |

## When an environment is torn down

`scripts/cloud_nuke.sh` deletes the environment's two Slack secrets with
everything else, and the installations go with the database. The Slack
app stays, with its URLs pointing at an API that no longer answers:
`/acme` fails in every workspace that installed it until a new staging
answers. The workspaces keep the app installed until someone removes it
in Slack. Each org's bot token stays in Secrets Manager under
`acme/<env>/app/org/`: the application wrote it, so Terraform does not
own it, and the nuke only counts them. If the environment is not coming
back, delete them:

```bash
aws secretsmanager list-secrets --profile acme-staging-admin --region us-west-2 \
  --filters Key=name,Values=acme/staging/app/org/ --query 'SecretList[].Name' --output text \
  | tr '\t' '\n' | while read -r name; do
      aws secretsmanager delete-secret --profile acme-staging-admin --region us-west-2 \
        --secret-id "$name" --force-delete-without-recovery
    done
```
 A recreated staging needs steps 1 to 3 again (the client id is
already committed), and each org installs again from its settings. The
manifest needs no paste unless it changed.

## Production (parked)

Production is not running yet. When it opens:

1. Make its app: [api.slack.com/apps](https://api.slack.com/apps) →
   **Create New App** → **From a manifest**. Pick the workspace that will
   own the app, pick **JSON**, paste
   `deployment/slack/manifest.production.json`, and create. Slack checks
   the events URL at once; it fails until step 3, which is expected.
2. Steps 1 and 2 above, for production, under `acme-prod-power` and only
   when that change is explicitly authorized. `acme-prod` only reads.
3. Step 3 with production's names: `acme/production/slack_signing_secret`,
   `acme/production/slack_client_secret`, and `slack_client_id` in
   `deployment/terraform/environments/prod/main.tf`. Then **Event
   Subscriptions** → **Retry**, and the URL turns verified.
4. Step 5, then install from production's settings.

Never put a secret or a token in the repository, a chat, a ticket, or a
log. The names are enough everywhere.
